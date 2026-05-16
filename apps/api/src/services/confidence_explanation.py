from __future__ import annotations

from packages.domain.src.entities.item import Item
from packages.intake.src.category_resolver import CategoryCandidate, CategoryResolution
from packages.intake.src.identity_scan import IdentityScanResult
from packages.intake.src.quality_gate import IntakeQualityResult

HIGH_EXTRACTION_THRESHOLD = 0.85
LOW_PHOTO_EVIDENCE_THRESHOLD = 0.55
EXTRACTION_CONFIDENCE_SOURCE = "phase1_vision_model_self_report"


def build_confidence_explanation(
    item: Item,
    *,
    quality: IntakeQualityResult,
    identity: IdentityScanResult | None = None,
    resolution: CategoryResolution | None = None,
    limited_evidence_used: bool = False,
) -> dict:
    extraction_confidence = _normalized(item.confidence_score)
    selected_category = _top_category_candidate(resolution)
    category_confidence = _normalized(getattr(selected_category, "confidence", None))
    photo_evidence_confidence = _photo_evidence_confidence(quality)
    operator_verified_identity = False
    operator_verified_category = False

    warnings: list[str] = []
    if (
        extraction_confidence is not None
        and extraction_confidence >= HIGH_EXTRACTION_THRESHOLD
        and photo_evidence_confidence < LOW_PHOTO_EVIDENCE_THRESHOLD
    ):
        warnings.append(
            "High extraction confidence does not mean category or publish readiness is confirmed."
        )
    if not operator_verified_category:
        warnings.append("Category has not been operator-confirmed.")
    if limited_evidence_used:
        warnings.append("Draft was generated with incomplete evidence.")

    if quality.needs_more_photos_for_analysis:
        explanation = (
            "Extraction confidence comes from the original vision pass, while photo evidence confidence "
            "reflects current required-photo coverage."
        )
    elif category_confidence is None:
        explanation = (
            "Extraction confidence is separate from category confidence. No category candidate has been "
            "confirmed yet."
        )
    else:
        explanation = (
            "Extraction confidence is the original model self-confidence. Category confidence and photo "
            "evidence confidence are reported separately for workflow decisions."
        )

    return {
        "extraction_confidence": extraction_confidence,
        "extraction_confidence_source": EXTRACTION_CONFIDENCE_SOURCE,
        "category_confidence": category_confidence,
        "category_confidence_source": _category_confidence_source(item, selected_category),
        "photo_evidence_confidence": photo_evidence_confidence,
        "confidence_explanation": explanation,
        "confidence_warnings": warnings,
        "operator_verified_identity": operator_verified_identity,
        "operator_verified_category": operator_verified_category,
    }


def _top_category_candidate(resolution: CategoryResolution | None) -> CategoryCandidate | None:
    if resolution is None or not resolution.marketplace_candidates:
        return None
    return max(
        resolution.marketplace_candidates,
        key=lambda candidate: (candidate.recommended, candidate.confidence),
    )


def _category_confidence_source(item: Item, selected_category: CategoryCandidate | None) -> str:
    if selected_category is None:
        return "no_category_candidate"
    if item.ebay_category_id and selected_category.category_id == str(item.ebay_category_id):
        return "existing_assignment_cached" if item.category_template_fetched else "existing_assignment_heuristic"
    if selected_category.recommended:
        return "recommended_candidate_heuristic"
    return "family_fallback_heuristic"


def _photo_evidence_confidence(quality: IntakeQualityResult) -> float:
    required = max(len(quality.required_photo_types), 1)
    recommended = max(len(quality.recommended_photo_types), 1)
    required_coverage = max(0.0, (required - len(quality.missing_required_photo_types)) / required)
    recommended_coverage = max(
        0.0,
        (recommended - len(quality.missing_recommended_photo_types)) / recommended,
    ) if quality.recommended_photo_types else 1.0
    score = 0.2 + (required_coverage * 0.6) + (recommended_coverage * 0.2)
    return round(min(1.0, max(0.0, score)), 3)


def _normalized(value: float | None) -> float | None:
    if value is None:
        return None
    return round(min(1.0, max(0.0, float(value))), 3)
