from __future__ import annotations
import os
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}


def scan_item_folders(source_dir: str | Path) -> list[dict]:
    """
    Scan a source directory for item subfolders.
    Each subfolder is treated as one item.
    Returns list of dicts: {folder_name, folder_path, image_paths}
    """
    source = Path(source_dir)
    if not source.exists():
        return []

    results = []
    for entry in sorted(source.iterdir()):
        if not entry.is_dir():
            continue

        images = sorted([
            str(f) for f in entry.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ])

        if not images:
            # Check one level deeper
            for sub in entry.iterdir():
                if sub.is_dir():
                    images += sorted([
                        str(f) for f in sub.iterdir()
                        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
                    ])

        results.append({
            "folder_name": entry.name,
            "folder_path": str(entry),
            "image_paths": images,
        })

    return results
