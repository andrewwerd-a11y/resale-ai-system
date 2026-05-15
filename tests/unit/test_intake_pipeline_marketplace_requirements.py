from __future__ import annotations

from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.intake.src.marketplace_requirements import (
    DeterministicMarketplaceRequirementProvider,
    get_marketplace_requirements,
)


def _item(**overrides) -> Item:
    base = dict(sku="X", title_final="Thing", image_paths=["a.jpg"])
    base.update(overrides)
    return Item(**base)


def test_requirements_for_ebay_with_no_template_flags_live_fetch():
    item = _item(category_template_fetched=False)
    req = get_marketplace_requirements(item, platform=Platform.EBAY)
    assert req.platform == Platform.EBAY
    assert req.data_freshness == "unknown"
    assert req.requires_live_read_only_fetch is True
    assert req.category_condition_policy == "unknown"


def test_requirements_when_template_fetched_marks_cached():
    item = _item(category_template_fetched=True, ebay_category_id="11450")
    req = get_marketplace_requirements(item, platform=Platform.EBAY)
    assert req.data_freshness == "cached"
    assert req.requires_live_read_only_fetch is False
    assert req.category_condition_policy == "known"


def test_requirements_surfaces_known_item_gaps():
    item = _item(
        missing_required_fields=["Brand", "Size"],
        missing_recommended_fields=["Color"],
    )
    req = get_marketplace_requirements(item, platform=Platform.EBAY)
    assert "Brand" in req.required_aspects
    assert "Color" in req.recommended_aspects
    assert "Brand" in req.missing_requirements_for_item


def test_requirements_unsupported_platform_returns_empty():
    req = get_marketplace_requirements(_item(), platform="poshmark")
    assert req.platform == "poshmark"
    assert req.required_aspects == []


def test_provider_is_deterministic():
    provider = DeterministicMarketplaceRequirementProvider()
    req = provider.get_requirements(Platform.EBAY, "11450", _item())
    assert req.category_policy_source == "deterministic-fallback"
