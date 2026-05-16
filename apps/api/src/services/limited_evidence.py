from __future__ import annotations

from packages.intake.src.analysis_contract import DeepAnalysisResult
from packages.intake.src.quality_gate import IntakeQualityResult

LIMITED_EVIDENCE_OPERATOR_WARNING = (
    "This draft was generated with incomplete evidence. It is not publish-ready and cannot be "
    "approved/published until required evidence blockers are resolved."
)


def limited_evidence_state(
    quality: IntakeQualityResult,
    *,
    allow_limited_evidence: bool,
) -> dict:
    missing_required = list(quality.missing_required_photo_types or [])
    missing_recommended = list(quality.missing_recommended_photo_types or [])
    limited_used = bool(allow_limited_evidence and missing_required)
    return {
        "limited_evidence_mode": bool(allow_limited_evidence),
        "limited_evidence_used": limited_used,
        "draft_quality": "incomplete" if limited_used else "standard",
        "confidence_source": "limited_evidence" if limited_used else None,
        "missing_required_photo_types": missing_required,
        "missing_recommended_photo_types": missing_recommended,
        "operator_warning": LIMITED_EVIDENCE_OPERATOR_WARNING if limited_used else "",
        "can_generate_limited_evidence_draft": bool(missing_required),
        "limited_evidence_allowed_for_draft_only": bool(missing_required),
        "publish_still_blocked": bool(missing_required),
    }


def annotate_deep_analysis_for_limited_evidence(
    result: DeepAnalysisResult,
    *,
    quality: IntakeQualityResult,
    allow_limited_evidence: bool,
) -> DeepAnalysisResult:
    state = limited_evidence_state(quality, allow_limited_evidence=allow_limited_evidence)
    if not state["limited_evidence_used"]:
        return result
    result.confidence_source = "limited_evidence"
    result.needs_more_photos = True
    result.missing_photo_types = list(quality.missing_required_photo_types or [])
    result.should_require_manual_review = True
    result.should_block_publish_approval = True
    if LIMITED_EVIDENCE_OPERATOR_WARNING not in result.correction_summary:
        result.correction_summary = [LIMITED_EVIDENCE_OPERATOR_WARNING, *list(result.correction_summary or [])]
    return result


def limited_evidence_block_detail(quality: IntakeQualityResult, *, sku: str) -> dict:
    return {
        "code": "intake_quality_blocked",
        "sku": sku,
        "message": "Item needs required evidence before this preview can run without limited-evidence mode.",
        "next_action": quality.suggested_next_uploads[0] if quality.suggested_next_uploads else quality.reason,
        "intake_quality": quality.as_dict(),
        "limited_evidence_draft_available": bool(quality.missing_required_photo_types),
        "operator_warning": LIMITED_EVIDENCE_OPERATOR_WARNING,
    }
