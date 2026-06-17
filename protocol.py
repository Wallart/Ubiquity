"""
Binary message protocol for TCP file sync.

Each message: [1 byte type][2 bytes payload length][payload bytes]
"""
import hashlib
import json
import struct
from typing import Tuple

CHUNK_PAYLOAD_SIZE = 32768  # 32KB — stays under asyncio's 64KB StreamReader buffer

MSG_ANNOUNCE = 0x01  # start of file transfer
MSG_CHUNK    = 0x02  # file data chunk
MSG_END      = 0x03  # transfer complete
MSG_MOVE     = 0x05  # file renamed/moved
MSG_DELETE   = 0x06  # file deleted
MSG_REQUEST  = 0x07  # client requests a file from server


def file_checksum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def _pack(msg_type: int, payload: bytes) -> bytes:
    return bytes([msg_type]) + struct.pack('>H', len(payload)) + payload


def _unpack(data: bytes) -> Tuple[int, bytes]:
    msg_type = data[0]
    length = struct.unpack('>H', data[1:3])[0]
    return msg_type, data[3:3 + length]


def encode_announce(path: str, size: int, inode: int, mtime: float, checksum: str) -> bytes:
    total_chunks = max(1, (size + CHUNK_PAYLOAD_SIZE - 1) // CHUNK_PAYLOAD_SIZE) if size > 0 else 0
    payload = json.dumps({
        'path': path, 'size': size, 'inode': inode,
        'mtime': mtime, 'checksum': checksum, 'total_chunks': total_chunks,
    }).encode()
    return _pack(MSG_ANNOUNCE, payload)


def decode_announce(data: bytes) -> dict:
    _, payload = _unpack(data)
    return json.loads(payload)


def encode_chunk(chunk_idx: int, data: bytes) -> bytes:
    header = struct.pack('>H', chunk_idx)
    return _pack(MSG_CHUNK, header + data)


def decode_chunk(data: bytes) -> Tuple[int, bytes]:
    _, payload = _unpack(data)
    chunk_idx = struct.unpack('>H', payload[:2])[0]
    return chunk_idx, payload[2:]


def encode_end(checksum: str) -> bytes:
    return _pack(MSG_END, checksum.encode())


def decode_end(data: bytes) -> str:
    _, payload = _unpack(data)
    return payload.decode()


def encode_move(old_path: str, new_path: str) -> bytes:
    payload = json.dumps({'old': old_path, 'new': new_path}).encode()
    return _pack(MSG_MOVE, payload)


def decode_move(data: bytes) -> Tuple[str, str]:
    _, payload = _unpack(data)
    obj = json.loads(payload)
    return obj['old'], obj['new']


def encode_delete(path: str) -> bytes:
    return _pack(MSG_DELETE, path.encode())


def decode_delete(data: bytes) -> str:
    _, payload = _unpack(data)
    return payload.decode()


def encode_request(path: str) -> bytes:
    return _pack(MSG_REQUEST, path.encode())


def decode_request(data: bytes) -> str:
    _, payload = _unpack(data)
    return payload.decode()
