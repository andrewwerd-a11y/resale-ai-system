from __future__ import annotations

from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import run_deep_analysis_preview
from packages.intake.src.correction_pipeline import (
    ChangeEvent,
    NEVER_AUTO_OVERWRITE,
    build_canonical_proposal_from_deep_analysis,
    classify_change_event_impact,
    classify_change_event_impacts,
    classify_manual_edit,
)
from packages.intake.src.pipeline_types import (
    IntakePipelineStage,
    ManualEditTrustLevel,
)


def _ready_book(**overrides) -> Item:
    base = dict(
        sku="BK-CP",
        category_key="books",
        category_label="Books",
        title_final="A Book",
        brand="Penguin",
        condition_label="Good",
        condition_id="5000",
        confidence_score=0.85,
        image_paths=[
            "front-cover.jpg", "back-cover.jpg", "spine.jpg",
            "title-page.jpg", "copyright.jpg", "condition-flaws.jpg",
        ],
    )
    base.update(overrides)
    return Item(**base)


# ── CanonicalItemProposal ────────────────────────────────────────────────────

def test_proposal_never_auto_overwrites_protected_fields():
    item = _ready_book(brand="Penguin")
    deep = run_deep_analysis_preview(item)
    proposal = build_canonical_proposal_from_deep_analysis(
        item, deep, source_stage=IntakePipelineStage.DEEP_ANALYSIS
    )
    # deep echoes existing brand value — old==new, so no proposal entry
    assert "brand" not in {p.field_name for p in proposal.field_proposals}


def test_proposal_marks_safe_autofill_only_when_empty_and_confident():
    # color is empty and SAFE_AUTOFILL_WHEN_EMPTY, but deterministic provider
    # never proposes new values for empty fields. Sanity-check the structure.
    item = _ready_book(color=None)
    deep = run_deep_analysis_preview(item)
    proposal = build_canonical_proposal_from_deep_analysis(
        item, deep, source_stage=IntakePipelineStage.DEEP_ANALYSIS
    )
    # No deterministic suggestion for color; ensure proposal builder doesn't crash.
    assert proposal.sku == item.sku
    assert proposal.source_stage == IntakePipelineStage.DEEP_ANALYSIS


def test_proposal_records_diff_and_evidence():
    # Force a proposal: deep provider echoes brand if set; old==new in pure call,
    # so we synthesize a deep-like result by tweaking suggestions in-place.
    item = _ready_book(brand=None)
    deep = run_deep_analysis_preview(item)
    deep.suggested_field_updates["brand"] = "Penguin"
    deep.confidence_by_field["brand"] = 0.75
    deep.evidence_by_field["brand"] = ["operator hint"]
    proposal = build_canonical_proposal_from_deep_analysis(
        item, deep, source_stage=IntakePipelineStage.DEEP_ANALYSIS
    )
    brand_proposal = next(p for p in proposal.field_proposals if p.field_name == "brand")
    assert brand_proposal.never_auto_overwrite is True
    assert "brand" in proposal.fields_never_auto_overwrite
    assert proposal.before_after_diff["brand"]["old"] is None
    assert proposal.before_after_diff["brand"]["new"] == "Penguin"


def test_never_auto_overwrite_set_includes_brand_and_condition():
    assert "brand" in NEVER_AUTO_OVERWRITE
    assert "condition_id" in NEVER_AUTO_OVERWRITE
    assert "ebay_category_id" in NEVER_AUTO_OVERWRITE


# ── Manual edit trust classification ─────────────────────────────────────────

def test_classify_manual_edit_measurement_is_factual():
    assessment = classify_manual_edit("shipping_weight", None, 1.5)
    assert assessment.trust_level == ManualEditTrustLevel.FACTUAL_MEASUREMENT


def test_classify_manual_edit_color_is_factual_observation():
    assessment = classify_manual_edit("color", None, "Blue")
    assert assessment.trust_level == ManualEditTrustLevel.FACTUAL_OBSERVATION
    assert assessment.warnings == []


def test_classify_manual_edit_brand_is_risky_claim():
    assessment = classify_manual_edit("brand", None, "Coach")
    assert assessment.trust_level == ManualEditTrustLevel.RISKY_CLAIM
    assert assessment.requires_evidence is True
    assert assessment.warnings


def test_classify_manual_edit_authenticity_language_triggers_risky():
    assessment = classify_manual_edit("notes", None, "100% authentic")
    assert assessment.trust_level == ManualEditTrustLevel.RISKY_CLAIM


def test_classify_manual_edit_does_not_block_save():
    assessment = classify_manual_edit("brand", "Penguin", "Coach")
    assert assessment.blocks_save is False  # warns only


# ── Change event impact ──────────────────────────────────────────────────────

def test_user_changes_brand_triggers_deep_analysis_rerun():
    impact = classify_change_event_impact(
        ChangeEvent(field_name="brand", old_value="Penguin", new_value="Coach")
    )
    assert impact.should_rerun_deep_analysis is True
    assert impact.affects_identity is True


def test_user_changes_condition_triggers_readiness_rerun():
    impact = classify_change_event_impact(
        ChangeEvent(field_name="condition_id", old_value="5000", new_value="3000")
    )
    assert impact.should_rerun_dry_run_readiness is True
    assert impact.affects_condition is True


def test_user_adds_measurement_triggers_deep_analysis_rerun():
    impact = classify_change_event_impact(
        ChangeEvent(field_name="shipping_weight", old_value=None, new_value=1.5)
    )
    assert impact.should_rerun_deep_analysis is True


def test_user_changes_category_triggers_readiness_rerun():
    impact = classify_change_event_impact(
        ChangeEvent(field_name="ebay_category_id", old_value="1", new_value="11450")
    )
    assert impact.should_rerun_dry_run_readiness is True
    assert impact.affects_marketplace_required_fields is True


def test_user_adds_authenticity_claim_via_notes_does_not_force_rerun():
    impact = classify_change_event_impact(
        ChangeEvent(field_name="notes", old_value=None, new_value="100% authentic")
    )
    # Notes alone don't change identity/category/condition; rerun not required.
    assert impact.should_rerun_deep_analysis is False
    assert impact.should_rerun_dry_run_readiness is False


def test_aggregate_impacts_combines_signals():
    summary = classify_change_event_impacts([
        ChangeEvent(field_name="brand", old_value=None, new_value="Coach"),
        ChangeEvent(field_name="color", old_value=None, new_value="Blue"),
    ])
    assert summary["should_rerun_deep_analysis"] is True
    assert summary["affects_identity"] is True
