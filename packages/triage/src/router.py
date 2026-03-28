from __future__ import annotations
import json
from packages.core.src.config import get_settings
from packages.domain.src.entities.item import Item


def load_rules() -> dict:
    settings = get_settings()
    path = settings.config_dir / "rules.json"
    with open(path) as f:
        return json.load(f)


def triage_item(item: Item) -> tuple[str, list[str]]:
    """
    Evaluate an item and return (status, review_reasons).
    Status is either 'approved' or 'needs_review'.
    """
    settings = get_settings()
    rules = load_rules()
    review_reasons: list[str] = []

    # Skip if manually overridden
    if item.manual_override:
        return (item.status, item.review_reasons)

    confidence = item.ai_confidence or 0.0
    estimated = item.estimated_price or 0.0

    # Low confidence
    threshold = settings.confidence_review_threshold
    if confidence < threshold:
        review_reasons.append(f"low_confidence:{confidence:.2f}")

    # High value
    high_val = settings.high_value_review_threshold
    if estimated >= high_val:
        review_reasons.append(f"high_value:{estimated:.2f}")

    # Missing required fields
    required = ["title", "condition"]
    missing = [f for f in required if not getattr(item, f, None)]
    if missing:
        review_reasons.append(f"missing_fields:{','.join(missing)}")

    # Condition concerns in defects
    concern_keywords = rules.get("review_triggers", {}).get("condition_concern", {}).get("keywords", [])
    all_defects = " ".join(item.defects or []).lower()
    if any(k in all_defects for k in concern_keywords):
        review_reasons.append("condition_concern")

    if review_reasons:
        return ("needs_review", review_reasons)
    return ("approved", [])
