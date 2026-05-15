"""Provider-agnostic platform translation foundation.

A canonical item -> per-platform listing draft. Draft only — never publishes,
never mutates remote state. The eBay translator REUSES existing publish
readiness/compatibility services rather than duplicating their payload logic.

Supported platforms today:
- ebay (deterministic translator using existing publish_readiness)

Stubs for: facebook_marketplace, mercari, poshmark, etsy, depop, generic.
These return ``platform_supported=False`` but a sane preview shape so the UI
can render "platform translation coming soon" sections without special-casing.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol

from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item


@dataclass
class PlatformListingDraft:
    platform: str
    platform_supported: bool
    title: str | None = None
    description: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    price: float | None = None
    condition: str | None = None
    condition_id: str | None = None
    photos: list[str] = field(default_factory=list)
    item_specifics: dict = field(default_factory=dict)
    shipping: dict = field(default_factory=dict)
    seller_policies: dict = field(default_factory=dict)
    missing_platform_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    readiness_status: str = "blocked"  # ready | blocked | needs_review
    publish_allowed: bool = False
    manual_review_required: bool = True
    reason: str = ""
    provider: str = "deterministic-fallback"

    def to_dict(self) -> dict:
        return asdict(self)


class PlatformTranslator(Protocol):
    platform: str

    def translate(self, item: Item) -> PlatformListingDraft: ...


class EbayDeterministicTranslator:
    """eBay draft translator.

    Reuses ``apps.api.src.services.publish_readiness.evaluate_publish_readiness``
    and ``publish_compatibility.evaluate_publish_compatibility`` for the
    publish-side checks instead of duplicating payload logic. Returns a draft
    with ``publish_allowed=False`` whenever any blocker is present, regardless
    of how local data looks. ``manual_review_required`` stays True until human
    approval flips it elsewhere.
    """

    platform = Platform.EBAY

    def translate(self, item: Item) -> PlatformListingDraft:
        from apps.api.src.services.publish_compatibility import evaluate_publish_compatibility
        from apps.api.src.services.publish_readiness import evaluate_publish_readiness

        readiness = evaluate_publish_readiness(item).as_dict()
        compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)

        blockers: list[str] = []
        blockers.extend(readiness.get("blockers") or [])
        blockers.extend(compatibility.get("blockers") or [])
        warnings: list[str] = []
        warnings.extend(readiness.get("warnings") or [])

        missing_fields = list(item.missing_required_fields or [])
        publish_allowed = (
            bool(readiness.get("ready"))
            and not blockers
            and not missing_fields
        )
        readiness_status = "ready" if publish_allowed else "blocked"
        if blockers and not missing_fields and item.needs_review:
            readiness_status = "needs_review"

        return PlatformListingDraft(
            platform=Platform.EBAY,
            platform_supported=True,
            title=item.title_final or item.title_raw,
            description=item.description_final,
            category_id=str(item.ebay_category_id) if item.ebay_category_id else None,
            category_name=item.ebay_category_name,
            price=item.list_price or item.estimated_price,
            condition=item.condition_label,
            condition_id=str(item.condition_id) if item.condition_id else None,
            photos=list(item.image_paths or []),
            item_specifics=dict(item.item_specifics or {}),
            shipping={
                "weight": item.shipping_weight,
                "method": item.shipping_method,
            },
            seller_policies={},
            missing_platform_fields=missing_fields,
            warnings=warnings,
            readiness_status=readiness_status,
            publish_allowed=publish_allowed,
            manual_review_required=True,
            reason=(
                "All publish checks passed; manual approval still required."
                if publish_allowed
                else "Blocked by readiness/compatibility checks."
            ),
            provider="deterministic-fallback",
        )


class _UnsupportedTranslator:
    def __init__(self, platform: str) -> None:
        self.platform = platform

    def translate(self, item: Item) -> PlatformListingDraft:
        return PlatformListingDraft(
            platform=self.platform,
            platform_supported=False,
            readiness_status="blocked",
            publish_allowed=False,
            manual_review_required=True,
            reason=f"Platform '{self.platform}' translation is not implemented yet.",
        )


# Public registry of platforms exposed to callers. Keep small and explicit.
SUPPORTED_PLATFORMS = [
    Platform.EBAY,
    "facebook_marketplace",
    "mercari",
    "poshmark",
    "etsy",
    "depop",
    "generic",
]


def get_translator(platform: str) -> PlatformTranslator:
    if platform == Platform.EBAY:
        return EbayDeterministicTranslator()
    return _UnsupportedTranslator(platform)


def translate_item_for_platforms(
    item: Item,
    platforms: list[str] | None = None,
) -> list[PlatformListingDraft]:
    """Generate drafts for one or more platforms. Never publishes."""
    target = platforms or [Platform.EBAY]
    return [get_translator(p).translate(item) for p in target]


# ── Marketplace recommendations (slice 4 / phase 13) ─────────────────────────

@dataclass
class MarketplaceRecommendation:
    platform: str
    recommended: bool
    reason: str
    expected_fit_score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    readiness: str = "blocked"
    missing_fields: list[str] = field(default_factory=list)
    manual_review_required: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# Simple deterministic fit table per category family. Conservative defaults.
_FAMILY_FIT: dict[str, dict[str, float]] = {
    "books": {Platform.EBAY: 0.8, "mercari": 0.5, "etsy": 0.4, "facebook_marketplace": 0.3},
    "clothing": {Platform.EBAY: 0.7, "poshmark": 0.85, "mercari": 0.65, "depop": 0.7, "facebook_marketplace": 0.4},
    "shoes": {Platform.EBAY: 0.75, "poshmark": 0.8, "mercari": 0.65, "depop": 0.7},
    "plush_toys": {Platform.EBAY: 0.7, "mercari": 0.55, "facebook_marketplace": 0.5, "etsy": 0.4},
    "bags": {Platform.EBAY: 0.8, "poshmark": 0.8, "mercari": 0.5, "facebook_marketplace": 0.4},
    "collectibles_antiques": {Platform.EBAY: 0.85, "etsy": 0.6, "facebook_marketplace": 0.4},
}


def recommend_marketplaces(
    item: Item,
    *,
    selection_mode: str = "hybrid",  # manual | auto | hybrid
) -> dict:
    """Return platform recommendations + drafts.

    ``selection_mode``:
    - manual: the recommendations are informational only; user picks.
    - auto: system suggests "recommended=True" picks.
    - hybrid: system suggests picks but defaults to user approval per platform.

    Never publishes; nothing here mutates anything.
    """
    from packages.intake.src.pipeline_types import RiskFlag
    from packages.intake.src.quality_gate import category_family_for_item

    family = category_family_for_item(item)
    fit_table = _FAMILY_FIT.get(family, {Platform.EBAY: 0.5})

    drafts = translate_item_for_platforms(item, platforms=SUPPORTED_PLATFORMS)
    drafts_by_platform = {d.platform: d for d in drafts}

    recs: list[MarketplaceRecommendation] = []
    for platform in SUPPORTED_PLATFORMS:
        draft = drafts_by_platform[platform]
        fit = fit_table.get(platform, 0.0)
        risk_flags: list[str] = []
        if not draft.platform_supported:
            risk_flags.append("platform_not_implemented")
        if draft.missing_platform_fields:
            risk_flags.append(RiskFlag.MISSING_REQUIRED_FIELDS)
        if (item.estimated_price or 0) >= 75:
            risk_flags.append(RiskFlag.HIGH_VALUE_ESTIMATE)

        recommended = False
        if selection_mode in {"auto", "hybrid"}:
            recommended = (
                draft.platform_supported
                and fit >= 0.6
                and draft.readiness_status != "blocked"
            )

        recs.append(
            MarketplaceRecommendation(
                platform=platform,
                recommended=recommended,
                reason=(
                    f"Strong fit for {family} on {platform}."
                    if fit >= 0.6
                    else f"Weak deterministic fit ({fit:.2f}) for {family} on {platform}."
                ),
                expected_fit_score=fit,
                risk_flags=risk_flags,
                readiness=draft.readiness_status,
                missing_fields=draft.missing_platform_fields,
                manual_review_required=True,
            )
        )

    return {
        "sku": item.sku,
        "selection_mode": selection_mode,
        "category_family": family,
        "recommendations": [r.to_dict() for r in recs],
        "drafts": [d.to_dict() for d in drafts],
        "no_ebay_mutation_performed": True,
        "no_external_provider_called": True,
    }
