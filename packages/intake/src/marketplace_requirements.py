"""Marketplace requirement fetch/cache contract.

Provider-agnostic. Defines what a platform's category requirements look like
in a normalized shape and provides a deterministic fallback that uses the
existing item.missing_required_fields / item.missing_recommended_fields and
known condition mappings — no live eBay calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol

from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.ebay.src.condition_mapping import CONDITION_ID_TO_ENUM
from packages.intake.src.pipeline_types import (
    DETERMINISTIC_FALLBACK_WARNING,
    ConfidenceSource,
    ProviderKind,
)


@dataclass
class MarketplaceRequirements:
    platform: str
    category_id: str | None
    required_aspects: list[str] = field(default_factory=list)
    recommended_aspects: list[str] = field(default_factory=list)
    allowed_aspect_values: dict[str, list[str]] = field(default_factory=dict)
    category_condition_policy: str | None = None
    allowed_condition_ids: list[str] = field(default_factory=list)
    category_policy_source: str = "deterministic-fallback"
    data_freshness: str = "unknown"  # cached | current | unknown
    requires_live_read_only_fetch: bool = False
    missing_requirements_for_item: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    provider_kind: str = ProviderKind.DETERMINISTIC_FALLBACK
    confidence_source: str = ConfidenceSource.HEURISTIC
    is_deterministic_fallback: bool = True
    fallback_warning: str = DETERMINISTIC_FALLBACK_WARNING

    def to_dict(self) -> dict:
        return asdict(self)


class MarketplaceRequirementProvider(Protocol):
    name: str

    def get_requirements(
        self,
        platform: str,
        category_id: str | None,
        item: Item,
    ) -> MarketplaceRequirements: ...


class DeterministicMarketplaceRequirementProvider:
    """Read-only fallback.

    Uses the item's already-known publish gaps (``missing_required_fields`` /
    ``missing_recommended_fields``) and condition mappings. Never calls live
    APIs. Flags ``requires_live_read_only_fetch=True`` when category template
    has not been fetched so callers know upgrading providers is worthwhile.
    """

    name = "deterministic-fallback"

    def get_requirements(
        self,
        platform: str,
        category_id: str | None,
        item: Item,
    ) -> MarketplaceRequirements:
        if platform != Platform.EBAY:
            return MarketplaceRequirements(
                platform=platform,
                category_id=category_id,
                category_policy_source=self.name,
                data_freshness="unknown",
                requires_live_read_only_fetch=False,
                notes=[f"Platform '{platform}' is not yet implemented; returning empty requirements."],
                provider_kind=ProviderKind.DETERMINISTIC_FALLBACK,
                confidence_source=ConfidenceSource.HEURISTIC,
                is_deterministic_fallback=True,
                fallback_warning=DETERMINISTIC_FALLBACK_WARNING,
            )

        required = list(item.missing_required_fields or [])
        recommended = list(item.missing_recommended_fields or [])
        allowed_conditions = sorted(CONDITION_ID_TO_ENUM.keys())
        notes: list[str] = []
        freshness = "cached" if item.category_template_fetched else "unknown"
        if not item.category_template_fetched:
            notes.append("Category template has not been fetched; values reflect item-known gaps only.")
        confidence_source = (
            ConfidenceSource.CACHED_METADATA
            if item.category_template_fetched
            else ConfidenceSource.HEURISTIC
        )
        return MarketplaceRequirements(
            platform=Platform.EBAY,
            category_id=str(category_id or item.ebay_category_id or ""),
            required_aspects=required,
            recommended_aspects=recommended,
            allowed_aspect_values={},
            category_condition_policy="known" if item.category_template_fetched else "unknown",
            allowed_condition_ids=[str(cid) for cid in allowed_conditions],
            category_policy_source=self.name,
            data_freshness=freshness,
            requires_live_read_only_fetch=not item.category_template_fetched,
            missing_requirements_for_item=required,
            notes=notes,
            provider_kind=ProviderKind.DETERMINISTIC_FALLBACK,
            confidence_source=confidence_source,
            is_deterministic_fallback=True,
            fallback_warning=DETERMINISTIC_FALLBACK_WARNING,
        )


def get_marketplace_requirements(
    item: Item,
    platform: str = Platform.EBAY,
    category_id: str | None = None,
    provider: MarketplaceRequirementProvider | None = None,
) -> MarketplaceRequirements:
    provider = provider or DeterministicMarketplaceRequirementProvider()
    return provider.get_requirements(platform, category_id, item)
