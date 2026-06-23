"""
Ubiquity — system tray application.

The engine runs in a background asyncio thread; the tray lives on the main thread.
"""
import asyncio
import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import pystray
from PIL import Image, ImageDraw

from ubiquity.config import CONFIG_PATH, SyncFilter, load as load_config, save as save_config
from ubiquity.engine import SyncEngine

log = logging.getLogger(__name__)

try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False


# ------------------------------------------------------------------ #
# Logging                                                              #
# ------------------------------------------------------------------ #

def setup_logging():
    log_path = Path.home() / '.ubiquity' / 'ubiquity.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(name)s  %(message)s', datefmt='%H:%M:%S'
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
               for h in root.handlers):
        root.addHandler(logging.StreamHandler())


# ------------------------------------------------------------------ #
# Icon drawing                                                         #
# ------------------------------------------------------------------ #

_PALETTE = {
    'grey':   (120, 120, 120),
    'red':    (239,  68,  68),
    'orange': (249, 115,  22),
    'green':  ( 34, 197,  94),
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
        self._cfg = load_config()

        self._status = 'stopped'
        self._peer_addr = ''
        self._transfers: dict[str, int] = {}
        self._last_error = ''

        self._engine: Optional[SyncEngine] = None
        self._engine_thread: Optional[threading.Thread] = None
        self._engine_loop: Optional[asyncio.AbstractEventLoop] = None

        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()

        self._icon = pystray.Icon(
            'Ubiquity',
            icon=_make_icon('grey'),
            title='Ubiquity',
            menu=pystray.Menu(self._build_menu),
        )

    def run(self):
        threading.Thread(target=self._ui_pump, daemon=True, name='ui-pump').start()
        self._start_engine()
        self._icon.run()

    # ------------------------------------------------------------------ #
    # Menu factory                                                         #
    # ------------------------------------------------------------------ #

    def _build_menu(self):
        running = self._status != 'stopped'

        status_text = {
            'stopped':   'Arrêté',
            'searching': 'Recherche du pair…',
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
            pystray.MenuItem('Démarrer',  self._action_start, enabled=not running),
            pystray.MenuItem('Arrêter',   self._action_stop,  enabled=running),
            pystray.Menu.SEPARATOR,
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
            pystray.MenuItem('Ouvrir le dossier', self._action_open_folder),
            pystray.MenuItem('Paramètres…',        self._action_settings),
            pystray.MenuItem('Voir les logs',      self._action_open_logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Quitter', self._action_quit),
        ]

        return tuple(items)

    # ------------------------------------------------------------------ #
    # Menu callbacks                                                       #
    # ------------------------------------------------------------------ #

    def _action_set_server(self, icon, item):
        self._cfg['mode'] = 'server'
        save_config(self._cfg)
        icon.update_menu()

    def _action_set_client(self, icon, item):
        self._cfg['mode'] = 'client'
        save_config(self._cfg)
        icon.update_menu()

    def _action_start(self, icon, item):
        self._start_engine()

    def _action_stop(self, icon, item):
        self._stop_engine()

    def _action_quit(self, icon, item):
        self._stop_engine()
        icon.stop()

    def _action_open_folder(self, icon, item):
        folder = self._cfg['watch_dir']
        if sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        elif sys.platform == 'win32':
            subprocess.Popen(['explorer', folder])

    def _action_open_logs(self, icon, item):
        log_path = Path.home() / '.ubiquity' / 'ubiquity.log'
        if not log_path.exists():
            return
        if sys.platform == 'darwin':
            subprocess.Popen(['open', str(log_path)])
        elif sys.platform == 'win32':
            os.startfile(str(log_path))

    def _action_settings(self, icon, item):
        threading.Thread(target=self._settings_dialog, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Engine management                                                    #
    # ------------------------------------------------------------------ #

    def _start_engine(self):
        if self._engine_thread and self._engine_thread.is_alive():
            return
        Path(self._cfg['watch_dir']).mkdir(parents=True, exist_ok=True)
        self._status = 'searching'
        self._ui_queue.put(('status', 'searching', ''))
        self._engine_thread = threading.Thread(
            target=self._run_engine, daemon=True, name='ubiquity-engine',
        )
        self._engine_thread.start()

    def _stop_engine(self):
        engine = self._engine
        if engine:
            engine.request_stop()

    def _run_engine(self):
        loop = asyncio.new_event_loop()
        self._engine_loop = loop
        asyncio.set_event_loop(loop)

        self._engine = SyncEngine(
            watch_dir=self._cfg['watch_dir'],
            mode=self._cfg['mode'],
            peer_name=self._cfg.get('peer') or None,
            port=int(self._cfg.get('port', 5000)),
            on_status=self._on_engine_status,
            on_transfer=self._on_engine_transfer,
            sync_filter=SyncFilter(self._cfg.get('exclude', [])),
        )
        try:
            loop.run_until_complete(self._engine.run())
        except Exception as e:
            log.exception('Engine crashed')
            self._ui_queue.put(('error', str(e), ''))
        finally:
            loop.close()
            self._engine = None
            self._engine_loop = None
            self._ui_queue.put(('status', 'stopped', ''))

    # ------------------------------------------------------------------ #
    # Engine callbacks → UI queue                                          #
    # ------------------------------------------------------------------ #

    def _on_engine_status(self, status: str, peer: str = ''):
        self._ui_queue.put(('status', status, peer))

    def _on_engine_transfer(self, name: str, pct: int, done: bool = False):
        self._ui_queue.put(('transfer', name, pct, done))

    # ------------------------------------------------------------------ #
    # UI pump — drains queue, updates pystray from a daemon thread         #
    # ------------------------------------------------------------------ #

    def _ui_pump(self):
        while True:
            try:
                msg = self._ui_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind in ('status', 'error'):
                if kind == 'error':
                    self._status     = 'error'
                    self._last_error = msg[1][:60]
                    self._peer_addr  = ''
                else:
                    self._status    = msg[1]
                    self._peer_addr = msg[2]
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
        if self._status == 'stopped':
            color = 'grey'
        elif self._status in ('searching', 'error'):
            color = 'red'
        elif self._transfers:
            color = 'orange'
        else:
            color = 'green'
        progress = None
        if self._transfers:
            progress = int(sum(self._transfers.values()) / len(self._transfers))
        try:
            self._icon.icon = _make_icon(color, progress)
            self._icon.update_menu()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Settings dialog                                                      #
    # ------------------------------------------------------------------ #

    def _settings_dialog(self):
        if not HAS_TKINTER:
            save_config(self._cfg)
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

        tk.Label(win, text='Fichiers exclus :').grid(row=3, column=0, sticky='nw', **pad)
        excl_text = tk.Text(win, width=38, height=4)
        excl_text.insert('1.0', '\n'.join(self._cfg.get('exclude', [])))
        excl_text.grid(row=3, column=1, columnspan=2, **pad)
        tk.Label(win, text='Un pattern par ligne\n(.DS_Store, *.tmp, build/*)').grid(
            row=4, column=1, sticky='w', padx=12)

        running = self._status != 'stopped'
        if running:
            tk.Label(win, text='⚠ Les changements prennent effet au prochain démarrage.',
                     fg='orange').grid(row=5, column=0, columnspan=3, pady=(0, 4))

        def save():
            self._cfg['watch_dir'] = dir_var.get()
            self._cfg['peer']      = peer_var.get().strip()
            try:
                self._cfg['port'] = int(port_var.get())
            except ValueError:
                pass
            raw = excl_text.get('1.0', 'end').strip()
            self._cfg['exclude'] = [l.strip() for l in raw.splitlines() if l.strip()]
            save_config(self._cfg)
            win.destroy()

        tk.Button(win, text='Enregistrer', command=save, width=14).grid(
            row=6, column=1, sticky='e', pady=(6, 12))
        tk.Button(win, text='Annuler', command=win.destroy, width=10).grid(
            row=6, column=2, sticky='w', padx=(0, 12), pady=(6, 12))

        win.mainloop()
