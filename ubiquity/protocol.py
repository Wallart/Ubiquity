"""
Binary message protocol for TCP file sync.

Each message: [1 byte type][2 bytes payload length][payload bytes]
"""
import hashlib
import json
import struct
from typing import Tuple

CHUNK_PAYLOAD_SIZE = 32768  # 32KB — stays under asyncio's 64KB StreamReader buffer

MSG_ANNOUNCE     = 0x01  # start of file transfer
MSG_CHUNK        = 0x02  # file data chunk
MSG_END          = 0x03  # transfer complete
MSG_MOVE         = 0x05  # file renamed/moved
MSG_DELETE       = 0x06  # file deleted
MSG_REQUEST      = 0x07  # client requests a file from server
MSG_CLIPBOARD    = 0x08  # clipboard text sync
MSG_SCREEN_START = 0x09  # start of a screen frame (JPEG, chunked)
MSG_SCREEN_CHUNK = 0x0A  # screen frame data chunk
MSG_SCREEN_CTRL  = 0x0B  # screen-share control: ask peer to start/stop sharing


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


# 2-byte length field caps payload at 65535; leave headroom for UTF-8 multibyte chars.
_CLIPBOARD_MAX_BYTES = 60000


def encode_clipboard(text: str) -> bytes:
    payload = text.encode('utf-8', errors='replace')[:_CLIPBOARD_MAX_BYTES]
    return _pack(MSG_CLIPBOARD, payload)


def decode_clipboard(data: bytes) -> str:
    _, payload = _unpack(data)
    return payload.decode('utf-8', errors='replace')


# ---- Screen share (view-only MJPEG) --------------------------------------- #
# A frame is one JPEG, split into MSG_SCREEN_CHUNK messages exactly like a
# file: MSG_SCREEN_START announces frame_id + total_chunks, then the chunks
# follow. Frames are droppable — no checksum, no retransmission.

def encode_screen_start(frame_id: int, size: int, total_chunks: int) -> bytes:
    payload = json.dumps({
        'frame_id': frame_id, 'size': size, 'total_chunks': total_chunks,
    }).encode()
    return _pack(MSG_SCREEN_START, payload)


def decode_screen_start(data: bytes) -> Tuple[int, int, int]:
    _, payload = _unpack(data)
    obj = json.loads(payload)
    return obj['frame_id'], obj['size'], obj['total_chunks']


def encode_screen_chunk(frame_id: int, chunk_idx: int, data: bytes) -> bytes:
    header = struct.pack('>HH', frame_id, chunk_idx)
    return _pack(MSG_SCREEN_CHUNK, header + data)


def decode_screen_chunk(data: bytes) -> Tuple[int, int, bytes]:
    _, payload = _unpack(data)
    frame_id, chunk_idx = struct.unpack('>HH', payload[:4])
    return frame_id, chunk_idx, payload[4:]


def encode_screen_ctrl(action: str) -> bytes:
    return _pack(MSG_SCREEN_CTRL, action.encode())


def decode_screen_ctrl(data: bytes) -> str:
    _, payload = _unpack(data)
    return payload.decode()
