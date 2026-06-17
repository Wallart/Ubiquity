"""
UDP broadcast discovery.
Server announces itself periodically; client listens and auto-connects.
"""
import asyncio
import json
import logging
import socket

log = logging.getLogger(__name__)

DISCOVERY_PORT = 5999
BROADCAST_INTERVAL = 2.0
DISCOVERY_MAGIC = 'ubiquity-sync-v1'


class _BroadcastProtocol(asyncio.DatagramProtocol):
    def __init__(self, payload: bytes):
        self._payload = payload
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def send(self):
        try:
            self._transport.sendto(self._payload, ('255.255.255.255', DISCOVERY_PORT))
        except Exception as e:
            log.debug(f'Broadcast send error: {e}')

    def error_received(self, exc):
        log.debug(f'Broadcast protocol error: {exc}')

    def connection_lost(self, exc):
        pass


class _ListenerProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    def datagram_received(self, data: bytes, addr):
        self.queue.put_nowait((data, addr))

    def error_received(self, exc):
        log.debug(f'Listener protocol error: {exc}')

    def connection_lost(self, exc):
        pass


class DiscoveryServer:
    def __init__(self, tcp_port: int):
        self._tcp_port = tcp_port
        self._task = None

    def start(self):
        self._task = asyncio.ensure_future(self._broadcast_loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _broadcast_loop(self):
        payload = json.dumps({'magic': DISCOVERY_MAGIC, 'port': self._tcp_port}).encode()
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: _BroadcastProtocol(payload),
            family=socket.AF_INET,
            allow_broadcast=True,
        )
        log.info(f'Broadcasting presence on UDP port {DISCOVERY_PORT}')
        try:
            while True:
                protocol.send()
                await asyncio.sleep(BROADCAST_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            transport.close()


class DiscoveryClient:
    async def find(self, timeout: float = 30.0) -> tuple:
        log.info(f'Looking for Ubiquity server on local network (UDP {DISCOVERY_PORT})...')
        loop = asyncio.get_running_loop()
        protocol = _ListenerProtocol()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', DISCOVERY_PORT))
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            sock=sock,
        )
        try:
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(f'No Ubiquity server found after {timeout}s')
                try:
                    data, addr = await asyncio.wait_for(
                        protocol.queue.get(),
                        timeout=min(remaining, 1.0),
                    )
                    msg = json.loads(data)
                    if msg.get('magic') == DISCOVERY_MAGIC:
                        host, port = addr[0], msg['port']
                        log.info(f'Found server at {host}:{port}')
                        return host, port
                except (asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                    continue
        finally:
            transport.close()
