from __future__ import annotations

from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.intake.src.platform_translation import (
    EbayDeterministicTranslator,
    SUPPORTED_PLATFORMS,
    get_translator,
    recommend_marketplaces,
    translate_item_for_platforms,
)


def _item(**overrides) -> Item:
    base = dict(
        sku="X-TR",
        title_final="Thing",
        ebay_category_id="11450",
        ebay_category_name="Clothing",
        condition_id="3000",
        condition_label="Used",
        list_price=20.0,
        image_paths=["a.jpg"],
        category_key="clothing",
    )
    base.update(overrides)
    return Item(**base)


def test_ebay_translator_returns_draft_shape():
    translator = EbayDeterministicTranslator()
    draft = translator.translate(_item())
    assert draft.platform == Platform.EBAY
    assert draft.platform_supported is True
    assert draft.category_id == "11450"
    assert draft.condition_id == "3000"
    assert draft.manual_review_required is True


def test_ebay_translator_blocked_when_required_fields_missing():
    item = _item(missing_required_fields=["Brand", "Size"])
    draft = EbayDeterministicTranslator().translate(item)
    assert draft.publish_allowed is False
    assert "Brand" in draft.missing_platform_fields


def test_unsupported_platform_returns_unsupported_draft():
    translator = get_translator("mercari")
    draft = translator.translate(_item())
    assert draft.platform_supported is False
    assert draft.publish_allowed is False


def test_translate_item_for_platforms_defaults_to_ebay():
    drafts = translate_item_for_platforms(_item())
    assert len(drafts) == 1
    assert drafts[0].platform == Platform.EBAY


def test_translate_item_for_platforms_multi_platform():
    drafts = translate_item_for_platforms(_item(), platforms=SUPPORTED_PLATFORMS)
    assert {d.platform for d in drafts} == set(SUPPORTED_PLATFORMS)
    # Only eBay is supported today.
    assert sum(1 for d in drafts if d.platform_supported) == 1


def test_recommend_marketplaces_hybrid_picks_high_fit_supported():
    rec = recommend_marketplaces(_item(), selection_mode="hybrid")
    ebay_rec = next(r for r in rec["recommendations"] if r["platform"] == Platform.EBAY)
    # eBay supported with clothing fit 0.7 — auto-recommended only if not blocked.
    assert ebay_rec["expected_fit_score"] >= 0.6


def test_recommend_marketplaces_manual_mode_never_auto_recommends():
    rec = recommend_marketplaces(_item(), selection_mode="manual")
    assert all(r["recommended"] is False for r in rec["recommendations"])


def test_recommend_marketplaces_unsupported_platforms_flagged():
    rec = recommend_marketplaces(_item(), selection_mode="hybrid")
    poshmark = next(r for r in rec["recommendations"] if r["platform"] == "poshmark")
    assert "platform_not_implemented" in poshmark["risk_flags"]
    assert poshmark["recommended"] is False


def test_recommend_marketplaces_high_value_flag():
    rec = recommend_marketplaces(_item(estimated_price=150), selection_mode="auto")
    ebay = next(r for r in rec["recommendations"] if r["platform"] == Platform.EBAY)
    assert "high_value_estimate" in ebay["risk_flags"]


def test_recommend_marketplaces_response_is_read_only():
    rec = recommend_marketplaces(_item())
    assert rec["no_ebay_mutation_performed"] is True
    assert rec["no_external_provider_called"] is True
