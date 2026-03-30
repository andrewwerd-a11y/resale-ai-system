"""
PhotoUploader — uploads local image files to Cloudinary for eBay listing photos.
Falls back to local file paths if Cloudinary is not configured.
"""
from __future__ import annotations

from pathlib import Path

from packages.core.src.config import get_settings
from packages.core.src.result import Result


class PhotoUploader:
    def __init__(self):
        self.settings = get_settings()

    def is_configured(self) -> bool:
        """Return True if all three Cloudinary credentials are set."""
        s = self.settings
        return bool(s.cloudinary_cloud_name and s.cloudinary_api_key and s.cloudinary_api_secret)

    def upload(self, image_path: Path) -> Result[str]:
        """
        Upload a single image to Cloudinary and return the secure URL.
        Returns Result.failure if not configured or upload fails.
        """
        if not self.is_configured():
            return Result.failure("Cloudinary credentials not configured", error_code="NOT_CONFIGURED")
        if not image_path.exists():
            return Result.failure(f"Image not found: {image_path}", error_code="FILE_NOT_FOUND")
        try:
            import cloudinary
            import cloudinary.uploader
            cloudinary.config(
                cloud_name=self.settings.cloudinary_cloud_name,
                api_key=self.settings.cloudinary_api_key,
                api_secret=self.settings.cloudinary_api_secret,
            )
            resp = cloudinary.uploader.upload(
                str(image_path),
                folder="resale",
                use_filename=True,
                unique_filename=True,
            )
            return Result.success(resp["secure_url"])
        except Exception as exc:
            return Result.failure(str(exc), error_code="UPLOAD_ERROR")

    def upload_all(self, image_paths: list[Path]) -> list[str]:
        """
        Upload multiple images and return list of URLs.
        Falls back to local path string for any failed uploads.
        """
        urls = []
        for path in image_paths:
            result = self.upload(path)
            urls.append(result.value if result.ok else str(path))
        return urls
