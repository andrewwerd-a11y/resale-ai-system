"""Hardening pass tests — verify provider labeling, photo-type priority,
marketplace safety flags, and endpoint safety consistency."""
from __future__ import annotations

from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import run_deep_analysis_preview
from packages.intake.src.category_resolver import resolve_categories
from packages.intake.src.identity_scan import run_first_pass_identity
from packages.intake.src.marketplace_requirements import get_marketplace_requirements
from packages.intake.src.photo_types import PhotoMeta, parse_photo_inputs
from packages.intake.src.pipeline_types import (
    DETERMINISTIC_FALLBACK_WARNING,
    ConfidenceSource,
    ProviderKind,
)
from packages.intake.src.platform_translation import recommend_marketplaces
from packages.intake.src.quality_gate import evaluate_intake_quality, infer_present_photo_types


def _book(**kw) -> Item:
    base = dict(
        sku="BK-HARD",
        category_key="books",
        category_label="Books",
        title_final="A Test Book",
        brand="Penguin",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.85,
        image_paths=[
            "front-cover.jpg", "back-cover.jpg", "spine.jpg",
            "title-page.jpg", "copyright.jpg", "condition-flaws.jpg",
        ],
    )
    base.update(kw)
    return Item(**base)


# ── Focus 1: Provider labeling ─────────────────────────────────────────────────

def test_identity_scan_carries_provider_kind():
    result = run_first_pass_identity(_book())
    assert result.provider_kind == ProviderKind.DETERMINISTIC_FALLBACK
    assert result.confidence_source == ConfidenceSource.HEURISTIC
    assert result.is_deterministic_fallback is True
    assert DETERMINISTIC_FALLBACK_WARNING in result.fallback_warning


def test_deep_analysis_carries_provider_kind():
    result = run_deep_analysis_preview(_book())
    assert result.provider_kind == ProviderKind.DETERMINISTIC_FALLBACK
    assert result.confidence_source == ConfidenceSource.HEURISTIC
    assert result.is_deterministic_fallback is True
    assert DETERMINISTIC_FALLBACK_WARNING in result.fallback_warning


def test_category_resolution_carries_provider_kind():
    item = _book()
    identity = run_first_pass_identity(item)
    resolution = resolve_categories(item, identity=identity)
    assert resolution.provider_kind == ProviderKind.DETERMINISTIC_FALLBACK
    assert resolution.is_deterministic_fallback is True
    d = resolution.to_dict()
    assert d["is_deterministic_fallback"] is True
    assert d["fallback_warning"] == DETERMINISTIC_FALLBACK_WARNING


def test_marketplace_requirements_unfetched_is_heuristic():
    item = _book(category_template_fetched=False)
    reqs = get_marketplace_requirements(item)
    assert reqs.provider_kind == ProviderKind.DETERMINISTIC_FALLBACK
    assert reqs.confidence_source == ConfidenceSource.HEURISTIC
    assert reqs.is_deterministic_fallback is True


def test_marketplace_requirements_fetched_is_cached_metadata():
    item = _book(category_template_fetched=True)
    reqs = get_marketplace_requirements(item)
    assert reqs.confidence_source == ConfidenceSource.CACHED_METADATA


# ── Focus 2: Photo-type priority ───────────────────────────────────────────────

def test_user_labeled_photo_meta_boosts_quality_gate_inference():
    """A user-labeled 'spine' PhotoMeta should count for books quality gate."""
    item = Item(
        sku="BK-PHOTO",
        category_key="books",
        category_label="Books",
        image_paths=["img1.jpg"],  # anonymous filename — no keyword match
    )
    # Without label: anonymous file should not infer spine
    present_no_label = infer_present_photo_types(item)
    assert "spine" not in present_no_label

    from packages.intake.src.pipeline_types import PhotoType
    labeled_meta = [PhotoMeta(path="img1.jpg", photo_type=PhotoType.SPINE, confidence=1.0, user_labeled=True)]
    present_with_label = infer_present_photo_types(item, photo_meta=labeled_meta)
    assert "spine" in present_with_label


def test_evaluate_intake_quality_accepts_photo_meta():
    """evaluate_intake_quality should accept photo_meta and not crash."""
    item = _book()
    metas = parse_photo_inputs(item)
    result = evaluate_intake_quality(item, photo_meta=metas)
    assert result.intake_quality_status is not None


# ── Focus 3: Marketplace recommendation safety fields ─────────────────────────

def test_recommendation_always_requires_approval():
    item = _book()
    result = recommend_marketplaces(item, selection_mode="hybrid")
    for rec in result["recommendations"]:
        assert rec["publish_approval_required"] is True
        assert "operator_warning" in rec
        assert rec["operator_warning"]


def test_recommendation_publish_allowed_matches_draft():
    item = _book()
    result = recommend_marketplaces(item, selection_mode="hybrid")
    recs_by_platform = {r["platform"]: r for r in result["recommendations"]}
    drafts_by_platform = {d["platform"]: d for d in result["drafts"]}
    for platform, rec in recs_by_platform.items():
        draft = drafts_by_platform.get(platform)
        if draft:
            assert rec["publish_allowed"] == draft["publish_allowed"]


def test_recommendation_publish_recommended_conservative():
    """publish_recommended should be False for a blocked item."""
    item = _book(missing_required_fields=["brand_tag", "size_tag"])
    result = recommend_marketplaces(item, selection_mode="auto")
    for rec in result["recommendations"]:
        # blocked draft → publish_recommended must be False
        if not rec["publish_allowed"]:
            assert rec["publish_recommended"] is False


def test_marketplace_fit_recommended_separate_from_publish():
    """marketplace_fit_recommended is about channel fit; publish_recommended is about gates."""
    item = _book()
    result = recommend_marketplaces(item, selection_mode="hybrid")
    recs_by_platform = {r["platform"]: r for r in result["recommendations"]}
    ebay = recs_by_platform.get("ebay", {})
    # eBay has fit >= 0.6 for books so fit is recommended
    assert ebay.get("marketplace_fit_recommended") is True
    # But publish is still gated
    assert ebay.get("publish_approval_required") is True


# ── Focus 4: Safety flags on service outputs ──────────────────────────────────

def test_recommend_marketplaces_includes_safety_flags():
    item = _book()
    result = recommend_marketplaces(item, selection_mode="hybrid")
    assert result["read_only"] is True
    assert result["draft_only"] is True
    assert result["no_publish_performed"] is True
    assert result["manual_approval_required"] is True
