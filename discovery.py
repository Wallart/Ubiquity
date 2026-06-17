"""
UDP discovery — client-initiated to avoid Windows firewall issues.

Client broadcasts "who's there?" -> server replies unicast -> client connects.
Only outbound UDP is needed on the client (always allowed by Windows Firewall).
"""
import asyncio
import json
import logging
import socket
import threading

log = logging.getLogger(__name__)

DISCOVERY_PORT = 5999
BROADCAST_INTERVAL = 2.0
MAGIC_DISCOVER = 'ubiquity-discover-v1'
MAGIC_ANNOUNCE = 'ubiquity-announce-v1'


def _broadcast_addresses() -> list:
    addrs = ['255.255.255.255']
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('127.'):
                parts = ip.split('.')
                parts[3] = '255'
                bcast = '.'.join(parts)
                if bcast not in addrs:
                    addrs.append(bcast)
    except Exception:
        pass
    return addrs


class DiscoveryServer:
    """Listens for discovery broadcasts in a background thread and replies unicast."""

    def __init__(self, tcp_port: int):
        self._tcp_port = tcp_port
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._listen, daemon=True, name='discovery')
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _listen(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.0)
            sock.bind(('', DISCOVERY_PORT))
        except Exception:
            log.exception('Discovery thread failed to start')
            return
        reply = json.dumps({'magic': MAGIC_ANNOUNCE, 'port': self._tcp_port}).encode()
        log.info(f'Discovery listening on UDP port {DISCOVERY_PORT}')
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(1024)
                msg = json.loads(data)
                if msg.get('magic') == MAGIC_DISCOVER:
                    sock.sendto(reply, addr)
                    log.info(f'Discovery reply sent to {addr}')
            except socket.timeout:
                continue
            except (json.JSONDecodeError, KeyError):
                continue
            except Exception:
                log.exception('Discovery recv error')
        sock.close()


class DiscoveryClient:
    async def find(self) -> tuple:
        log.info(f'Broadcasting discovery on UDP port {DISCOVERY_PORT} (waiting for server)...')
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._find_blocking)

    def _find_blocking(self) -> tuple:
        payload = json.dumps({'magic': MAGIC_DISCOVER}).encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(BROADCAST_INTERVAL)
        sock.bind(('', 0))
        try:
            while True:
                for addr in _broadcast_addresses():
                    try:
                        sock.sendto(payload, (addr, DISCOVERY_PORT))
                    except Exception as e:
                        log.debug(f'Broadcast to {addr} error: {e}')
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = json.loads(data)
                    if msg.get('magic') == MAGIC_ANNOUNCE:
                        host, port = addr[0], msg['port']
                        log.info(f'Found server at {host}:{port}')
                        return host, port
                except socket.timeout:
                    continue
                except (json.JSONDecodeError, KeyError):
                    continue
        finally:
            sock.close()
