from __future__ import annotations
from pathlib import Path
from typing import Optional

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def normalize_image_path(path: str) -> str:
    """
    Normalize a single image path:
    - Convert .jpeg extension to .jpg in the path string
    - If PIL is available and file exists, convert the actual file
    """
    p = Path(path)
    if p.suffix.lower() == ".jpeg":
        new_path = p.with_suffix(".jpg")
        if p.exists() and not new_path.exists():
            if PIL_AVAILABLE:
                try:
                    with Image.open(p) as img:
                        img.save(new_path, "JPEG")
                    p.unlink()
                except Exception:
                    pass
        if new_path.exists():
            return str(new_path)
        if p.exists():
            return str(p)
        return str(new_path)
    return path


def normalize_paths(paths: list[str]) -> list[str]:
    return [normalize_image_path(p) for p in paths]
