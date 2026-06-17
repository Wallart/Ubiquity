"""
Run this on the SERVER (macOS) while the Windows client is running.
Stop the Ubiquity server first to free port 5999.

Usage: python3 test_scripts/test_udp_receive.py
"""
import json
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
sock.bind(('', 5999))
print('Listening on UDP 5999... (Ctrl+C to stop)')
while True:
    data, addr = sock.recvfrom(1024)
    try:
        print(f'Received from {addr}: {json.loads(data)}')
    except json.JSONDecodeError:
        print(f'Received from {addr}: {data!r}')
