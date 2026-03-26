"""
TriageRouter — classifies each item as:
  single   → list individually
  lot      → group with similar items
  review   → human must inspect before listing
  reject   → not worth listing

Rules are config-driven from config/rules.json.
Manual override always wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from packages.core.src.config import get_rules, get_categories
from packages.core.src.constants import ItemMode, ReviewTrigger
from packages.domain.src.entities.item import Item


@dataclass
class TriageResult:
    item_mode: str
    reasons: list[str] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


class TriageRouter:
    def __init__(self):
        self.rules = get_rules()
        self.categories = get_categories()
        triage = self.rules.get("triage", {})
        self.single_min = triage.get("single_min_estimated_value_usd", 5.0)
        self.lot_max = triage.get("lot_max_individual_value_usd", 12.0)
        self.reject_max = triage.get("reject_max_estimated_value_usd", 2.0)
        self.high_val_threshold = triage.get("high_value_review_threshold_usd", 75.0)
        self.confidence_threshold = triage.get("confidence_review_threshold", 0.72)
        self.min_images = triage.get("min_images_required", 1)

    def route(self, item: Item) -> TriageResult:
        # Manual override: if mode already set by human, respect it
        if item.manual_override and item.item_mode in ItemMode.ALL:
            return TriageResult(
                item_mode=item.item_mode,
                reasons=["manual_override"],
                needs_review=item.needs_review,
                review_reasons=item.review_reasons,
            )

        review_reasons = list(item.review_reasons or [])
        reasons = []

        # ── Review triggers ───────────────────────────────────────────
        if item.needs_review or review_reasons:
            return TriageResult(
                item_mode=ItemMode.REVIEW,
                needs_review=True,
                review_reasons=review_reasons,
            )

        if (item.confidence_score or 0) < self.confidence_threshold:
            review_reasons.append(ReviewTrigger.LOW_CONFIDENCE)

        if item.image_count_from_paths() < self.min_images:
            review_reasons.append(ReviewTrigger.IMAGE_INSUFFICIENCY)

        est = item.estimated_price or 0.0
        if est >= self.high_val_threshold:
            review_reasons.append(ReviewTrigger.HIGH_VALUE_ESTIMATE)

        # Check category-specific triggers
        category_triggers = (
            self.rules.get("review_triggers", {}).get(item.category_key or "", [])
        )
        for trigger in category_triggers:
            text = f"{item.title_raw or ''} {item.condition_notes or ''} {item.brand or ''}".lower()
            if trigger.replace("_", " ") in text:
                review_reasons.append(trigger)

        if review_reasons:
            return TriageResult(
                item_mode=ItemMode.REVIEW,
                needs_review=True,
                review_reasons=review_reasons,
            )

        # ── Reject ────────────────────────────────────────────────────
        if est > 0 and est <= self.reject_max:
            reasons.append(f"estimated_price_${est:.2f}_below_reject_threshold")
            # Don't reject outright — send to lot check
            # If not lot eligible, reject
            profile = self.categories.get(item.category_key or "", {})
            if not profile.get("lot_eligible", False):
                return TriageResult(item_mode=ItemMode.REJECT, reasons=reasons)

        # ── Lot candidate ─────────────────────────────────────────────
        profile = self.categories.get(item.category_key or "", {})
        lot_eligible = profile.get("lot_eligible", False)
        if lot_eligible and est > 0 and est <= self.lot_max:
            reasons.append(f"low_individual_value_${est:.2f}_lot_candidate")
            return TriageResult(item_mode=ItemMode.LOT, reasons=reasons)

        # ── Single ────────────────────────────────────────────────────
        return TriageResult(item_mode=ItemMode.SINGLE, reasons=["meets_single_criteria"])

    def image_count_from_paths(self, item: Item) -> int:
        return len(item.image_paths or [])


# Monkey-patch Item to support image count (avoids circular import)
def _image_count(self) -> int:
    return len(self.image_paths or [])

Item.image_count_from_paths = _image_count
