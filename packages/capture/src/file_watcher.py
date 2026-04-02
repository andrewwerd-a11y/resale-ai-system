"""
IntakeWatcher — monitors a folder for new photos using watchdog.
Calls a callback for each new image file detected.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    logger.warning("watchdog not installed — IntakeWatcher unavailable. Run: uv sync")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


class IntakeWatcher:
    def __init__(self, watch_path: Path, callback: Callable[[Path], None]):
        """
        Watch watch_path for new image files.
        Calls callback(photo_path) for each new image.
        """
        self.watch_path = watch_path
        self.callback = callback
        self._observer = None

    def start(self) -> None:
        """Start watching in a background thread."""
        if not WATCHDOG_AVAILABLE:
            logger.warning("Cannot start watcher — watchdog not installed")
            return

        if self._observer and self._observer.is_alive():
            logger.debug("Watcher already running")
            return

        callback = self.callback

        class _Handler(FileSystemEventHandler):
            def on_created(self, event: FileCreatedEvent) -> None:
                if event.is_directory:
                    return
                p = Path(event.src_path)
                if p.suffix.lower() in IMAGE_EXTS:
                    logger.info("New image detected: %s", p.name)
                    try:
                        callback(p)
                    except Exception as e:
                        logger.error("Watcher callback error for %s: %s", p.name, e)

        self.watch_path.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._observer.schedule(_Handler(), str(self.watch_path), recursive=False)
        self._observer.start()
        logger.info("IntakeWatcher started on %s", self.watch_path)

    def stop(self) -> None:
        """Stop the background watcher thread."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
            logger.info("IntakeWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
