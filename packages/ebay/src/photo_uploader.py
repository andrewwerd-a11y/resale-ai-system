"""
Photo hosting via Imgur anonymous upload.

To get a free Imgur Client ID:
1. Go to https://api.imgur.com/oauth2/addclient
2. Select "OAuth 2 authorization without a callback URL"
3. Fill in application name (e.g. "ResaleAI")
4. Copy the Client ID and add it to .env as: IMGUR_CLIENT_ID=your_client_id_here

The uploader falls back gracefully — if IMGUR_CLIENT_ID is not set,
upload_item_photos() returns an empty list and photos_already_hosted() is used
to detect pre-hosted URLs. The system never crashes without Imgur configured.
"""
from __future__ import annotations
from pathlib import Path

import httpx

from packages.core.src.config import get_settings
from packages.core.src.result import Result

IMGUR_UPLOAD_URL = "https://api.imgur.com/3/image"
MAX_PHOTOS = 12


def _imgur_configured() -> bool:
    return bool(get_settings().imgur_client_id)


def upload_image(path: str | Path) -> Result[str]:
    """Upload a single image to Imgur. Returns the https:// URL."""
    if not _imgur_configured():
        return Result.failure("IMGUR_CLIENT_ID not set")

    p = Path(path)
    if not p.exists():
        return Result.failure(f"Image not found: {path}")

    client_id = get_settings().imgur_client_id

    try:
        with open(p, "rb") as f:
            image_data = f.read()

        with httpx.Client(timeout=60) as client:
            resp = client.post(
                IMGUR_UPLOAD_URL,
                headers={"Authorization": f"Client-ID {client_id}"},
                files={"image": (p.name, image_data, _mime_type(p))},
            )
            resp.raise_for_status()
            data = resp.json()

        link = data.get("data", {}).get("link", "")
        if not link:
            return Result.failure(f"Imgur returned no link: {data}")

        # Force HTTPS
        if link.startswith("http://"):
            link = "https://" + link[7:]

        return Result.success(link)

    except httpx.HTTPStatusError as e:
        return Result.failure(f"Imgur HTTP error {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        return Result.failure(f"Imgur upload failed: {e}")


def upload_item_photos(paths: list[str], max_photos: int = MAX_PHOTOS) -> list[str]:
    """
    Upload up to max_photos images. Skips already-hosted URLs and failures.
    Returns list of https:// URLs (may be empty if Imgur not configured).
    """
    if not _imgur_configured():
        return []

    urls: list[str] = []
    for path in paths[:max_photos]:
        if _is_url(path):
            urls.append(path)
            continue
        result = upload_image(path)
        if result.is_ok:
            urls.append(result.value)
        # Skip failures silently

    return urls


def photos_already_hosted(paths: list[str]) -> bool:
    """Return True if all paths are already https:// URLs."""
    if not paths:
        return False
    return all(_is_url(p) for p in paths)


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _mime_type(p: Path) -> str:
    ext = p.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")
