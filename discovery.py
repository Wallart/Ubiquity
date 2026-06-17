"""
UDP discovery — client-initiated to avoid Windows firewall issues.

Client broadcasts "who's there?" → server replies unicast → client connects.
Only outbound UDP is needed on the client (always allowed by Windows Firewall).
"""
import asyncio
import json
import logging
import socket

log = logging.getLogger(__name__)

DISCOVERY_PORT = 5999
BROADCAST_INTERVAL = 2.0
MAGIC_DISCOVER = 'ubiquity-discover-v1'
MAGIC_ANNOUNCE = 'ubiquity-announce-v1'


def _broadcast_addresses() -> list:
    """Return all candidate broadcast addresses for the local machine."""
    addrs = ['255.255.255.255']
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('127.'):
                parts = ip.split('.')
                parts[3] = '255'
                subnet_bcast = '.'.join(parts)
                if subnet_bcast not in addrs:
                    addrs.append(subnet_bcast)
    except Exception:
        pass
    return addrs


class _ServerListenerProtocol(asyncio.DatagramProtocol):
    def __init__(self, tcp_port: int):
        self._tcp_port = tcp_port
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr):
        try:
            msg = json.loads(data)
            if msg.get('magic') == MAGIC_DISCOVER:
                response = json.dumps({'magic': MAGIC_ANNOUNCE, 'port': self._tcp_port}).encode()
                self._transport.sendto(response, addr)
                log.debug(f'Discovery reply sent to {addr}')
        except (json.JSONDecodeError, KeyError):
            pass

    def error_received(self, exc):
        log.debug(f'Discovery listener error: {exc}')

    def connection_lost(self, exc):
        pass


class _ClientProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr):
        self.queue.put_nowait((data, addr))

    def broadcast(self):
        payload = json.dumps({'magic': MAGIC_DISCOVER}).encode()
        for addr in _broadcast_addresses():
            try:
                self._transport.sendto(payload, (addr, DISCOVERY_PORT))
            except Exception as e:
                log.debug(f'Broadcast to {addr} error: {e}')

    def error_received(self, exc):
        log.debug(f'Discovery client error: {exc}')

    def connection_lost(self, exc):
        pass


class DiscoveryServer:
    def __init__(self, tcp_port: int):
        self._tcp_port = tcp_port
        self._task = None

    def start(self):
        self._task = asyncio.ensure_future(self._listen())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _listen(self):
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(('0.0.0.0', DISCOVERY_PORT))
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _ServerListenerProtocol(self._tcp_port),
            sock=sock,
        )
        log.info(f'Discovery listening on UDP port {DISCOVERY_PORT}')
        try:
            await asyncio.Future()  # run forever
        except asyncio.CancelledError:
            pass
        finally:
            transport.close()


class DiscoveryClient:
    async def find(self) -> tuple:
        log.info(f'Broadcasting discovery on UDP port {DISCOVERY_PORT} (waiting for server)...')
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        transport, protocol = await loop.create_datagram_endpoint(
            _ClientProtocol,
            sock=sock,
        )
        try:
            while True:
                protocol.broadcast()
                try:
                    data, addr = await asyncio.wait_for(
                        protocol.queue.get(),
                        timeout=BROADCAST_INTERVAL,
                    )
                    msg = json.loads(data)
                    if msg.get('magic') == MAGIC_ANNOUNCE:
                        host, port = addr[0], msg['port']
                        log.info(f'Found server at {host}:{port}')
                        return host, port
                except (asyncio.TimeoutError, json.JSONDecodeError, KeyError):
                    continue
        finally:
            transport.close()
