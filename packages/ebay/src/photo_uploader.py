"""
Photo hosting via Cloudinary.

To set up Cloudinary:
1. Sign up for a free account at https://cloudinary.com (25 GB storage, 25 GB bandwidth/month free)
2. From your Cloudinary dashboard, copy:
   - Cloud Name
   - API Key
   - API Secret
3. Add to .env:
   CLOUDINARY_CLOUD_NAME=your_cloud_name
   CLOUDINARY_API_KEY=your_api_key
   CLOUDINARY_API_SECRET=your_api_secret

If credentials are not set, upload_image() returns a file:/// fallback URL so
the rest of the publish flow never crashes — eBay will simply have no photo URL.
"""
from __future__ import annotations

import cloudinary
import cloudinary.uploader

from pathlib import Path

from packages.core.src.config import get_settings
from packages.core.src.result import Result

MAX_PHOTOS = 12


class PhotoUploader:
    def __init__(self):
        settings = get_settings()
        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )
        self.configured = bool(
            settings.cloudinary_cloud_name
            and settings.cloudinary_api_key
            and settings.cloudinary_api_secret
        )

    def is_configured(self) -> bool:
        return self.configured

    def upload_image(self, image_path: Path) -> Result[str]:
        """Upload a single image. Returns https:// URL or file:// fallback."""
        if not self.configured:
            return Result.success(f"file:///{image_path}")
        if not image_path.exists():
            return Result.failure(f"Image not found: {image_path}")
        try:
            result = cloudinary.uploader.upload(
                str(image_path),
                folder="resale-ai",
                use_filename=True,
                unique_filename=True,
                overwrite=False,
            )
            return Result.success(result["secure_url"])
        except Exception as e:
            return Result.failure(f"Cloudinary upload failed: {e}")

    def upload_item_photos(self, image_paths: list[Path], max_photos: int = MAX_PHOTOS) -> list[str]:
        """Upload up to max_photos images, skipping failures. Returns list of URLs."""
        urls = []
        for path in image_paths[:max_photos]:
            result = self.upload_image(path)
            if result.is_ok:
                urls.append(result.value)
        return urls

    def photos_already_hosted(self, image_paths: list[str]) -> bool:
        """Return True if all non-empty paths are already https:// URLs."""
        non_empty = [p for p in image_paths if p]
        if not non_empty:
            return False
        return all(p.startswith("https://") for p in non_empty)


# ---------------------------------------------------------------------------
# Module-level convenience functions (backwards-compatible with inventory_client)
# ---------------------------------------------------------------------------

_uploader: PhotoUploader | None = None


def _get_uploader() -> PhotoUploader:
    global _uploader
    if _uploader is None:
        _uploader = PhotoUploader()
    return _uploader


def upload_image(path: str | Path) -> Result[str]:
    return _get_uploader().upload_image(Path(path))


def upload_item_photos(paths: list[str], max_photos: int = MAX_PHOTOS) -> list[str]:
    return _get_uploader().upload_item_photos([Path(p) for p in paths], max_photos)


def photos_already_hosted(paths: list[str]) -> bool:
    return _get_uploader().photos_already_hosted(paths)
