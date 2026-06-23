"""
File system watcher with inode-based move detection.
Uses watchdog for cross-platform OS-native events (FSEvents/inotify/ReadDirectoryChangesW).
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Optional

from watchdog.events import (FileCreatedEvent, FileDeletedEvent,
                              FileModifiedEvent, FileMovedEvent,
                              FileSystemEventHandler)
from watchdog.observers import Observer

log = logging.getLogger(__name__)


class InodeTracker:
    """Maps inodes → relative paths to distinguish renames from new files."""

    def __init__(self, watch_dir: Path):
        self._watch_dir = watch_dir
        self._inode_to_path: Dict[int, str] = {}
        self._scan()

    def _scan(self):
        for p in self._watch_dir.rglob('*'):
            if p.is_file():
                try:
                    self._inode_to_path[p.stat().st_ino] = str(p.relative_to(self._watch_dir))
                except OSError:
                    pass

    def get_by_inode(self, inode: int) -> Optional[str]:
        return self._inode_to_path.get(inode)

    def update(self, rel_path: str, inode: int):
        stale = [i for i, p in self._inode_to_path.items() if p == rel_path]
        for i in stale:
            del self._inode_to_path[i]
        self._inode_to_path[inode] = rel_path

    def remove(self, rel_path: str):
        stale = [i for i, p in self._inode_to_path.items() if p == rel_path]
        for i in stale:
            del self._inode_to_path[i]


class _EventHandler(FileSystemEventHandler):
    def __init__(self, watch_dir: Path, tracker: InodeTracker,
                 loop: asyncio.AbstractEventLoop, queue: asyncio.Queue,
                 is_excluded=None):
        self._watch_dir = watch_dir
        self._tracker = tracker
        self._loop = loop
        self._queue = queue
        self._is_excluded = is_excluded or (lambda _: False)

    def _rel(self, abs_path: str) -> str:
        return str(Path(abs_path).relative_to(self._watch_dir))

    def _emit(self, event: tuple):
        asyncio.run_coroutine_threadsafe(self._queue.put(event), self._loop)

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return
        try:
            stat = os.stat(event.src_path)
            rel = self._rel(event.src_path)
            if self._is_excluded(rel):
                return
            self._tracker.update(rel, stat.st_ino)
            self._emit(('modified', rel))
        except OSError:
            pass

    def on_modified(self, event: FileModifiedEvent):
        if event.is_directory:
            return
        try:
            stat = os.stat(event.src_path)
            rel = self._rel(event.src_path)
            if self._is_excluded(rel):
                return
            self._tracker.update(rel, stat.st_ino)
            self._emit(('modified', rel))
        except OSError:
            pass

    def on_moved(self, event: FileMovedEvent):
        if event.is_directory:
            return
        old_rel = self._rel(event.src_path)
        new_rel = self._rel(event.dest_path)
        self._tracker.remove(old_rel)
        if self._is_excluded(new_rel):
            return
        try:
            stat = os.stat(event.dest_path)
            self._tracker.update(new_rel, stat.st_ino)
        except OSError:
            pass
        self._emit(('moved', old_rel, new_rel))

    def on_deleted(self, event: FileDeletedEvent):
        if event.is_directory:
            return
        rel = self._rel(event.src_path)
        self._tracker.remove(rel)
        if self._is_excluded(rel):
            return
        self._emit(('deleted', rel))


class FileWatcher:
    def __init__(self, watch_dir: str, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue,
                 is_excluded=None):
        self._watch_dir = Path(watch_dir)
        self._tracker = InodeTracker(self._watch_dir)
        self._handler = _EventHandler(self._watch_dir, self._tracker, loop, queue, is_excluded)
        self._observer = Observer()

    def start(self):
        self._observer.schedule(self._handler, str(self._watch_dir), recursive=True)
        self._observer.start()
        log.info(f'Watching {self._watch_dir}')

    def stop(self):
        self._observer.stop()
        self._observer.join()
