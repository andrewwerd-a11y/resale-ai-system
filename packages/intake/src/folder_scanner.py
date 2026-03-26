"""
FolderScanner — walks the intake folder and builds a manifest of
item folders ready for processing.

Handles both:
  - New items in intake/pending/ (may have TEMP_ID names)
  - Existing inventory (already have proper SKU folder names)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from packages.core.src.config import get_settings, get_sku_prefixes
from packages.core.src.constants import SUPPORTED_IMAGE_EXTENSIONS


SKU_PATTERN = re.compile(r"^([A-Z]{2})-(\d{6})$")


@dataclass
class FolderManifest:
    folder_path: Path
    folder_name: str
    detected_sku: str | None          # e.g. "CL-000007" if name matches pattern
    detected_prefix: str | None       # e.g. "CL"
    detected_number: int | None       # e.g. 7
    image_paths: list[Path] = field(default_factory=list)
    image_count: int = 0
    is_existing_sku: bool = False     # True if SKU already exists in DB
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.image_count > 0 and len(self.errors) == 0

    @property
    def needs_sku_assignment(self) -> bool:
        return self.detected_sku is None


class FolderScanner:
    def __init__(self):
        self.settings = get_settings()
        self.prefixes = get_sku_prefixes()
        self.valid_prefixes = set(self.prefixes.keys())

    def scan_pending(self) -> list[FolderManifest]:
        """Scan intake/pending/ for new item folders."""
        pending_dir = self.settings.intake_root / "pending"
        return self._scan_directory(pending_dir)

    def scan_existing(self, source_path: Path) -> list[FolderManifest]:
        """
        Scan an existing inventory directory (e.g. Inventory_Photos/).
        Used by the migration/backfill script.
        Recursively finds all SKU-named folders.
        """
        manifests = []
        # Walk up to 2 levels deep (category subfolder / SKU subfolder)
        for item in source_path.iterdir():
            if item.is_dir():
                match = SKU_PATTERN.match(item.name)
                if match:
                    manifests.append(self._build_manifest(item))
                else:
                    # Could be a category folder like BK/, CL/
                    for sub in item.iterdir():
                        if sub.is_dir() and SKU_PATTERN.match(sub.name):
                            manifests.append(self._build_manifest(sub))
        return sorted(manifests, key=lambda m: (m.detected_prefix or "", m.detected_number or 0))

    def _scan_directory(self, directory: Path) -> list[FolderManifest]:
        if not directory.exists():
            return []
        manifests = []
        for item in directory.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                manifests.append(self._build_manifest(item))
        return manifests

    def _build_manifest(self, folder: Path) -> FolderManifest:
        name = folder.name
        match = SKU_PATTERN.match(name)

        detected_sku = None
        detected_prefix = None
        detected_number = None

        if match:
            prefix = match.group(1)
            number = int(match.group(2))
            if prefix in self.valid_prefixes:
                detected_sku = name
                detected_prefix = prefix
                detected_number = number

        # Find images
        images = sorted([
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ])

        errors = []
        if len(images) == 0:
            errors.append("no_images_found")

        return FolderManifest(
            folder_path=folder,
            folder_name=name,
            detected_sku=detected_sku,
            detected_prefix=detected_prefix,
            detected_number=detected_number,
            image_paths=images,
            image_count=len(images),
            errors=errors,
        )

    def validate_image_order(self, manifest: FolderManifest) -> list[str]:
        """
        Check that images follow the 01.jpg naming convention.
        Returns list of warnings (non-blocking).
        """
        warnings = []
        for i, path in enumerate(manifest.image_paths, start=1):
            expected = f"{i:02d}.jpg"
            if path.name.lower() != expected:
                warnings.append(f"Expected {expected}, found {path.name}")
        return warnings
