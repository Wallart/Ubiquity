"""
Ubiquity system tray application.
Wraps the sync engine in a background asyncio thread.

Dependencies: pystray, Pillow  (pip install pystray pillow)
"""
import asyncio
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Optional

import pystray
from PIL import Image, ImageDraw

from sync_engine import SyncEngine

log = logging.getLogger(__name__)

try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

# ------------------------------------------------------------------ #
# Config                                                               #
# ------------------------------------------------------------------ #

CONFIG_PATH = Path.home() / '.ubiquity' / 'config.json'

DEFAULTS = {
    'mode':      'client',
    'peer':      '',
    'port':      5000,
    'watch_dir': str(Path.home() / 'Ubiquity'),
}


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(CONFIG_PATH.read_text())}
        except Exception:
            pass
    return dict(DEFAULTS)


def _save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


# ------------------------------------------------------------------ #
# Icon drawing                                                         #
# ------------------------------------------------------------------ #

_PALETTE = {
    'grey':   (120, 120, 120),
    'yellow': (234, 179,   8),
    'green':  ( 34, 197,  94),
    'red':    (239,  68,  68),
}


def _make_icon(color: str, progress: Optional[int] = None) -> Image.Image:
    size = 64
    margin = 6
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = _PALETTE.get(color, _PALETTE['grey'])
    box = [margin, margin, size - margin, size - margin]
    draw.ellipse(box, fill=c + (60,))
    if progress is not None:
        draw.arc(box, start=-90, end=-90 + int(3.6 * progress),
                 fill=c + (255,), width=7)
    else:
        inner = [margin + 10, margin + 10, size - margin - 10, size - margin - 10]
        draw.ellipse(inner, fill=c + (255,))
    return img


# ------------------------------------------------------------------ #
# Tray application                                                     #
# ------------------------------------------------------------------ #

class TrayApp:
    def __init__(self):
        self._cfg = _load_config()

        # State — written from asyncio thread, read on main thread.
        # Individual assignments are GIL-atomic in CPython.
        self._status = 'stopped'   # 'stopped' | 'searching' | 'connected' | 'error'
        self._peer_addr = ''
        self._transfers: dict[str, int] = {}
        self._last_error = ''

        self._engine_thread: Optional[threading.Thread] = None
        self._engine_loop: Optional[asyncio.AbstractEventLoop] = None

        # Queue used to push icon/menu updates from the asyncio thread
        # back to pystray via a periodic detached timer.
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()

        self._icon = pystray.Icon(
            'Ubiquity',
            icon=_make_icon('grey'),
            title='Ubiquity',
            menu=pystray.Menu(self._build_menu),
        )

    def run(self):
        # Drain the UI queue every 250 ms via a background daemon thread
        # so pystray (main thread) always gets updates from the engine.
        threading.Thread(target=self._ui_pump, daemon=True, name='ui-pump').start()
        self._icon.run()

    # ------------------------------------------------------------------ #
    # Menu factory — called by pystray each time the menu opens           #
    # ------------------------------------------------------------------ #

    def _build_menu(self):
        running = self._status != 'stopped'

        status_text = {
            'stopped':   'Arrêté',
            'searching': 'Recherche en cours…',
            'connected': f'Connecté  —  {self._peer_addr}',
            'error':     f'Erreur  —  {self._last_error}',
        }.get(self._status, self._status)

        items = [
            pystray.MenuItem(status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]

        for name, pct in list(self._transfers.items()):
            short = name if len(name) <= 32 else '…' + name[-30:]
            items.append(pystray.MenuItem(f'↑  {short}  {pct}%', None, enabled=False))
        if self._transfers:
            items.append(pystray.Menu.SEPARATOR)

        items += [
            pystray.MenuItem(
                'Mode Serveur',
                self._action_set_server,
                checked=lambda _: self._cfg['mode'] == 'server',
                radio=True,
                enabled=not running,
            ),
            pystray.MenuItem(
                'Mode Client',
                self._action_set_client,
                checked=lambda _: self._cfg['mode'] == 'client',
                radio=True,
                enabled=not running,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Paramètres…', self._action_settings, enabled=not running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Arrêter',   self._action_stop,  enabled=running),
            pystray.MenuItem('Démarrer',  self._action_start, enabled=not running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Quitter', self._action_quit),
        ]

        # pystray expects a tuple from a menu factory
        return tuple(items)

    # ------------------------------------------------------------------ #
    # Menu callbacks — called on the pystray / main thread                #
    # ------------------------------------------------------------------ #

    def _action_set_server(self, icon, item):
        self._cfg['mode'] = 'server'
        _save_config(self._cfg)
        icon.update_menu()

    def _action_set_client(self, icon, item):
        self._cfg['mode'] = 'client'
        _save_config(self._cfg)
        icon.update_menu()

    def _action_settings(self, icon, item):
        threading.Thread(target=self._settings_dialog, daemon=True).start()

    def _action_start(self, icon, item):
        if self._engine_thread and self._engine_thread.is_alive():
            return
        Path(self._cfg['watch_dir']).mkdir(parents=True, exist_ok=True)
        self._engine_thread = threading.Thread(
            target=self._run_engine, daemon=True, name='ubiquity-engine',
        )
        self._engine_thread.start()

    def _action_stop(self, icon, item):
        self._stop_engine()

    def _action_quit(self, icon, item):
        self._stop_engine()
        icon.stop()

    def _stop_engine(self):
        if self._engine_loop and self._engine_loop.is_running():
            self._engine_loop.call_soon_threadsafe(self._engine_loop.stop)

    # ------------------------------------------------------------------ #
    # Engine thread                                                        #
    # ------------------------------------------------------------------ #

    def _run_engine(self):
        loop = asyncio.new_event_loop()
        self._engine_loop = loop
        asyncio.set_event_loop(loop)

        engine = SyncEngine(
            watch_dir=self._cfg['watch_dir'],
            mode=self._cfg['mode'],
            peer_name=self._cfg.get('peer') or None,
            port=int(self._cfg.get('port', 5000)),
            on_status=self._on_engine_status,
            on_transfer=self._on_engine_transfer,
        )
        try:
            loop.run_until_complete(engine.run())
        except Exception as e:
            log.exception('Engine crashed')
            self._ui_queue.put(('error', str(e), ''))
        finally:
            loop.close()
            self._engine_loop = None
            self._ui_queue.put(('status', 'stopped', ''))

    # ------------------------------------------------------------------ #
    # Engine callbacks — called from asyncio thread, push to UI queue     #
    # ------------------------------------------------------------------ #

    def _on_engine_status(self, status: str, peer: str = ''):
        self._ui_queue.put(('status', status, peer))

    def _on_engine_transfer(self, name: str, pct: int, done: bool = False):
        self._ui_queue.put(('transfer', name, pct, done))

    # ------------------------------------------------------------------ #
    # UI pump — daemon thread, drains queue and updates pystray            #
    # ------------------------------------------------------------------ #

    def _ui_pump(self):
        while True:
            try:
                msg = self._ui_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind in ('status', 'error'):
                status = 'error' if kind == 'error' else msg[1]
                peer   = msg[2]
                if kind == 'error':
                    self._last_error = msg[1][:60]
                self._status    = status
                self._peer_addr = peer
                self._transfers.clear()
                self._apply_icon()
            elif kind == 'transfer':
                _, name, pct, done = msg
                if done:
                    self._transfers.pop(name, None)
                else:
                    self._transfers[name] = pct
                self._apply_icon()

    def _apply_icon(self):
        color = {
            'stopped':   'grey',
            'searching': 'yellow',
            'connected': 'green',
            'error':     'red',
        }.get(self._status, 'grey')
        progress = None
        if self._transfers:
            progress = int(sum(self._transfers.values()) / len(self._transfers))
        try:
            self._icon.icon = _make_icon(color, progress)
            self._icon.update_menu()
        except Exception:
            pass  # icon may not be ready yet on startup

    # ------------------------------------------------------------------ #
    # Settings dialog                                                      #
    # ------------------------------------------------------------------ #

    def _settings_dialog(self):
        if not HAS_TKINTER:
            import os, subprocess, sys
            _save_config(self._cfg)
            if sys.platform == 'darwin':
                subprocess.Popen(['open', str(CONFIG_PATH)])
            elif sys.platform == 'win32':
                os.startfile(str(CONFIG_PATH))
            return

        win = tk.Tk()
        win.title('Ubiquity — Paramètres')
        win.resizable(False, False)
        win.attributes('-topmost', True)
        pad = {'padx': 12, 'pady': 6}

        tk.Label(win, text='Dossier synchronisé :').grid(row=0, column=0, sticky='w', **pad)
        dir_var = tk.StringVar(value=self._cfg['watch_dir'])
        tk.Entry(win, textvariable=dir_var, width=38).grid(row=0, column=1, **pad)
        tk.Button(win, text='…', command=lambda: dir_var.set(
            filedialog.askdirectory(initialdir=dir_var.get(), parent=win) or dir_var.get()
        )).grid(row=0, column=2, padx=(0, 12))

        tk.Label(win, text='IP du serveur :').grid(row=1, column=0, sticky='w', **pad)
        peer_var = tk.StringVar(value=self._cfg.get('peer', ''))
        tk.Entry(win, textvariable=peer_var, width=38).grid(row=1, column=1, **pad)
        tk.Label(win, text='(vide = auto-découverte)').grid(
            row=1, column=2, sticky='w', padx=(0, 12))

        tk.Label(win, text='Port TCP :').grid(row=2, column=0, sticky='w', **pad)
        port_var = tk.StringVar(value=str(self._cfg.get('port', 5000)))
        tk.Entry(win, textvariable=port_var, width=10).grid(row=2, column=1, sticky='w', **pad)

        def save():
            self._cfg['watch_dir'] = dir_var.get()
            self._cfg['peer']      = peer_var.get().strip()
            try:
                self._cfg['port'] = int(port_var.get())
            except ValueError:
                pass
            _save_config(self._cfg)
            win.destroy()

        tk.Button(win, text='Enregistrer', command=save, width=14).grid(
            row=3, column=1, sticky='e', pady=(6, 12))
        tk.Button(win, text='Annuler', command=win.destroy, width=10).grid(
            row=3, column=2, sticky='w', padx=(0, 12), pady=(6, 12))

        win.mainloop()


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s  %(levelname)-8s  %(message)s')
    TrayApp().run()
