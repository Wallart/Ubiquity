"""
Shared config loading and sync exclusion filter.

Config file: ~/.ubiquity/config.json
Exclusion patterns use fnmatch syntax:
  - Simple name:  .DS_Store, Thumbs.db
  - Glob (name):  *.tmp, ~$*
  - Path glob:    build/*, .git/*
"""
import fnmatch
import json
import os
from pathlib import Path

CONFIG_PATH = Path.home() / '.ubiquity' / 'config.json'

DEFAULTS: dict = {
    'mode':      'client',
    'peer':      '',
    'port':      5000,
    'watch_dir': str(Path.home() / 'Ubiquity'),
    'exclude': [
        '.DS_Store',
        'Thumbs.db',
        'desktop.ini',
        '*.tmp',
        '~$*',
    ],
}


def load() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULTS, **json.loads(CONFIG_PATH.read_text())}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


class SyncFilter:
    """Returns True for paths that should be excluded from sync."""

    def __init__(self, patterns: list[str]):
        self._patterns = patterns

    def is_excluded(self, rel_path: str) -> bool:
        name = Path(rel_path).name
        for pattern in self._patterns:
            if '/' in pattern or os.sep in pattern:
                if fnmatch.fnmatch(rel_path, pattern):
                    return True
            else:
                if fnmatch.fnmatch(name, pattern):
                    return True
        return False
