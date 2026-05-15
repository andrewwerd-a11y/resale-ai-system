"""Provider-agnostic category resolver.

Returns multiple candidate internal category families and platform-specific
category candidates (eBay first). Deterministic fallback uses only locally
available data — no live API calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol

from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.intake.src.identity_scan import IdentityScanResult
from packages.intake.src.pipeline_types import (
    DETERMINISTIC_FALLBACK_WARNING,
    ConfidenceSource,
    ProviderKind,
)
from packages.intake.src.quality_gate import category_family_for_item


@dataclass
class CategoryCandidate:
    platform: str
    category_id: str | None
    category_name: str | None
    confidence: float = 0.0
    reason: str = ""
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    buyer_visibility_score: float = 0.0
    requirement_fit_score: float = 0.0
    condition_policy_known: bool = False
    publish_reliability_score: float = 0.0
    recommended: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CategoryResolution:
    sku: str | None
    internal_family_candidates: list[str] = field(default_factory=list)
    marketplace_candidates: list[CategoryCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    provider: str = "deterministic-fallback"
    provider_kind: str = ProviderKind.DETERMINISTIC_FALLBACK
    confidence_source: str = ConfidenceSource.HEURISTIC
    is_deterministic_fallback: bool = True
    fallback_warning: str = DETERMINISTIC_FALLBACK_WARNING

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "internal_family_candidates": self.internal_family_candidates,
            "marketplace_candidates": [c.to_dict() for c in self.marketplace_candidates],
            "notes": self.notes,
            "provider": self.provider,
            "provider_kind": self.provider_kind,
            "confidence_source": self.confidence_source,
            "is_deterministic_fallback": self.is_deterministic_fallback,
            "fallback_warning": self.fallback_warning,
        }


class CategoryResolver(Protocol):
    name: str

    def resolve(
        self,
        item: Item,
        identity: IdentityScanResult | None = None,
    ) -> CategoryResolution: ...


# Lightweight family → eBay leaf hints used by the deterministic resolver.
_FAMILY_TO_EBAY_HINTS: dict[str, list[tuple[str, str]]] = {
    "books": [("261186", "Books & Magazines > Books")],
    "clothing": [("11450", "Clothing, Shoes & Accessories")],
    "shoes": [("93427", "Clothing, Shoes & Accessories > Shoes")],
    "plush_toys": [("48084", "Dolls & Bears > Bears > Other Bears")],
    "bags": [("169291", "Clothing, Shoes & Accessories > Handbags & Purses")],
    "collectibles_antiques": [("1", "Collectibles & Antiques")],
}


class DeterministicCategoryResolver:
    """Read-only fallback resolver.

    Uses the item's existing eBay category fields (if set) and category-family
    inference. Does not contact live taxonomy APIs.
    """

    name = "deterministic-fallback"

    def resolve(
        self,
        item: Item,
        identity: IdentityScanResult | None = None,
    ) -> CategoryResolution:
        family = category_family_for_item(item)
        internal_families = []
        if family:
            internal_families.append(family)
        if identity:
            for candidate in identity.category_family_candidates:
                if candidate and candidate not in internal_families:
                    internal_families.append(candidate)

        candidates: list[CategoryCandidate] = []
        notes: list[str] = []

        if item.ebay_category_id:
            candidates.append(
                CategoryCandidate(
                    platform=Platform.EBAY,
                    category_id=str(item.ebay_category_id),
                    category_name=item.ebay_category_name,
                    confidence=0.7 if item.category_template_fetched else 0.5,
                    reason="Item already has an eBay category assigned.",
                    pros=["Already assigned — no taxonomy call needed."],
                    cons=[] if item.category_template_fetched else ["Category template has not been fetched yet."],
                    condition_policy_known=bool(item.category_template_fetched),
                    requirement_fit_score=0.7 if item.category_template_fetched else 0.4,
                    publish_reliability_score=0.7 if item.category_template_fetched else 0.4,
                    buyer_visibility_score=0.6,
                    recommended=True,
                )
            )
        else:
            notes.append("No eBay category assigned yet; using deterministic family fallback only.")

        for hint_family in internal_families:
            for category_id, category_name in _FAMILY_TO_EBAY_HINTS.get(hint_family, []):
                if any(c.category_id == category_id for c in candidates):
                    continue
                candidates.append(
                    CategoryCandidate(
                        platform=Platform.EBAY,
                        category_id=category_id,
                        category_name=category_name,
                        confidence=0.3,
                        reason=f"Deterministic family fallback for {hint_family}.",
                        pros=["Reasonable starting point for category review."],
                        cons=["Not validated against live eBay taxonomy."],
                        condition_policy_known=False,
                        requirement_fit_score=0.3,
                        publish_reliability_score=0.3,
                        buyer_visibility_score=0.4,
                        recommended=False,
                    )
                )

        if not candidates:
            notes.append("No category candidates could be derived; manual category review required.")

        return CategoryResolution(
            sku=item.sku,
            internal_family_candidates=internal_families,
            marketplace_candidates=candidates,
            notes=notes,
            provider=self.name,
            provider_kind=ProviderKind.DETERMINISTIC_FALLBACK,
            confidence_source=ConfidenceSource.HEURISTIC,
            is_deterministic_fallback=True,
            fallback_warning=DETERMINISTIC_FALLBACK_WARNING,
        )


def resolve_categories(
    item: Item,
    identity: IdentityScanResult | None = None,
    resolver: CategoryResolver | None = None,
) -> CategoryResolution:
    resolver = resolver or DeterministicCategoryResolver()
    return resolver.resolve(item, identity)
