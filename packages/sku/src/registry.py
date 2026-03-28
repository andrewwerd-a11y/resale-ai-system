from __future__ import annotations
import json
from pathlib import Path
from packages.core.src.config import get_settings


def load_prefixes() -> dict[str, str]:
    settings = get_settings()
    path = settings.config_dir / "sku_prefixes.json"
    with open(path) as f:
        return json.load(f)


def category_to_prefix(category: str) -> str:
    prefixes = load_prefixes()
    # Try direct match first
    for prefix, cat in prefixes.items():
        if cat.lower() == category.lower():
            return prefix
    # Try partial match
    for prefix, cat in prefixes.items():
        if cat.lower() in category.lower() or category.lower() in cat.lower():
            return prefix
    return "CO"  # Default to Collectibles


def prefix_to_category(prefix: str) -> str:
    prefixes = load_prefixes()
    return prefixes.get(prefix.upper(), "Collectibles")


def guess_category_from_folder(folder_name: str) -> str:
    """
    Infer category from folder naming conventions.
    e.g. 'BK-000001-Harry Potter' → Books
    """
    name = folder_name.upper()
    prefixes = load_prefixes()
    for prefix in prefixes:
        if name.startswith(prefix + "-") or name.startswith(prefix + "_"):
            return prefix_to_category(prefix)

    # Keyword fallback
    name_lower = folder_name.lower()
    if any(k in name_lower for k in ["book", "novel", "paperback", "hardcover"]):
        return "Books"
    if any(k in name_lower for k in ["shirt", "pants", "dress", "jacket", "coat", "jeans"]):
        return "Clothing"
    if any(k in name_lower for k in ["shoe", "boot", "sneaker", "sandal"]):
        return "Shoes"
    if any(k in name_lower for k in ["toy", "lego", "action figure", "doll", "game"]):
        return "Toys"

    return "Collectibles"


def extract_sku_from_folder(folder_name: str) -> str | None:
    """
    Extract existing SKU from folder name if present.
    e.g. 'BK-000001-Harry Potter' → 'BK-000001'
         'CL_000007_Blue Shirt'   → 'CL-000007'
    """
    import re
    match = re.match(r"([A-Z]{2})[-_](\d{6})", folder_name.upper())
    if match:
        prefix = match.group(1)
        seq = match.group(2)
        return f"{prefix}-{seq}"
    return None
