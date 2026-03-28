from __future__ import annotations
import json
import re
from typing import Any

from packages.core.src.result import Result


_CONDITION_MAP = {
    "new": "New",
    "like new": "Like New",
    "excellent": "Excellent",
    "very good": "Very Good",
    "good": "Good",
    "acceptable": "Acceptable",
    "fair": "Fair",
    "poor": "Poor",
    "for parts": "For Parts",
    "for parts or not working": "For Parts",
}

_CONDITION_ID_MAP = {
    "New": "1000",
    "Like New": "1500",
    "Excellent": "2000",
    "Very Good": "2500",
    "Good": "3000",
    "Acceptable": "4000",
    "Fair": "5000",
    "Poor": "6000",
    "For Parts": "7000",
}


def _dedup(lst: list) -> list:
    """Deduplicate a list — safe for unhashable types (dicts)."""
    seen = []
    for item in lst:
        if item not in seen:
            seen.append(item)
    return seen


def _safe_list(val: Any) -> list[str]:
    """Ensure val is a flat list of strings."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val else []
    if isinstance(val, list):
        result = []
        for item in val:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                # Flatten dict values to strings
                for v in item.values():
                    if isinstance(v, str) and v:
                        result.append(v)
            else:
                s = str(item)
                if s:
                    result.append(s)
        return _dedup(result)
    return [str(val)]


def _normalize_condition(raw: Any) -> tuple[str, str]:
    """Return (condition_label, condition_id)."""
    if raw is None:
        return ("Good", "3000")
    s = str(raw).strip().lower()
    label = _CONDITION_MAP.get(s, "Good")
    condition_id = _CONDITION_ID_MAP.get(label, "3000")
    return (label, condition_id)


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a text string."""
    # Try direct parse first
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to find JSON block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def parse_extraction_response(raw: str) -> Result[dict]:
    """
    Parse AI vision extraction response into a clean dict.
    Returns Result.success(data_dict) or Result.failure(error_msg).
    """
    data = _extract_json(raw)
    if data is None:
        return Result.failure(f"Could not extract JSON from response: {raw[:200]}")

    condition_label, condition_id = _normalize_condition(data.get("condition"))

    parsed = {
        "title": data.get("title"),
        "category": data.get("category"),
        "brand": data.get("brand"),
        "item_type": data.get("item_type") or data.get("type"),
        "department": data.get("department"),
        "size": data.get("size"),
        "color": data.get("color"),
        "material": data.get("material"),
        "style": data.get("style"),
        "condition": condition_label,
        "condition_id": condition_id,
        "condition_notes": data.get("condition_notes"),
        "author": data.get("author"),
        "book_format": data.get("format"),
        "isbn": data.get("isbn"),
        "franchise": data.get("franchise"),
        "character": data.get("character"),
        "features": _safe_list(data.get("features")),
        "defects": _safe_list(data.get("defects")),
        "keywords": _safe_list(data.get("keywords")),
        "estimated_price": _safe_float(data.get("estimated_price")),
        "ai_confidence": _safe_float(data.get("confidence"), default=0.5),
    }

    return Result.success(parsed)


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default
