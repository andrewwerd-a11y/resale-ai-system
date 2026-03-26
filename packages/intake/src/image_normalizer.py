"""
ImageNormalizer — ensures all images in an item folder follow the
standard naming convention (01.jpg, 02.jpg, ...) and are within
the configured max dimension.

Non-destructive: originals are backed up before renaming.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from packages.core.src.config import get_settings
from packages.core.src.constants import SUPPORTED_IMAGE_EXTENSIONS
from packages.core.src.result import Result


class ImageNormalizer:
    def __init__(self):
        settings = get_settings()
        self.max_dimension = settings.extraction.get("image_max_dimension", 1600) if hasattr(settings, "extraction") else 1600
        self.quality = 85

    def normalize_folder(self, folder: Path, dry_run: bool = False) -> Result[list[Path]]:
        """
        Rename all images in folder to 01.jpg, 02.jpg, ...
        Resize any that exceed max_dimension.
        Returns Result with list of normalized image paths.
        """
        images = sorted([
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ])

        if not images:
            return Result.failure("no_images_found", folder=str(folder))

        if dry_run:
            normalized = [folder / f"{i:02d}.jpg" for i in range(1, len(images) + 1)]
            return Result.success(normalized)

        # Back up originals if any have non-standard names
        needs_rename = any(
            img.name.lower() != f"{i:02d}.jpg"
            for i, img in enumerate(images, 1)
        )
        if needs_rename:
            backup_dir = folder / "_original_backup"
            backup_dir.mkdir(exist_ok=True)
            for img in images:
                shutil.copy2(img, backup_dir / img.name)

        normalized_paths = []
        for i, src in enumerate(images, start=1):
            dest = folder / f"{i:02d}.jpg"
            try:
                with Image.open(src) as img:
                    img = img.convert("RGB")
                    # Resize if over max dimension
                    if max(img.size) > self.max_dimension:
                        img.thumbnail((self.max_dimension, self.max_dimension), Image.LANCZOS)
                    img.save(dest, "JPEG", quality=self.quality, optimize=True)
                # Remove source if it was renamed (avoid keeping both)
                if src != dest and src.exists():
                    src.unlink()
                normalized_paths.append(dest)
            except Exception as e:
                return Result.failure(f"image_processing_failed: {src.name}: {e}")

        return Result.success(normalized_paths)

    def get_normalized_paths(self, folder: Path) -> list[Path]:
        """Return sorted list of normalized image paths that currently exist."""
        return sorted([
            p for p in folder.iterdir()
            if p.is_file() and p.name.lower().endswith(".jpg")
            and p.stem.isdigit()
        ], key=lambda p: int(p.stem))
