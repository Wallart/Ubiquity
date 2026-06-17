"""
TCP transport layer — replaces BLE.

Server listens on TCP_PORT, client connects by hostname/IP.
Messages are framed with the existing 3-byte protocol header.
"""
import asyncio
import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)

TCP_PORT = 5000
OnReceive = Callable[[bytes], None]


class TCPServer:
    def __init__(self, on_receive: OnReceive, port: int = TCP_PORT):
        self._on_receive = on_receive
        self._port = port
        self._server = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._send_lock = asyncio.Lock()

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, '0.0.0.0', self._port
        )
        log.info(f'TCP server listening on 0.0.0.0:{self._port}')

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def send(self, data: bytes):
        if self._writer is None:
            return
        async with self._send_lock:
            self._writer.write(data)
            await self._writer.drain()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info('peername')
        log.info(f'Client connected from {addr}')
        self._writer = writer
        try:
            await self._read_loop(reader)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            log.warning(f'Client {addr} disconnected')
        finally:
            self._writer = None
            writer.close()

    async def _read_loop(self, reader: asyncio.StreamReader):
        while True:
            header = await reader.readexactly(3)
            length = (header[1] << 8) | header[2]
            payload = await reader.readexactly(length)
            self._on_receive(header + payload)


class TCPClient:
    def __init__(self, host: str, port: int = TCP_PORT, on_receive: OnReceive = None):
        self._host = host
        self._port = port
        self._on_receive = on_receive
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = asyncio.Event()
        self._send_lock = asyncio.Lock()

    async def connect(self):
        log.info(f'Connecting to {self._host}:{self._port}...')
        reader, writer = await asyncio.open_connection(self._host, self._port)
        self._writer = writer
        self._connected.set()
        log.info(f'Connected to {self._host}:{self._port}')
        asyncio.ensure_future(self._read_loop(reader))

    async def disconnect(self):
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass

    async def send(self, data: bytes):
        await self._connected.wait()
        async with self._send_lock:
            self._writer.write(data)
            await self._writer.drain()

    async def _read_loop(self, reader: asyncio.StreamReader):
        try:
            while True:
                header = await reader.readexactly(3)
                length = (header[1] << 8) | header[2]
                payload = await reader.readexactly(length)
                self._on_receive(header + payload)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            log.warning(f'Disconnected from {self._host}:{self._port}')
            self._connected.clear()
