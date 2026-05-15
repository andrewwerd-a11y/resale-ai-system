from __future__ import annotations

from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.intake.src.category_resolver import (
    DeterministicCategoryResolver,
    resolve_categories,
)


def _item(**overrides) -> Item:
    base = dict(
        sku="CL-CAT",
        category_key="clothing",
        category_label="Clothing",
        title_final="Jacket",
        image_paths=["front.jpg"],
    )
    base.update(overrides)
    return Item(**base)


def test_resolver_uses_existing_ebay_category_when_present():
    item = _item(ebay_category_id="11450", ebay_category_name="Clothing")
    result = resolve_categories(item)
    assert any(c.category_id == "11450" for c in result.marketplace_candidates)
    top = next(c for c in result.marketplace_candidates if c.category_id == "11450")
    assert top.platform == Platform.EBAY
    assert top.recommended is True


def test_resolver_falls_back_to_family_hint():
    result = resolve_categories(_item(ebay_category_id=None))
    assert result.internal_family_candidates == ["clothing"]
    assert result.marketplace_candidates
    assert all(c.platform == Platform.EBAY for c in result.marketplace_candidates)


def test_resolver_returns_multiple_candidates_when_template_unfetched():
    item = _item(ebay_category_id="11450", category_template_fetched=False)
    result = resolve_categories(item)
    # Should not over-rank; assigned category gets <0.7 confidence when template missing.
    assigned = next(c for c in result.marketplace_candidates if c.category_id == "11450")
    assert assigned.condition_policy_known is False
    assert assigned.confidence <= 0.5


def test_resolver_notes_when_no_category():
    item = Item(sku="UNK", title_final="thing", image_paths=[])
    result = resolve_categories(item)
    assert any("manual" in note.lower() or "deterministic" in note.lower() for note in result.notes)


def test_provider_protocol_compliance():
    resolver = DeterministicCategoryResolver()
    result = resolver.resolve(_item())
    assert result.provider == "deterministic-fallback"
