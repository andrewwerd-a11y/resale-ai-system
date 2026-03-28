from __future__ import annotations
import json
from packages.core.src.config import get_settings


def load_categories() -> dict:
    settings = get_settings()
    path = settings.config_dir / "categories.json"
    with open(path) as f:
        return json.load(f)


def get_ebay_category_id(category: str) -> str:
    cats = load_categories()
    profile = cats.get(category, {})
    return profile.get("ebay_category_id", "1")


def get_required_fields(category: str) -> list[str]:
    cats = load_categories()
    profile = cats.get(category, {})
    return profile.get("required_fields", ["title", "condition"])


def get_category_profile(category: str) -> dict:
    cats = load_categories()
    return cats.get(category, {})


def map_ai_category(raw: str | None) -> str:
    """Normalize AI-returned category to canonical name."""
    if not raw:
        return "Collectibles"
    KNOWN = {"Books", "Clothing", "Collectibles", "Shoes", "Toys"}
    for known in KNOWN:
        if known.lower() == raw.strip().lower():
            return known
    # Fuzzy
    raw_lower = raw.lower()
    if "book" in raw_lower:
        return "Books"
    if "cloth" in raw_lower or "apparel" in raw_lower or "wear" in raw_lower:
        return "Clothing"
    if "shoe" in raw_lower or "boot" in raw_lower or "sneaker" in raw_lower:
        return "Shoes"
    if "toy" in raw_lower or "game" in raw_lower or "lego" in raw_lower:
        return "Toys"
    return "Collectibles"
