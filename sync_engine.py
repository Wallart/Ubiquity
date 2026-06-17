"""
Sync engine: ties together the file watcher and BLE transport.

Conflict resolution: last-write-wins based on mtime.
Echo prevention: files written locally are ignored by the watcher for 1 second.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import protocol
from tcp_transport import TCPClient, TCPServer
from watcher import FileWatcher

log = logging.getLogger(__name__)

BLE_DEVICE_NAME = 'UbiquitySync'
# Delay between BLE packets to avoid overwhelming the receiver (seconds).
INTER_PACKET_DELAY = 0.02


class _ReceiveState:
    """Accumulates chunks for a single in-progress incoming file."""

    def __init__(self, meta: dict):
        self.meta = meta
        self.chunks: dict[int, bytes] = {}

    def add_chunk(self, idx: int, data: bytes):
        self.chunks[idx] = data

    def is_complete(self) -> bool:
        return len(self.chunks) == self.meta['total_chunks']

    def assemble(self) -> bytes:
        return b''.join(self.chunks[i] for i in sorted(self.chunks))


class SyncEngine:
    def __init__(self, watch_dir: str, mode: str, peer_name: str = '127.0.0.1', port: int = 5000):
        self._watch_dir = Path(watch_dir).resolve()
        self._mode = mode
        self._peer_name = peer_name
        self._port = port
        self._fs_queue: asyncio.Queue = asyncio.Queue()
        self._recv_state: Optional[_ReceiveState] = None
        self._transport = None
        self._send_lock = asyncio.Lock()
        # Relative paths of files we wrote ourselves — suppressed in watcher.
        self._local_writes: set[str] = set()

    async def run(self):
        loop = asyncio.get_running_loop()
        self._loop = loop
        watcher = FileWatcher(str(self._watch_dir), loop, self._fs_queue)

        if self._mode == 'server':
            self._transport = TCPServer(self._on_receive, self._port, on_connect=self._send_all_files)
            await self._transport.start()
        else:
            self._transport = TCPClient(self._peer_name, self._port, self._on_receive)
            await self._transport.connect()

        watcher.start()
        log.info(f'Sync engine running in {self._mode} mode for {self._watch_dir}')

        try:
            await self._process_fs_events()
        finally:
            watcher.stop()
            if self._mode == 'server':
                await self._transport.stop()
            else:
                await self._transport.disconnect()

    # ------------------------------------------------------------------ #
    # Outbound: local changes → BLE                                        #
    # ------------------------------------------------------------------ #

    async def _send_all_files(self):
        log.info('Client connected — sending initial file list')
        for abs_path in sorted(self._watch_dir.rglob('*')):
            if abs_path.is_file():
                rel = str(abs_path.relative_to(self._watch_dir))
                await self._send_file(rel)

    async def _process_fs_events(self):
        while True:
            event = await self._fs_queue.get()
            kind = event[0]
            try:
                if kind == 'modified':
                    rel_path = event[1]
                    if rel_path not in self._local_writes:
                        await self._send_file(rel_path)
                elif kind == 'moved':
                    if self._mode != 'client':
                        await self._send_move(event[1], event[2])
                elif kind == 'deleted':
                    if self._mode == 'client':
                        pass  # client deletions never propagate — server is source of truth
                    else:
                        rel_path = event[1]
                        # Debounce: editors often delete+rename atomically (atomic write).
                        await asyncio.sleep(0.3)
                        if (self._watch_dir / rel_path).is_file():
                            await self._send_file(rel_path)
                        else:
                            await self._send_delete(rel_path)
            except Exception:
                log.exception(f'Error handling FS event {event}')

    async def _send_file(self, rel_path: str):
        abs_path = self._watch_dir / rel_path
        if not abs_path.is_file():
            return

        try:
            stat = abs_path.stat()
            checksum = protocol.file_checksum(str(abs_path))
        except OSError:
            return

        async with self._send_lock:
            log.info(f'Sending {rel_path} ({stat.st_size} bytes)')

            await self._transport.send(
                protocol.encode_announce(rel_path, stat.st_size, stat.st_ino, stat.st_mtime, checksum)
            )
            await asyncio.sleep(INTER_PACKET_DELAY)

            if stat.st_size > 0:
                with open(abs_path, 'rb') as f:
                    idx = 0
                    while chunk := f.read(protocol.CHUNK_PAYLOAD_SIZE):
                        await self._transport.send(protocol.encode_chunk(idx, chunk))
                        await asyncio.sleep(INTER_PACKET_DELAY)
                        idx += 1

            await self._transport.send(protocol.encode_end(checksum))
            log.info(f'Sent {rel_path}')

    async def _send_move(self, old_path: str, new_path: str):
        async with self._send_lock:
            await self._transport.send(protocol.encode_move(old_path, new_path))
            log.info(f'Sent move: {old_path} → {new_path}')

    async def _send_delete(self, rel_path: str):
        async with self._send_lock:
            await self._transport.send(protocol.encode_delete(rel_path))
            log.info(f'Sent delete: {rel_path}')

    # ------------------------------------------------------------------ #
    # Inbound: BLE → local disk                                            #
    # ------------------------------------------------------------------ #

    def _on_receive(self, data: bytes):
        asyncio.ensure_future(self._handle_message(data))

    async def _handle_message(self, data: bytes):
        if not data:
            return
        msg_type = data[0]

        if msg_type == protocol.MSG_ANNOUNCE:
            meta = protocol.decode_announce(data)
            self._recv_state = _ReceiveState(meta)
            log.info(f'Incoming: {meta["path"]} ({meta["size"]} bytes, {meta["total_chunks"]} chunks)')

        elif msg_type == protocol.MSG_CHUNK:
            if self._recv_state:
                idx, chunk_data = protocol.decode_chunk(data)
                self._recv_state.add_chunk(idx, chunk_data)
                if self._recv_state.is_complete():
                    await self._finalise_receive()
                    self._recv_state = None

        elif msg_type == protocol.MSG_END:
            # Safety net: finalise even if chunk counter diverged.
            if self._recv_state:
                await self._finalise_receive()
                self._recv_state = None

        elif msg_type == protocol.MSG_MOVE:
            old_path, new_path = protocol.decode_move(data)
            await self._apply_move(old_path, new_path)

        elif msg_type == protocol.MSG_DELETE:
            rel_path = protocol.decode_delete(data)
            await self._apply_delete(rel_path)

    async def _finalise_receive(self):
        state = self._recv_state
        meta = state.meta
        rel_path = meta['path']
        abs_path = self._watch_dir / rel_path

        # Last-write-wins: skip if our local copy is newer.
        if abs_path.exists():
            local_mtime = abs_path.stat().st_mtime
            if local_mtime > meta['mtime']:
                log.info(f'Skipping {rel_path}: local file is newer ({local_mtime:.1f} > {meta["mtime"]:.1f})')
                return

        content = state.assemble()

        # Verify checksum.
        import hashlib
        actual = hashlib.sha256(content).hexdigest()
        if actual != meta['checksum']:
            log.error(f'Checksum mismatch for {rel_path}: expected {meta["checksum"]}, got {actual}')
            return

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        self._local_writes.add(rel_path)
        try:
            abs_path.write_bytes(content)
            os.utime(abs_path, (meta['mtime'], meta['mtime']))
            log.info(f'Saved {rel_path}')
        except OSError:
            log.exception(f'Failed to write {rel_path}')
        finally:
            await asyncio.sleep(1.0)  # watcher debounce window
            self._local_writes.discard(rel_path)

    async def _apply_move(self, old_path: str, new_path: str):
        old_abs = self._watch_dir / old_path
        new_abs = self._watch_dir / new_path
        if not old_abs.exists():
            return
        new_abs.parent.mkdir(parents=True, exist_ok=True)
        self._local_writes.add(new_path)
        try:
            old_abs.rename(new_abs)
            log.info(f'Moved {old_path} → {new_path}')
        except OSError:
            log.exception(f'Failed to move {old_path}')
        finally:
            await asyncio.sleep(1.0)
            self._local_writes.discard(new_path)

    async def _apply_delete(self, rel_path: str):
        abs_path = self._watch_dir / rel_path
        if abs_path.exists():
            try:
                abs_path.unlink()
                log.info(f'Deleted {rel_path}')
            except OSError:
                log.exception(f'Failed to delete {rel_path}')
