"""Correction Report v2.

Extends the v1 correction report with the staged-intake pipeline results from
slice 2 (first-pass identity, category candidates, marketplace requirements,
deep analysis preview) and a canonical-proposal layer. Read-only. Never
publishes. Reuses existing publish_readiness / publish_compatibility for
publish-side blockers.
"""
from __future__ import annotations

from apps.api.src.services.intake_correction_report import build_intake_correction_report
from apps.api.src.services.intake_pipeline import build_pipeline_snapshot
from apps.api.src.services.publish_compatibility import evaluate_publish_compatibility
from apps.api.src.services.publish_readiness import evaluate_publish_readiness
from packages.core.src.constants import Platform
from packages.domain.src.entities.item import Item
from packages.intake.src.analysis_contract import (
    DeepAnalysisResult,
    run_deep_analysis_preview,
)
from packages.intake.src.category_resolver import resolve_categories
from packages.intake.src.correction_pipeline import (
    build_canonical_proposal_from_deep_analysis,
)
from packages.intake.src.identity_scan import run_first_pass_identity
from packages.intake.src.marketplace_requirements import get_marketplace_requirements
from packages.intake.src.pipeline_types import IntakePipelineStage
from packages.intake.src.quality_gate import evaluate_intake_quality


# Action groups used to organize the next-action sequence. Keep stable so UI
# can sort/render reliably.
GROUP_NEEDS_MORE_PHOTOS = "Needs more photos"
GROUP_NEEDS_USER_CONTEXT = "Needs user context"
GROUP_NEEDS_CATEGORY_REVIEW = "Needs category review"
GROUP_NEEDS_CONDITION_REVIEW = "Needs condition review"
GROUP_NEEDS_AUTH_REVIEW = "Needs authenticity/high-value review"
GROUP_FIX_MALFORMED_CONDITION = "Fix malformed condition"
GROUP_FIX_INVALID_ASPECTS = "Fix invalid aspects"
GROUP_FETCH_CATEGORY_POLICY = "Fetch category policy"
GROUP_HOST_PHOTOS = "Host photos"
GROUP_APPROVE_OUT_OF_REVIEW = "Approve/move out of review"
GROUP_READY_DEEP_ANALYSIS = "Ready for deep analysis"
GROUP_READY_PLATFORM_TRANSLATION = "Ready for platform translation"
GROUP_READY_PUBLISH_DRY_RUN = "Ready for publish dry-run"


def build_correction_report_v2(
    item: Item,
    *,
    platform: str = Platform.EBAY,
    user_context: str | None = None,
) -> dict:
    quality = evaluate_intake_quality(item)
    identity = run_first_pass_identity(item, user_context=user_context)
    resolution = resolve_categories(item, identity=identity)
    selected = None
    if resolution.marketplace_candidates:
        selected = max(
            resolution.marketplace_candidates,
            key=lambda c: (c.recommended, c.confidence),
        )
    requirements = get_marketplace_requirements(
        item,
        platform=platform,
        category_id=(selected.category_id if selected else None),
    )
    readiness = evaluate_publish_readiness(item).as_dict()
    compatibility = evaluate_publish_compatibility(item, strict_condition_policy=True)

    deep: DeepAnalysisResult | None = None
    if quality.should_run_deep_analysis:
        deep = run_deep_analysis_preview(
            item,
            identity=identity,
            selected_category=selected,
            marketplace_requirements=requirements,
            user_context=user_context,
            current_publish_blockers=readiness.get("blockers") or [],
        )

    proposal = None
    if deep:
        proposal = build_canonical_proposal_from_deep_analysis(
            item,
            deep,
            source_stage=IntakePipelineStage.DEEP_ANALYSIS,
        ).to_dict()

    grouped_actions = _build_grouped_actions(
        quality=quality.as_dict(),
        identity=identity.to_dict(),
        resolution=resolution.to_dict(),
        requirements=requirements.to_dict(),
        deep=deep.to_dict() if deep else None,
        readiness=readiness,
        compatibility=compatibility,
    )

    # Top-level gates the UI needs to know about.
    should_run_deep = quality.should_run_deep_analysis
    human_review_required = bool(
        quality.intake_quality_status != "READY_FOR_DEEP_ANALYSIS"
        or (deep and deep.should_require_manual_review)
    )
    platform_translation_allowed = bool(
        quality.should_run_deep_analysis
        and readiness.get("ready") is True
        and compatibility.get("ready") is True
        and not (deep and deep.should_block_publish_approval)
    )
    publish_approval_blocked = bool(
        quality.should_block_publish_approval
        or not readiness.get("ready")
        or not compatibility.get("ready")
        or (deep and deep.should_block_publish_approval)
    )

    v1 = build_intake_correction_report(item)

    return {
        "sku": item.sku,
        "schema_version": "v2",
        "platform": platform,
        "current_item_status": item.status,
        "intake_quality": quality.as_dict(),
        "first_pass_identity": identity.to_dict(),
        "category_candidates": resolution.to_dict(),
        "marketplace_requirements": requirements.to_dict(),
        "missing_photo_checklist": quality.missing_photo_types,
        "missing_user_context": _missing_user_context(item, identity.to_dict()),
        "malformed_data": _malformed_data(item),
        "publish_readiness": {
            "ready": readiness.get("ready"),
            "blockers": readiness.get("blockers") or [],
            "required_actions": readiness.get("required_actions") or [],
        },
        "publish_compatibility": {
            "ready": compatibility.get("ready"),
            "blockers": compatibility.get("blockers") or [],
            "required_actions": compatibility.get("required_actions") or [],
        },
        "deep_analysis_preview": deep.to_dict() if deep else None,
        "proposed_corrections": proposal,
        "risk_flags": sorted(set(
            list(identity.risk_flags)
            + (deep.publish_risk_flags if deep else [])
            + (deep.authenticity_flags if deep else [])
            + (deep.high_value_flags if deep else [])
        )),
        "grouped_next_actions": grouped_actions,
        "next_action_sequence": v1["next_action_sequence"],
        "should_run_deep_analysis": should_run_deep,
        "human_review_required": human_review_required,
        "platform_translation_allowed": platform_translation_allowed,
        "publish_approval_blocked": publish_approval_blocked,
        "no_ebay_mutation_performed": True,
        "no_external_provider_called": True,
    }


def _missing_user_context(item: Item, identity: dict) -> list[str]:
    out: list[str] = []
    if not item.notes:
        out.append("No operator notes provided.")
    if identity.get("decision") == "NEEDS_USER_CONTEXT":
        out.append("Identity scan flagged a need for user context.")
    return out


def _malformed_data(item: Item) -> list[str]:
    """Detect obviously malformed canonical values seen in past previews."""
    from packages.ebay.src.condition_mapping import CONDITION_ID_TO_ENUM

    malformed: list[str] = []
    cid = (item.condition_id or "").strip()
    if cid:
        cid_clean = cid.strip("[]() \"'")
        if (
            cid_clean != cid
            or "," in cid
            or any(ch.isalpha() for ch in cid_clean)
        ):
            malformed.append(f"condition_id contains malformed characters: {cid!r}")
        elif cid_clean and cid_clean not in {str(k) for k in CONDITION_ID_TO_ENUM.keys()}:
            malformed.append(f"condition_id {cid_clean!r} is not in allowed list.")
    return malformed


def _build_grouped_actions(
    *,
    quality: dict,
    identity: dict,
    resolution: dict,
    requirements: dict,
    deep: dict | None,
    readiness: dict,
    compatibility: dict,
) -> list[dict]:
    groups: list[dict] = []

    def add(group: str, action: str) -> None:
        groups.append({"group": group, "action": action})

    if quality.get("missing_photo_types"):
        for label in quality["missing_photo_types"]:
            add(GROUP_NEEDS_MORE_PHOTOS, f"Upload {label}")

    if identity.get("decision") == "NEEDS_USER_CONTEXT":
        add(GROUP_NEEDS_USER_CONTEXT, "Operator must add notes or context.")

    status = quality.get("intake_quality_status")
    if status == "NEEDS_CATEGORY_REVIEW":
        add(GROUP_NEEDS_CATEGORY_REVIEW, "Confirm or select the item category.")
    if status == "NEEDS_CONDITION_REVIEW":
        add(GROUP_NEEDS_CONDITION_REVIEW, "Add condition notes, defects, or condition data.")
    if status == "NEEDS_AUTHENTICITY_REVIEW":
        add(GROUP_NEEDS_AUTH_REVIEW, "Complete manual authenticity / high-value review.")

    if requirements.get("requires_live_read_only_fetch"):
        add(GROUP_FETCH_CATEGORY_POLICY, "Fetch eBay category policy/template for this category.")

    for action in readiness.get("required_actions") or []:
        text = str(action).lower()
        if "condition" in text and ("invalid" in text or "malformed" in text):
            add(GROUP_FIX_MALFORMED_CONDITION, str(action))
        elif "aspect" in text:
            add(GROUP_FIX_INVALID_ASPECTS, str(action))
        elif "photo" in text or "image" in text or "host" in text:
            add(GROUP_HOST_PHOTOS, str(action))
        elif "approve" in text or "review" in text:
            add(GROUP_APPROVE_OUT_OF_REVIEW, str(action))
        else:
            add("Publish readiness", str(action))

    for action in compatibility.get("required_actions") or []:
        add("Publish compatibility", str(action))

    if deep is None and status == "READY_FOR_DEEP_ANALYSIS":
        add(GROUP_READY_DEEP_ANALYSIS, "Run deep analysis preview.")
    elif deep is not None and not deep.get("should_block_publish_approval"):
        if readiness.get("ready"):
            add(GROUP_READY_PLATFORM_TRANSLATION, "Generate platform drafts for review.")
            add(GROUP_READY_PUBLISH_DRY_RUN, "Run publish dry-run before approval.")

    # Dedup while preserving order.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for entry in groups:
        key = (entry["group"], entry["action"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped
