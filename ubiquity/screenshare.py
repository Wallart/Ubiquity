"""
Screen-share helpers (view-only MJPEG).

Capture: mss grabs a monitor, Pillow encodes it to JPEG. One JPEG = one frame.
Frames are streamed over the existing TCP connection, chunked by the protocol,
and reassembled by FrameReassembler on the viewing peer.

Frames are intentionally lossy/droppable: if a new frame starts before the
previous one finished, the old partial frame is discarded.
"""
import io
import logging
import sys
from typing import Callable, Optional

log = logging.getLogger(__name__)


def grab_jpeg(monitor: int, quality: int) -> Optional[bytes]:
    """Capture *monitor* and return it as JPEG bytes (None on failure).

    Runs in a worker thread (mss/PIL are blocking). A fresh mss instance is
    created each call — mss instances are not thread-safe to share, and the
    per-call cost is negligible at typical frame rates.
    """
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            mons = sct.monitors
            idx = monitor if 0 <= monitor < len(mons) else 1
            shot = sct.grab(mons[idx])
        img = Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        return buf.getvalue()
    except Exception:
        log.exception('Screen capture failed')
        return None


def ensure_permission() -> bool:
    """Best-effort macOS Screen Recording (TCC) permission check.

    Returns True if capture is allowed (or the check is unavailable / not macOS).
    On first denial it triggers the system prompt; macOS usually requires an app
    restart after granting, so we log guidance for the user.
    """
    if sys.platform != 'darwin':
        return True
    try:
        from Quartz import (
            CGPreflightScreenCaptureAccess,
            CGRequestScreenCaptureAccess,
        )
    except Exception:
        return True  # older macOS / Quartz missing — let the capture try anyway
    if CGPreflightScreenCaptureAccess():
        return True
    CGRequestScreenCaptureAccess()
    log.warning(
        'Autorisation « Enregistrement de l’écran » requise. '
        'Activez Ubiquity dans Réglages Système → Confidentialité et sécurité '
        '→ Enregistrement de l’écran, puis relancez l’application.'
    )
    return False


class FrameReassembler:
    """Reassembles MSG_SCREEN_* chunks into complete JPEG frames.

    on_frame(jpeg_bytes) is called once per fully-received frame.
    """

    def __init__(self, on_frame: Callable[[bytes], None]):
        self._on_frame = on_frame
        self._frame_id: Optional[int] = None
        self._total = 0
        self._chunks: dict[int, bytes] = {}

    def start(self, frame_id: int, size: int, total_chunks: int):
        # A new frame supersedes any partially-received previous one.
        self._frame_id = frame_id
        self._total = total_chunks
        self._chunks = {}

    def chunk(self, frame_id: int, chunk_idx: int, data: bytes):
        if frame_id != self._frame_id:
            return  # stale chunk from a superseded frame
        self._chunks[chunk_idx] = data
        if len(self._chunks) >= self._total:
            jpeg = b''.join(self._chunks[i] for i in sorted(self._chunks))
            self._frame_id = None
            self._chunks = {}
            self._on_frame(jpeg)
