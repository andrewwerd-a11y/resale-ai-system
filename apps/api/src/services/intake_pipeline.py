"""Staged intake pipeline orchestration service.

Composes the provider-agnostic intake modules into a single read-only,
draft-only view. Never mutates the database; never calls external paid APIs;
never publishes. Used by the new ``/api/items/{sku}/intake-pipeline-status``
endpoint and by correction-report v2 in slice 3.
"""
from __future__ import annotations

from apps.api.src.services.limited_evidence import (
    annotate_deep_analysis_for_limited_evidence,
    limited_evidence_state,
)
from apps.api.src.services.confidence_explanation import build_confidence_explanation
from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import (
    DeepAnalysisResult,
    run_deep_analysis_preview,
)
from packages.intake.src.category_resolver import (
    CategoryCandidate,
    CategoryResolution,
    resolve_categories,
)
from packages.intake.src.identity_scan import (
    IdentityScanResult,
    run_first_pass_identity,
)
from packages.intake.src.marketplace_requirements import (
    MarketplaceRequirements,
    get_marketplace_requirements,
)
from packages.intake.src.photo_types import (
    PhotoCoverageSummary,
    PhotoMeta,
    parse_photo_inputs,
    summarize_photo_coverage,
)
from packages.intake.src.pipeline_types import IntakePipelineStage
from packages.intake.src.quality_gate import (
    IntakeQualityResult,
    category_family_for_item,
    evaluate_intake_quality,
)
from apps.api.src.services.publish_readiness import evaluate_publish_readiness


def _select_top_candidate(resolution: CategoryResolution) -> CategoryCandidate | None:
    if not resolution.marketplace_candidates:
        return None
    return max(
        resolution.marketplace_candidates,
        key=lambda c: (c.recommended, c.confidence),
    )


def build_pipeline_snapshot(
    item: Item,
    *,
    platform: str = Platform.EBAY,
    user_context: str | None = None,
    run_deep_analysis: bool = False,
    allow_limited_evidence: bool = False,
    photo_meta: list[PhotoMeta] | None = None,
) -> dict:
    """Return a stage-by-stage snapshot of the intake pipeline.

    Read-only. The optional ``run_deep_analysis`` flag opts into running the
    deterministic deep-analysis preview as well — also read-only.
    """
    resolved_photo_meta = list(photo_meta) if photo_meta is not None else parse_photo_inputs(item)
    family = category_family_for_item(item)
    coverage: PhotoCoverageSummary = summarize_photo_coverage(item, family, resolved_photo_meta)
    quality: IntakeQualityResult = evaluate_intake_quality(item, photo_meta=resolved_photo_meta)
    identity: IdentityScanResult = run_first_pass_identity(item, user_context=user_context)
    resolution: CategoryResolution = resolve_categories(item, identity=identity)
    top_candidate = _select_top_candidate(resolution)
    requirements: MarketplaceRequirements = get_marketplace_requirements(
        item,
        platform=platform,
        category_id=(top_candidate.category_id if top_candidate else None),
    )
    readiness = evaluate_publish_readiness(item).as_dict()
    limited_state = limited_evidence_state(quality, allow_limited_evidence=allow_limited_evidence)
    confidence_fields = build_confidence_explanation(
        item,
        quality=quality,
        identity=identity,
        resolution=resolution,
        limited_evidence_used=bool(limited_state["limited_evidence_used"]),
    )
    deep_result: DeepAnalysisResult | None = None
    if run_deep_analysis and (quality.should_run_deep_analysis or limited_state["limited_evidence_used"]):
        deep_result = run_deep_analysis_preview(
            item,
            identity=identity,
            selected_category=top_candidate,
            marketplace_requirements=requirements,
            user_context=user_context,
            current_publish_blockers=readiness.get("blockers") or [],
            photo_meta=resolved_photo_meta,
        )
        deep_result = annotate_deep_analysis_for_limited_evidence(
            deep_result,
            quality=quality,
            allow_limited_evidence=allow_limited_evidence,
        )

    stages = {
        IntakePipelineStage.PHOTO_INTAKE: {
            "total_photos": coverage.total_photos,
            "present_photo_types": coverage.present_photo_types,
            "unknown_photo_count": coverage.unknown_photo_count,
        },
        IntakePipelineStage.FIRST_PASS_IDENTITY: identity.to_dict(),
        IntakePipelineStage.PHOTO_SUFFICIENCY: {
            "has_enough_photos": quality.has_enough_photos,
            "needs_more_photos_for_analysis": quality.needs_more_photos_for_analysis,
            "missing_required_photo_types": coverage.missing_required_photo_types,
            "missing_recommended_photo_types": coverage.missing_recommended_photo_types,
            "intake_quality_status": quality.intake_quality_status,
        },
        IntakePipelineStage.CATEGORY_RESOLUTION: resolution.to_dict(),
        IntakePipelineStage.MARKETPLACE_REQUIREMENTS: requirements.to_dict(),
        IntakePipelineStage.DEEP_ANALYSIS: deep_result.to_dict() if deep_result else None,
    }
    external_call_made = bool(deep_result and not deep_result.is_deterministic_fallback)
    return {
        "sku": item.sku,
        "platform": platform,
        "intake_quality": quality.as_dict(),
        "category_family": family,
        "photo_coverage": coverage.to_dict(),
        "stages": stages,
        "no_ebay_mutation_performed": True,
        "no_external_provider_called": not external_call_made,
        "no_publish_performed": True,
        "read_only": True,
        "draft_only": True,
        "manual_approval_required": True,
        **confidence_fields,
        **limited_state,
        "publish_approval_blocked": bool(quality.should_block_publish_approval or limited_state["limited_evidence_used"]),
    }
