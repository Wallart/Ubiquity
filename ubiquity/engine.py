"""
Sync engine: ties together the file watcher and TCP transport.

Conflict resolution: last-write-wins based on mtime.
Echo prevention: files written locally are ignored by the watcher for 1 second.
"""
import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# tqdm lazily creates a multiprocessing.RLock for its progress bars.  Inside a
# PyInstaller bundle sys.executable IS the app, so spawning multiprocessing's
# resource_tracker re-executes the whole bundle — launching a second tray icon
# and relaunching the app on quit.  Force a plain threading lock instead: we
# never use multiprocessing, so this removes the only path that touches it.
tqdm.set_lock(threading.RLock())

from ubiquity import protocol, screenshare
from ubiquity.clipboard import ClipboardMonitor
from ubiquity.discovery import DiscoveryServer
from ubiquity.transport import TCPClient, TCPServer
from ubiquity.watcher import FileWatcher

log = logging.getLogger(__name__)


class _ReceiveState:
    """Accumulates chunks for a single in-progress incoming file."""

    def __init__(self, meta: dict):
        self.meta = meta
        self.chunks: dict[int, bytes] = {}
        self.pbar = (
            tqdm(total=meta['size'], unit='B', unit_scale=True,
                 desc=f'↓ {meta["path"]}', leave=True)
            if meta['size'] > 0 else None
        )

    def add_chunk(self, idx: int, data: bytes):
        self.chunks[idx] = data
        if self.pbar:
            self.pbar.update(len(data))

    def close(self):
        if self.pbar:
            self.pbar.close()

    def is_complete(self) -> bool:
        return len(self.chunks) == self.meta['total_chunks']

    def assemble(self) -> bytes:
        return b''.join(self.chunks[i] for i in sorted(self.chunks))


class SyncEngine:
    def __init__(self, watch_dir: str, mode: str, peer_name: str = None, port: int = 5000,
                 on_status=None, on_transfer=None, sync_filter=None,
                 on_frame=None, on_share_ended=None,
                 screen_fps: int = 8, screen_quality: int = 50,
                 screen_monitor: int = 1):
        self._watch_dir = Path(watch_dir).resolve()
        self._mode = mode
        self._peer_name = peer_name
        self._port = port
        self._discovery = None
        self._fs_queue: asyncio.Queue = asyncio.Queue()
        self._recv_state: Optional[_ReceiveState] = None
        self._transport = None
        self._send_lock = asyncio.Lock()
        self._local_writes: set[str] = set()
        self._filter = sync_filter
        self._clipboard = ClipboardMonitor(self._on_clipboard_change)
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Screen share (view-only MJPEG).
        self._screen_fps = screen_fps
        self._screen_quality = screen_quality
        self._screen_monitor = screen_monitor
        self._capture_task: Optional[asyncio.Task] = None
        self._reassembler = screenshare.FrameReassembler(self._on_frame_received)
        # GUI callbacks — called from the asyncio thread, must be thread-safe.
        self._on_status = on_status or (lambda status, peer='': None)
        self._on_transfer = on_transfer or (lambda name, pct, done=False: None)
        self._on_frame = on_frame or (lambda jpeg: None)
        self._on_share_ended = on_share_ended or (lambda: None)

    def request_stop(self):
        """Thread-safe: signal the engine to stop gracefully."""
        if self._stop_event and self._loop:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    async def _run_until_stopped(self, coro):
        """Run coro concurrently with the stop signal; cancel the other when one finishes."""
        task = asyncio.ensure_future(coro)
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        done, pending = await asyncio.wait(
            {task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    async def run(self):
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._stop_event = asyncio.Event()
        is_excl = self._filter.is_excluded if self._filter else None
        watcher = FileWatcher(str(self._watch_dir), loop, self._fs_queue, is_excluded=is_excl)

        watcher.start()
        log.info(f'Sync engine running in {self._mode} mode for {self._watch_dir}')

        clipboard_task = asyncio.ensure_future(self._clipboard.run())
        try:
            if self._mode == 'server':
                self._transport = TCPServer(
                    self._on_receive, self._port,
                    on_connect=self._on_client_connected,
                    on_disconnect=lambda: self._on_status('searching'),
                )
                await self._transport.start()
                self._discovery = DiscoveryServer(self._port)
                self._discovery.start()
                self._on_status('searching')
                try:
                    await self._run_until_stopped(self._process_fs_events())
                finally:
                    self._discovery.stop()
                    await self._transport.stop()
                    watcher.stop()
            else:
                self._transport = TCPClient(self._on_receive)
                try:
                    await self._run_until_stopped(self._client_loop())
                finally:
                    await self._transport.disconnect()
                    watcher.stop()
        finally:
            self._stop_capture()
            clipboard_task.cancel()
            try:
                await clipboard_task
            except asyncio.CancelledError:
                pass

    async def _on_client_connected(self):
        addr = self._transport.peer_addr()
        self._on_status('connected', addr)
        await self._send_all_files()

    async def _client_loop(self):
        from ubiquity.discovery import DiscoveryClient
        use_discovery = self._peer_name is None
        while True:
            self._on_status('searching')
            if use_discovery:
                host, port = await DiscoveryClient().find()
            else:
                host, port = self._peer_name, self._port
            try:
                await self._transport.connect(host, port)
            except OSError as e:
                log.warning(f'Connection failed ({e}), retrying in 5s...')
                await asyncio.sleep(5.0)
                continue

            self._on_status('connected', host)
            fs_task = asyncio.ensure_future(self._process_fs_events())
            try:
                await self._transport.wait_disconnected()
            finally:
                fs_task.cancel()
                try:
                    await fs_task
                except asyncio.CancelledError:
                    pass
            log.info('Server lost — restarting discovery...')

    # ------------------------------------------------------------------ #
    # Outbound: local changes → TCP                                        #
    # ------------------------------------------------------------------ #

    async def _send_all_files(self):
        log.info('Client connected — sending initial file list')
        for abs_path in sorted(self._watch_dir.rglob('*')):
            if abs_path.is_file():
                rel = str(abs_path.relative_to(self._watch_dir))
                if self._filter and self._filter.is_excluded(rel):
                    log.debug(f'Skipping excluded file: {rel}')
                    continue
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
                        await self._send_request(event[1])
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

            if stat.st_size > 0:
                sent = 0
                with open(abs_path, 'rb') as f:
                    with tqdm(total=stat.st_size, unit='B', unit_scale=True,
                              desc=f'↑ {rel_path}', leave=True) as pbar:
                        idx = 0
                        while chunk := f.read(protocol.CHUNK_PAYLOAD_SIZE):
                            await self._transport.send(protocol.encode_chunk(idx, chunk))
                            sent += len(chunk)
                            pbar.update(len(chunk))
                            self._on_transfer(rel_path, int(sent * 100 / stat.st_size))
                            idx += 1
                            await asyncio.sleep(0)

            await self._transport.send(protocol.encode_end(checksum))
            self._on_transfer(rel_path, 100, done=True)
            log.info(f'Sent {rel_path}')

    async def _send_move(self, old_path: str, new_path: str):
        async with self._send_lock:
            await self._transport.send(protocol.encode_move(old_path, new_path))
            log.info(f'Sent move: {old_path} → {new_path}')

    async def _send_request(self, rel_path: str):
        async with self._send_lock:
            await self._transport.send(protocol.encode_request(rel_path))
            log.info(f'Requested {rel_path} from server')

    async def _send_delete(self, rel_path: str):
        async with self._send_lock:
            await self._transport.send(protocol.encode_delete(rel_path))
            log.info(f'Sent delete: {rel_path}')

    async def _on_clipboard_change(self, text: str):
        if self._transport is None or not self._transport.connected:
            return
        async with self._send_lock:
            await self._transport.send(protocol.encode_clipboard(text))
        log.info(f'Sent clipboard ({len(text)} chars)')

    # ------------------------------------------------------------------ #
    # Screen share (view-only MJPEG)                                       #
    # ------------------------------------------------------------------ #
    # Public API below is thread-safe: it hops onto the engine's loop via
    # call_soon_threadsafe so the tray (a different thread) can drive it.

    def start_sharing(self):
        """Begin capturing and streaming this screen to the peer."""
        if self._loop:
            self._loop.call_soon_threadsafe(self._start_capture)

    def stop_sharing(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._stop_capture)

    def ask_peer_to_share(self, on: bool):
        """Tell the peer to start/stop sharing its screen with us."""
        self._schedule_ctrl('start' if on else 'stop')

    def _schedule_ctrl(self, action: str):
        if self._loop:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._send_ctrl(action))
            )

    async def _send_ctrl(self, action: str):
        if self._transport and self._transport.connected:
            await self._transport.send(protocol.encode_screen_ctrl(action))

    def _start_capture(self):
        if self._capture_task and not self._capture_task.done():
            return
        if not screenshare.ensure_permission():
            return  # permission prompt shown; user must grant + relaunch
        log.info('Screen sharing started')
        self._capture_task = asyncio.ensure_future(self._capture_loop())

    def _stop_capture(self):
        if self._capture_task:
            self._capture_task.cancel()
            self._capture_task = None
            log.info('Screen sharing stopped')
            # Tell the viewing peer we stopped, so it can close its window
            # instead of freezing on the last frame.
            asyncio.ensure_future(self._send_ctrl('ended'))

    async def _capture_loop(self):
        loop = asyncio.get_running_loop()
        interval = 1.0 / max(1, self._screen_fps)
        frame_id = 0
        try:
            while True:
                t0 = loop.time()
                if self._transport and self._transport.connected:
                    jpeg = await loop.run_in_executor(
                        None, screenshare.grab_jpeg,
                        self._screen_monitor, self._screen_quality,
                    )
                    if jpeg:
                        await self._send_frame(frame_id, jpeg)
                        frame_id = (frame_id + 1) & 0xFFFF
                # Hold the cadence, accounting for capture+send time.
                await asyncio.sleep(max(0.0, interval - (loop.time() - t0)))
        except asyncio.CancelledError:
            pass

    async def _send_frame(self, frame_id: int, jpeg: bytes):
        # Frame chunks are NOT wrapped in self._send_lock: each transport.send
        # is atomic on its own, so screen chunks interleave with file chunks and
        # neither blocks the other for long.
        size = len(jpeg)
        total = max(1, (size + protocol.CHUNK_PAYLOAD_SIZE - 1) // protocol.CHUNK_PAYLOAD_SIZE)
        await self._transport.send(protocol.encode_screen_start(frame_id, size, total))
        for idx in range(total):
            chunk = jpeg[idx * protocol.CHUNK_PAYLOAD_SIZE:(idx + 1) * protocol.CHUNK_PAYLOAD_SIZE]
            await self._transport.send(protocol.encode_screen_chunk(frame_id, idx, chunk))

    def _on_frame_received(self, jpeg: bytes):
        """Called by FrameReassembler on a complete inbound frame."""
        self._on_frame(jpeg)

    # ------------------------------------------------------------------ #
    # Inbound: TCP → local disk                                            #
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
                    state, self._recv_state = self._recv_state, None
                    state.close()
                    await self._finalise_receive(state)

        elif msg_type == protocol.MSG_END:
            if self._recv_state:
                state, self._recv_state = self._recv_state, None
                state.close()
                await self._finalise_receive(state)

        elif msg_type == protocol.MSG_MOVE:
            old_path, new_path = protocol.decode_move(data)
            await self._apply_move(old_path, new_path)

        elif msg_type == protocol.MSG_DELETE:
            rel_path = protocol.decode_delete(data)
            await self._apply_delete(rel_path)

        elif msg_type == protocol.MSG_REQUEST:
            rel_path = protocol.decode_request(data)
            await self._send_file(rel_path)

        elif msg_type == protocol.MSG_CLIPBOARD:
            text = protocol.decode_clipboard(data)
            self._clipboard.set(text)
            log.info(f'Received clipboard ({len(text)} chars)')

        elif msg_type == protocol.MSG_SCREEN_START:
            frame_id, size, total = protocol.decode_screen_start(data)
            self._reassembler.start(frame_id, size, total)

        elif msg_type == protocol.MSG_SCREEN_CHUNK:
            frame_id, idx, chunk_data = protocol.decode_screen_chunk(data)
            self._reassembler.chunk(frame_id, idx, chunk_data)

        elif msg_type == protocol.MSG_SCREEN_CTRL:
            action = protocol.decode_screen_ctrl(data)
            log.info(f'Screen-share control from peer: {action}')
            if action == 'start':
                self._start_capture()
            elif action == 'stop':
                self._stop_capture()
            elif action == 'ended':
                # The peer we were viewing stopped sharing → close our viewer.
                self._on_share_ended()

    async def _finalise_receive(self, state: '_ReceiveState'):
        meta = state.meta
        rel_path = meta['path']
        abs_path = self._watch_dir / rel_path

        # Last-write-wins: skip if our local copy is newer.
        if abs_path.exists():
            local_mtime = abs_path.stat().st_mtime
            if local_mtime > meta['mtime']:
                log.info(f'Skipping {rel_path}: local file is newer')
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
