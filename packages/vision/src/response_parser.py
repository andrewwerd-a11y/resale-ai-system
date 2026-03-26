"""
ResponseParser — validates and normalises raw extraction JSON from the vision model.
"""
from __future__ import annotations

import re

from packages.core.src.config import get_categories, get_rules
from packages.core.src.constants import ReviewTrigger
from packages.core.src.result import Result
from packages.core.src.types import JsonDict


def _dedup(lst: list) -> list:
    """Deduplicate a list while preserving order and tolerating unhashable items."""
    seen = []
    for item in lst:
        if item not in seen:
            seen.append(item)
    return seen


class ResponseParser:
    def __init__(self):
        self.categories = get_categories()
        self.rules = get_rules()

    def parse(self, raw: JsonDict, category_key: str) -> Result[JsonDict]:
        profile = self.categories.get(category_key, {})
        required = profile.get("required_fields", [])

        out = dict(raw)

        # Coerce condition_id to string (model sometimes returns int)
        if "condition_id" in out and out["condition_id"] is not None:
            out["condition_id"] = str(out["condition_id"])

        # Ensure list fields are lists
        for field in ["features", "defects", "review_reasons"]:
            if not isinstance(out.get(field), list):
                out[field] = []

        # Ensure measurements is a dict
        if not isinstance(out.get("measurements"), dict):
            out["measurements"] = {}

        # Ensure item_specifics is a dict
        if not isinstance(out.get("item_specifics"), dict):
            out["item_specifics"] = {}

        # Clamp confidence — model sometimes returns a string description or list
        raw_score = out.get("confidence_score") or 0.0
        if isinstance(raw_score, list):
            raw_score = raw_score[0] if raw_score else 0.0
        if isinstance(raw_score, str):
            m = re.search(r"\d+\.?\d*", raw_score)
            raw_score = float(m.group()) / 10.0 if m and float(m.group()) > 1 else (float(m.group()) if m else 0.0)
        score = max(0.0, min(1.0, float(raw_score)))
        out["confidence_score"] = score

        # Coerce numeric price fields — model sometimes returns list or string
        for price_field in ["estimated_price", "list_price"]:
            val = out.get(price_field) or 0.0
            if isinstance(val, list):
                val = val[0] if val else 0.0
            try:
                out[price_field] = float(val)
            except (TypeError, ValueError):
                out[price_field] = 0.0

        # Check required fields
        missing = [f for f in required if not out.get(f)]
        if missing:
            out["review_reasons"] = _dedup(out["review_reasons"] + [ReviewTrigger.MISSING_REQUIRED_FIELDS])
            out["needs_review"] = True

        # Check confidence threshold
        threshold = self.rules.get("triage", {}).get("confidence_review_threshold", 0.72)
        if score < threshold:
            out["review_reasons"] = _dedup(out["review_reasons"] + [ReviewTrigger.LOW_CONFIDENCE])
            out["needs_review"] = True

        # Check high value
        high_val = self.rules.get("triage", {}).get("high_value_review_threshold_usd", 75.0)
        if out["estimated_price"] >= high_val:
            out["review_reasons"] = _dedup(out["review_reasons"] + [ReviewTrigger.HIGH_VALUE_ESTIMATE])
            out["needs_review"] = True

        # Detect review triggers from text signals
        title = str(out.get("title_raw") or "").lower()
        notes = str(out.get("condition_notes") or "").lower()
        brand = str(out.get("brand") or "").lower()
        combined = f"{title} {notes} {brand}"

        trigger_words = {
            ReviewTrigger.SIGNED: ["signed", "autographed", "inscribed"],
            ReviewTrigger.FIRST_EDITION: ["first edition", "1st edition"],
            ReviewTrigger.ANTIQUE: ["antique", "antiquarian"],
            ReviewTrigger.RARE_BINDING: ["rare binding", "leather bound", "vellum"],
        }
        for trigger, words in trigger_words.items():
            if any(w in combined for w in words):
                out["review_reasons"] = _dedup(out["review_reasons"] + [trigger])
                out["needs_review"] = True

        # Check luxury brands
        luxury = self.rules.get("luxury_brands", {}).get(category_key, [])
        brand_raw = (out.get("brand") or "")
        if any(b.lower() == brand_raw.lower() for b in luxury):
            out["review_reasons"] = _dedup(out["review_reasons"] + [ReviewTrigger.LUXURY_BRAND])
            out["needs_review"] = True

        return Result.success(out)
