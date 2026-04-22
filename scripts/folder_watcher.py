"""
Folder watcher — monitors _incoming/ for new photo files and routes them
to the correct item folder via the photos API.

File naming convention: {SKU}_{sequence}.jpg
Example: CL-001_01.jpg → routes to item CL-001

Usage:
    python scripts/folder_watcher.py [--incoming ./intake/_incoming]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watchdog.events import FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer

from packages.core.src.config import get_settings
from packages.data.src.db.sqlite import get_session, init_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.core.src.constants import (
    ReviewSubQueue, MINIMUM_PHOTOS_PER_CATEGORY, MINIMUM_PHOTOS_DEFAULT,
)

# Pattern: SKU prefix _ sequence number . extension
# e.g. CL-001_01.jpg or BK-042_03.png
_FILENAME_RE = re.compile(
    r"^(?P<sku>[A-Z]{2,3}-\d+)_(?P<seq>\d+)(?P<ext>\.[a-zA-Z]+)$",
    re.IGNORECASE,
)

log_path = get_settings().log_dir / "watcher.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_path, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _route_file(src: Path) -> None:
    """Parse item ID from filename, add photo to correct item folder."""
    m = _FILENAME_RE.match(src.name)
    if not m:
        logger.warning("Skipping unrecognized filename: %s", src.name)
        return

    sku = m.group("sku").upper()
    ext = m.group("ext").lower()

    settings = get_settings()
    init_db()

    with next(get_session()) as session:  # type: ignore[call-overload]
        repo = ItemRepository(session)
        item = repo.get_by_sku(sku)
        if not item:
            logger.error("Item not found for SKU %s — file: %s", sku, src.name)
            return

        category_prefix = (item.category_key or "misc").lower()
        if item.photo_folder:
            folder = Path(item.photo_folder)
        else:
            folder = settings.intake_root / "Inventory_Photos" / category_prefix / sku
        folder.mkdir(parents=True, exist_ok=True)

        existing = sorted(
            p for p in folder.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}
        )
        next_num = len(existing) + 1
        dest = folder / f"{next_num:02d}{ext}"
        src.rename(dest)

        all_paths = [str(p) for p in existing] + [str(dest)]
        item.image_paths = all_paths
        item.photo_folder = str(folder)

        min_photos = MINIMUM_PHOTOS_PER_CATEGORY.get(
            item.category_key or "", MINIMUM_PHOTOS_DEFAULT
        )
        if (
            item.review_sub_queue == ReviewSubQueue.PHOTO_BLOCKED
            and len(all_paths) >= min_photos
        ):
            item.review_sub_queue = ReviewSubQueue.ENRICHABLE
            reasons = [r for r in (item.review_reasons or []) if r != "insufficient_photos"]
            item.review_reasons = reasons
            item.review_reason = reasons[0] if reasons else None
            logger.info("SKU %s: photo threshold met, moved to enrichable", sku)

        from datetime import datetime
        item.updated_at = datetime.utcnow()
        repo.upsert(item)
        logger.info(
            "SKU %s: added photo %s (%d/%d photos)",
            sku, dest.name, len(all_paths), min_photos,
        )


class _PhotoHandler(FileSystemEventHandler):
    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            return
        # Small delay to ensure file write is complete
        time.sleep(0.5)
        try:
            _route_file(path)
        except Exception as exc:
            logger.error("Error routing %s: %s", path.name, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch _incoming/ folder for new photos")
    parser.add_argument(
        "--incoming",
        default=str(get_settings().intake_root / "_incoming"),
        help="Path to the incoming folder to watch",
    )
    args = parser.parse_args()

    watch_dir = Path(args.incoming)
    watch_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Watching %s for new photos...", watch_dir)
    handler = _PhotoHandler()
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    logger.info("Watcher stopped.")


if __name__ == "__main__":
    main()
