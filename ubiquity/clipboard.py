"""
Cross-platform clipboard monitor.

Polls the system clipboard every POLL_INTERVAL seconds and calls on_change(text)
when the content changes. Uses pyperclip for macOS (pbpaste/pbcopy) and Windows (ctypes).

Only text is synced; image or other non-text clipboard content is silently ignored.
"""
import asyncio
import logging
import shutil
import sys

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.5  # seconds

if sys.platform == 'linux' and not shutil.which('xclip') and not shutil.which('xsel'):
    log.warning(
        'Clipboard sync unavailable: install xclip or xsel '
        '(e.g. "sudo apt install xclip") to enable it on Linux.'
    )


def _get() -> str:
    try:
        import pyperclip
        return pyperclip.paste() or ''
    except Exception:
        return ''


def _set(text: str):
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception as e:
        log.warning(f'Failed to set clipboard: {e}')


class ClipboardMonitor:
    def __init__(self, on_change):
        self._on_change = on_change  # async callable(text: str)
        self._last: str | None = None

    def set(self, text: str):
        """Apply a received clipboard update without triggering a re-send."""
        _set(text)
        self._last = text

    async def run(self):
        self._last = _get()
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                current = _get()
            except Exception:
                continue
            if current == self._last or not current:
                continue
            self._last = current
            await self._on_change(current)
