"""
PhotoUploader — uploads local image files to Imgur for eBay listing photos.
Falls back to local file paths if Imgur is not configured.
"""
from __future__ import annotations

import base64
from pathlib import Path

from packages.core.src.config import get_settings
from packages.core.src.result import Result


class PhotoUploader:
    IMGUR_UPLOAD_URL = "https://api.imgur.com/3/image"

    def __init__(self):
        self.settings = get_settings()

    def is_configured(self) -> bool:
        """Return True if Imgur client ID is set."""
        return bool(self.settings.imgur_client_id)

    def upload(self, image_path: Path) -> Result[str]:
        """
        Upload a single image to Imgur and return the public URL.
        Returns Result.failure if not configured or upload fails.
        """
        if not self.is_configured():
            return Result.failure("Imgur client ID not configured", error_code="NOT_CONFIGURED")
        if not image_path.exists():
            return Result.failure(f"Image not found: {image_path}", error_code="FILE_NOT_FOUND")
        try:
            import urllib.request
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
            data = f"image={urllib.parse.quote(image_data)}&type=base64".encode()
            req = urllib.request.Request(
                self.IMGUR_UPLOAD_URL,
                data=data,
                headers={"Authorization": f"Client-ID {self.settings.imgur_client_id}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                import json
                result = json.loads(resp.read())
                if result.get("success"):
                    return Result.success(result["data"]["link"])
                return Result.failure(f"Imgur error: {result}", error_code="IMGUR_ERROR")
        except Exception as exc:
            return Result.failure(str(exc), error_code="UPLOAD_ERROR")

    def upload_all(self, image_paths: list[Path]) -> list[str]:
        """
        Upload multiple images and return list of URLs.
        Skips failed uploads silently (falls back to local path string).
        """
        urls = []
        for path in image_paths:
            result = self.upload(path)
            if result.ok:
                urls.append(result.value)
            else:
                # Fall back to local path string so the item still has image references
                urls.append(str(path))
        return urls


# Avoid circular import at module level
import urllib.parse  # noqa: E402
