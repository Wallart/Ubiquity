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
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        try:
            while True:
                await loop.sock_sendto(sock, payload, ('255.255.255.255', DISCOVERY_PORT))
                await asyncio.sleep(BROADCAST_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            sock.close()


class DiscoveryClient:
    async def find(self, timeout: float = 30.0) -> tuple:
        log.info('Looking for Ubiquity server on local network...')
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', DISCOVERY_PORT))
        sock.setblocking(False)
        try:
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(f'No Ubiquity server found after {timeout}s')
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 1024),
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
            sock.close()
