from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from collections.abc import Callable
from typing import Protocol

from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus, Platform
from packages.logging.src.audit_log import AuditLog
from sqlmodel import Session, select

from apps.api.src.services.publish_repair import classify_publish_failure
from apps.api.src.services.publish_repair import get_publish_repair_blocker
from packages.data.src.models.item_record import ItemRecord
from packages.data.src.models.publish_repair_decision_record import PublishRepairDecisionRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.condition_mapping import condition_id_to_inventory_enum

REMEDIATION_TYPE = "refresh_existing_unpublished_offer"
REQUIRED_TYPED_CONFIRMATION = "REFRESH UNPUBLISHED OFFER ONLY"
SUPERSEDE_ACTION_TYPE = "supersede_repair_plan_after_refresh"
SUPERSEDE_TYPED_CONFIRMATION = "RESOLVE REPAIR PLAN AFTER REFRESH ONLY"
REPLACEMENT_BLOCKING_ERROR_CODE = "requires_publish_decision_after_refresh"
PUBLISH_DECISION_ACTION_TYPE = "publish_refreshed_unpublished_offer"
PUBLISH_DECISION_TYPED_CONFIRMATION = "PUBLISH REFRESHED UNPUBLISHED OFFER ONLY"
PREVIEW_PLACEHOLDER_VALUES = {
    "preview-fulfillment-policy",
    "preview-payment-policy",
    "preview-return-policy",
    "preview-location",
}


class StaleOfferRemediationExecutor(Protocol):
    """Test-only executor for previewed stale-offer remediation calls."""

    def put_inventory_item(self, sku: str, payload: dict) -> dict:
        ...

    def put_offer(self, offer_id: str, payload: dict) -> dict:
        ...


class PublishDecisionExecutor(Protocol):
    """Minimal publisher for an existing eBay offer."""

    def publish_existing_offer(self, offer_id: str, sku: str):
        ...


def build_stale_offer_remediation_approval_preview(diagnostics: dict) -> dict:
    """Build the read-only approval template for a future stale-offer refresh."""
    sku = str(diagnostics.get("sku") or "").strip().upper()
    draft = diagnostics.get("stale_offer_remediation_draft") or {}
    refusal_reasons = _eligibility_refusals(
        sku=sku,
        diagnostics=diagnostics,
        draft=draft,
        operator_approved=True,
        remediation_type=REMEDIATION_TYPE,
        publish_after_remediation=False,
    )
    if not draft:
        refusal_reasons.append(
            {
                "code": "missing_remediation_draft",
                "message": "Publish diagnostics did not include a stale-offer remediation draft.",
            }
        )
    if not draft.get("intended_inventory_item_payload_preview") or not draft.get("intended_offer_payload_preview"):
        refusal_reasons.append(
            {
                "code": "missing_payload_preview",
                "message": "Remediation payload previews are required before approval can be prepared.",
            }
        )
    payload_hash = build_remediation_payload_hash(draft) if draft else ""
    if not payload_hash or payload_hash == build_remediation_payload_hash({}):
        refusal_reasons.append(
            {
                "code": "payload_hash_unavailable",
                "message": "Remediation payload hash could not be built.",
            }
        )

    eligible = not refusal_reasons
    approval_template = _approval_template(
        sku=sku,
        diagnostics=diagnostics,
        draft=draft,
        payload_hash=payload_hash,
    )
    return {
        "sku": sku,
        "eligible_for_approval_preview": eligible,
        "remediation_type": REMEDIATION_TYPE,
        "approval_required": True,
        "typed_confirmation_required": REQUIRED_TYPED_CONFIRMATION,
        "live_execution_enabled": False,
        "no_mutation_performed": True,
        "publish_after_remediation": False,
        "safe_to_execute_now": False,
        "reason": "" if eligible else (refusal_reasons[0]["message"] if refusal_reasons else "Not eligible for approval preview."),
        "blockers": refusal_reasons,
        "local_item_summary": {
            "status": diagnostics.get("local_status") or "",
            "category_id": diagnostics.get("local_category_id") or "",
            "category_name": diagnostics.get("local_category_name") or "",
            "condition_id": diagnostics.get("local_condition_id") or "",
            "inventory_condition_enum": diagnostics.get("local_inventory_condition_enum") or "",
            "offer_id": diagnostics.get("offer_id") or "",
            "listing_id": diagnostics.get("listing_id") or "",
            "planned_action": diagnostics.get("planned_action") or "",
            "existing_offer_id_detected": bool(diagnostics.get("existing_offer_id_detected")),
        },
        "repair_queue_summary": {
            "blocked_by_repair_queue": bool(diagnostics.get("blocked_by_repair_queue")),
            "repair_plan_id": diagnostics.get("repair_plan_id") or "",
            "latest_publish_attempt_id": diagnostics.get("latest_publish_attempt_id") or "",
            "repair_status": diagnostics.get("repair_status") or {},
            "retry_allowed": bool(diagnostics.get("retry_allowed")),
            "classified_error_code": diagnostics.get("classified_error_code") or "",
        },
        "remediation_draft_summary": {
            "status": draft.get("status") or "",
            "safe_to_preview": bool(draft.get("safe_to_preview")),
            "actionable": bool(draft.get("actionable")),
            "safe_to_execute": bool(draft.get("safe_to_execute")),
            "offer_id": draft.get("offer_id") or "",
            "listing_id": draft.get("listing_id") or "",
            "offer_status": draft.get("offer_status") or "",
            "category_id": draft.get("category_id") or "",
            "category_name": draft.get("category_name") or "",
            "condition_id": draft.get("condition_id") or "",
            "inventory_condition_enum": draft.get("inventory_condition_enum") or "",
            "live_policy_result": draft.get("live_policy_result") or {},
            "stale_offer_reasoning": draft.get("stale_offer_reasoning") or "",
            "refusal_reasons": draft.get("refusal_reasons") or [],
            "call_sequence_preview": deepcopy(draft.get("intended_call_sequence_preview") or []),
        },
        "payload_hash": payload_hash,
        "required_approval_fields_template": approval_template,
        "next_step_warning": "This preview does not publish, does not refresh eBay, and does not clear the repair queue.",
        "live_readonly_summary": {
            "requested": bool(diagnostics.get("live_readonly_requested")),
            "performed": bool(diagnostics.get("live_readonly_performed")),
            "methods_called": diagnostics.get("live_readonly_methods_called") or [],
            "unavailable": diagnostics.get("live_readonly_unavailable") or [],
            "errors": diagnostics.get("live_readonly_errors") or [],
        },
    }


def render_stale_offer_remediation_approval_packet(
    approval_preview: dict,
    *,
    generated_at: str | None = None,
) -> str:
    """Render a durable Markdown approval packet from a read-only preview."""
    sku = str(approval_preview.get("sku") or "").strip().upper()
    if not sku:
        raise ValueError("sku is required to render a stale-offer remediation approval packet")
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    local = approval_preview.get("local_item_summary") or {}
    repair = approval_preview.get("repair_queue_summary") or {}
    draft = approval_preview.get("remediation_draft_summary") or {}
    live = approval_preview.get("live_readonly_summary") or {}
    live_policy = draft.get("live_policy_result") or {}
    approval_template = approval_preview.get("required_approval_fields_template") or {}
    blockers = approval_preview.get("blockers") or []
    call_sequence = draft.get("call_sequence_preview") or []

    lines = [
        f"# Stale Offer Remediation Approval Packet - {sku}",
        "",
        f"Generated: {generated}",
        "",
        "## Safety Statement",
        "- Read-only approval packet.",
        "- No publish performed.",
        "- No eBay refresh performed.",
        "- No repair queue clear performed.",
        "- No category/condition change performed.",
        "",
        "## Item Summary",
        f"- SKU: {sku}",
        f"- Title: {local.get('title') or ''}",
        f"- Local status: {local.get('status') or ''}",
        f"- Category ID/name: {local.get('category_id') or ''} / {local.get('category_name') or ''}",
        f"- Condition ID/enum: {local.get('condition_id') or ''} / {local.get('inventory_condition_enum') or ''}",
        f"- Offer ID: {local.get('offer_id') or ''}",
        f"- Listing ID: {local.get('listing_id') or ''}",
        f"- Planned action: {local.get('planned_action') or ''}",
        "",
        "## Repair Queue Summary",
        f"- Repair plan ID: {repair.get('repair_plan_id') or ''}",
        f"- Latest publish attempt ID: {repair.get('latest_publish_attempt_id') or ''}",
        f"- Classified error: {repair.get('classified_error_code') or ''}",
        f"- Retry allowed: {_json_bool(repair.get('retry_allowed'))}",
        f"- Blocked by repair queue: {_json_bool(repair.get('blocked_by_repair_queue'))}",
        f"- Repair status: `{json.dumps(repair.get('repair_status') or {}, sort_keys=True)}`",
        "",
        "## Live Read-Only Diagnostics Summary",
        f"- Live read-only requested: {_json_bool(live.get('requested'))}",
        f"- Live read-only performed: {_json_bool(live.get('performed'))}",
        f"- Methods called: {', '.join(str(value) for value in live.get('methods_called') or [])}",
        f"- Offer status: {draft.get('offer_status') or ''}",
        f"- Inventory condition: {draft.get('inventory_condition_enum') or local.get('inventory_condition_enum') or ''}",
        f"- Category policy source: {live_policy.get('source') or ''}",
        f"- Live policy allows condition {local.get('condition_id') or draft.get('condition_id') or ''}: {_json_bool(live_policy.get('live_policy_allows_condition'))}",
        f"- Read-only diagnostic warnings/unavailable: `{json.dumps(live.get('unavailable') or [], sort_keys=True)}`",
        f"- Read-only diagnostic errors: `{json.dumps(live.get('errors') or [], sort_keys=True)}`",
        "",
        "## Remediation Draft Summary",
        f"- Remediation type: {approval_preview.get('remediation_type') or ''}",
        f"- Eligible for approval preview: {_json_bool(approval_preview.get('eligible_for_approval_preview'))}",
        f"- Payload hash: {approval_preview.get('payload_hash') or ''}",
        f"- Publish after remediation: {_json_bool(approval_preview.get('publish_after_remediation'))}",
        f"- Live execution enabled: {_json_bool(approval_preview.get('live_execution_enabled'))}",
        f"- Safe to execute now: {_json_bool(approval_preview.get('safe_to_execute_now'))}",
        "",
        "### Call Sequence",
    ]
    if call_sequence:
        for step in call_sequence:
            endpoint = step.get("endpoint") or "(stop)"
            note = f" - {step.get('note')}" if step.get("note") else ""
            lines.append(
                f"{step.get('order')}. {step.get('method')} {endpoint} "
                f"(preview_only={_json_bool(step.get('preview_only'))}, mutation_performed={_json_bool(step.get('mutation_performed'))}){note}"
            )
    else:
        lines.append("- No call sequence is available because the draft is not previewable.")

    lines.extend(
        [
            "",
            "## Approval Preview Blockers",
            *(f"- {reason.get('code')}: {reason.get('message')}" for reason in blockers),
            *([] if blockers else ["- None"]),
            "",
            "## Required Approval Template",
            "```json",
            json.dumps(approval_template, indent=2, sort_keys=True),
            "```",
            "",
            "## Explicit Warning",
            (
                "This packet does not authorize publish. A future separate live remediation phase would still need "
                "a final preflight recheck and explicit operator approval. A later separate publish decision would "
                "still be required after remediation."
            ),
            "",
            approval_preview.get("next_step_warning")
            or "This preview does not publish, does not refresh eBay, and does not clear the repair queue.",
            "",
        ]
    )
    return "\n".join(lines)


def execute_refresh_existing_unpublished_offer(
    *,
    sku: str,
    diagnostics: dict,
    operator_approved: bool,
    remediation_type: str = REMEDIATION_TYPE,
    publish_after_remediation: bool = False,
    execute_live: bool = False,
    live_remediation_enabled: bool = False,
    approval_request: dict | None = None,
    preflight_diagnostics: dict | None = None,
    post_refresh_diagnostics_provider: Callable[[], dict] | None = None,
    executor: StaleOfferRemediationExecutor | None = None,
) -> dict:
    """Recheck and mock-execute a stale unpublished-offer remediation draft.

    This service intentionally has no live eBay client dependency. Without an
    injected test executor, it always fails closed with live execution disabled.
    """
    normalized_sku = str(sku or "").strip().upper()
    effective_diagnostics = preflight_diagnostics or diagnostics
    draft = effective_diagnostics.get("stale_offer_remediation_draft") or {}
    refusal_reasons = _eligibility_refusals(
        sku=normalized_sku,
        diagnostics=effective_diagnostics,
        draft=draft,
        operator_approved=operator_approved,
        remediation_type=remediation_type,
        publish_after_remediation=publish_after_remediation,
    )
    approval_refusals = _approval_refusals(
        approval_request=approval_request,
        sku=normalized_sku,
        diagnostics=effective_diagnostics,
        draft=draft,
        remediation_type=remediation_type,
        publish_after_remediation=publish_after_remediation,
        require_approval_request=bool(execute_live or approval_request),
    )
    base = _base_result(
        sku=normalized_sku,
        diagnostics=effective_diagnostics,
        draft=draft,
        operator_approved=operator_approved,
        publish_after_remediation=publish_after_remediation,
        live_remediation_enabled=live_remediation_enabled,
        approval_request=approval_request,
        preflight_recheck_performed=preflight_diagnostics is not None,
        executor=executor,
    )
    all_refusals = refusal_reasons + approval_refusals

    if execute_live:
        if not live_remediation_enabled:
            return base | {
                "code": "live_execution_disabled",
                "execution_status": "live_execution_disabled",
                "refusal_reasons": all_refusals
                + [
                    {
                        "code": "live_execution_disabled",
                        "message": "Live stale-offer remediation execution is disabled by feature gate.",
                    }
                ],
                "next_recommended_action": "Keep using preview/mock remediation. Live remediation requires a later approved phase.",
            }
        return base | {
            "code": "live_execution_disabled",
            "execution_status": "live_execution_disabled",
            "refusal_reasons": all_refusals
            + [
                {
                    "code": "live_execution_disabled",
                    "message": "Live stale-offer remediation has no real executor in this phase.",
                }
            ],
            "next_recommended_action": "Use mock-only remediation tests or preview diagnostics; live execution requires a later approved phase.",
        }

    if all_refusals:
        return base | {
            "execution_status": "blocked",
            "refusal_reasons": all_refusals,
            "next_recommended_action": "Resolve the refusal reasons and rerun publish diagnostics before any remediation.",
        }

    if executor is None:
        return base | {
            "code": "live_execution_disabled",
            "execution_status": "live_execution_disabled",
            "refusal_reasons": [
                {
                    "code": "live_execution_disabled",
                    "message": "No mock executor was provided; live stale-offer remediation execution is disabled in this phase.",
                }
            ],
            "next_recommended_action": "Review the preview only. Live remediation requires a later approved phase.",
        }

    inventory_payload = deepcopy(base["inventory_payload_preview"])
    offer_payload = deepcopy(base["offer_payload_preview"])
    inventory_result = executor.put_inventory_item(normalized_sku, inventory_payload)
    offer_result = executor.put_offer(base["offer_id"], offer_payload)
    post_refresh_diagnostics = post_refresh_diagnostics_provider() if post_refresh_diagnostics_provider else {}
    return base | {
        "execution_status": "mock_executed",
        "mode": "mock_only",
        "no_mutation_performed": False,
        "no_live_mutation_performed": True,
        "real_ebay_mutation_performed": False,
        "mocked_mutation_performed": True,
        "refusal_reasons": [],
        "mock_results": {
            "put_inventory_item": inventory_result or {},
            "put_offer": offer_result or {},
        },
        "post_refresh_readonly_diagnostics": post_refresh_diagnostics,
        "post_refresh_readonly_diagnostics_performed": bool(post_refresh_diagnostics),
        "next_recommended_action": (
            "Run publish diagnostics/readiness again. If the repair queue is explicitly resolved and preview is clean, "
            "perform a separately approved one-SKU publish retry."
        ),
    }


def execute_approved_refresh_existing_unpublished_offer(
    *,
    sku: str,
    diagnostics: dict,
    approval_request: dict,
    executor: StaleOfferRemediationExecutor | None,
    live_remediation_enabled: bool,
    post_refresh_diagnostics_provider: Callable[[], dict] | None = None,
) -> dict:
    """Execute the narrowly approved stale-offer refresh.

    This path is live-capable only when the caller supplies an executor after
    all route-level live guards pass. It still fails closed unless the current
    live-read-only diagnostics and exact operator approval both match.
    """
    normalized_sku = str(sku or "").strip().upper()
    draft = diagnostics.get("stale_offer_remediation_draft") or {}
    operator_approved = bool((approval_request or {}).get("operator_approved"))
    publish_after_remediation = bool((approval_request or {}).get("confirm_publish_after_remediation"))
    refusal_reasons = _eligibility_refusals(
        sku=normalized_sku,
        diagnostics=diagnostics,
        draft=draft,
        operator_approved=operator_approved,
        remediation_type=str((approval_request or {}).get("remediation_type") or ""),
        publish_after_remediation=publish_after_remediation,
    )
    refusal_reasons += _approval_refusals(
        approval_request=approval_request,
        sku=normalized_sku,
        diagnostics=diagnostics,
        draft=draft,
        remediation_type=str((approval_request or {}).get("remediation_type") or ""),
        publish_after_remediation=publish_after_remediation,
        require_approval_request=True,
    )
    refusal_reasons += _live_preflight_refusals(
        sku=normalized_sku,
        diagnostics=diagnostics,
        draft=draft,
        approval_request=approval_request,
    )
    base = _base_result(
        sku=normalized_sku,
        diagnostics=diagnostics,
        draft=draft,
        operator_approved=operator_approved,
        publish_after_remediation=publish_after_remediation,
        live_remediation_enabled=live_remediation_enabled,
        approval_request=approval_request,
        preflight_recheck_performed=True,
        executor=executor,
    )
    preflight_payload_hash = build_remediation_payload_hash(draft)
    response_base = base | {
        "mode": "live_gated",
        "live_execution_enabled": bool(live_remediation_enabled),
        "approved_payload_hash": str((approval_request or {}).get("approved_payload_hash") or ""),
        "preflight_payload_hash": preflight_payload_hash,
        "listing_id_before": str(diagnostics.get("listing_id") or draft.get("listing_id") or ""),
        "listing_id_after": str(diagnostics.get("listing_id") or draft.get("listing_id") or ""),
        "item_status_after": str(diagnostics.get("local_status") or ""),
        "repair_queue_cleared": False,
        "no_publish_performed": True,
        "calls_performed": [],
        "inventory_refresh_result": {},
        "offer_refresh_result": {},
        "post_refresh_diagnostics": {},
        "post_refresh_diagnostics_performed": False,
        "offer_payload_live_executable": {},
    }

    if not live_remediation_enabled:
        return response_base | {
            "code": "live_execution_disabled",
            "execution_status": "live_execution_disabled",
            "live_execution_enabled": False,
            "refusal_reasons": refusal_reasons
            + [
                {
                    "code": "live_execution_disabled",
                    "message": "Live stale-offer refresh is disabled by feature gate.",
                }
            ],
            "next_recommended_action": "Keep using read-only diagnostics until live stale-offer refresh is explicitly enabled.",
        }
    if executor is None:
        return response_base | {
            "code": "live_executor_unavailable",
            "execution_status": "live_execution_disabled",
            "refusal_reasons": refusal_reasons
            + [
                {
                    "code": "live_executor_unavailable",
                    "message": "No approved stale-offer refresh executor is available.",
                }
            ],
            "next_recommended_action": "Do not retry publish. Recheck the remediation runtime configuration.",
        }
    if refusal_reasons:
        return response_base | {
            "execution_status": "blocked",
            "refusal_reasons": refusal_reasons,
            "next_recommended_action": "Resolve the refusal reasons and rerun live-read-only diagnostics before remediation.",
        }

    inventory_payload = deepcopy(response_base["inventory_payload_preview"])
    offer_payload, executable_payload_refusals = _build_live_executable_offer_payload(
        preview_payload=response_base["offer_payload_preview"],
        diagnostics=diagnostics,
    )
    response_base = response_base | {"offer_payload_live_executable": deepcopy(offer_payload)}
    if executable_payload_refusals:
        return response_base | {
            "execution_status": "blocked",
            "refusal_reasons": executable_payload_refusals,
            "next_recommended_action": "Resolve live listing policy IDs before retrying stale-offer refresh.",
        }
    calls_performed: list[str] = []

    inventory_result = _call_put_safely(
        lambda: executor.put_inventory_item(normalized_sku, deepcopy(inventory_payload)),
        stage="put_inventory_item",
    )
    calls_performed.append("put_inventory_item")
    if not inventory_result["ok"]:
        return response_base | {
            "execution_status": "failed_before_offer_refresh",
            "stage": "put_inventory_item",
            "calls_performed": calls_performed,
            "inventory_refresh_result": inventory_result,
            "offer_refresh_result": {"ok": False, "skipped": True, "reason": "inventory_refresh_failed"},
            "refusal_reasons": [],
            "next_recommended_action": "Rerun publish diagnostics before deciding whether to retry stale-offer refresh.",
        }

    offer_result = _call_put_safely(
        lambda: executor.put_offer(response_base["offer_id"], deepcopy(offer_payload)),
        stage="put_offer",
    )
    calls_performed.append("put_offer")
    post_refresh_diagnostics = _run_post_refresh_diagnostics(post_refresh_diagnostics_provider)
    if not offer_result["ok"]:
        return response_base | {
            "execution_status": "partial_failure_offer_refresh_failed",
            "stage": "put_offer",
            "calls_performed": calls_performed,
            "inventory_refresh_result": inventory_result,
            "offer_refresh_result": offer_result,
            "post_refresh_diagnostics": post_refresh_diagnostics,
            "post_refresh_diagnostics_performed": bool(post_refresh_diagnostics),
            "refusal_reasons": [],
            "no_mutation_performed": False,
            "no_live_mutation_performed": False,
            "real_ebay_mutation_performed": True,
            "mocked_mutation_performed": False,
            "next_recommended_action": "Rerun publish diagnostics. Do not publish until the partial offer refresh failure is reviewed.",
        }

    return response_base | {
        "execution_status": "refresh_completed",
        "calls_performed": calls_performed,
        "inventory_refresh_result": inventory_result,
        "offer_refresh_result": offer_result,
        "post_refresh_diagnostics": post_refresh_diagnostics,
        "post_refresh_diagnostics_performed": bool(post_refresh_diagnostics),
        "refusal_reasons": [],
        "no_mutation_performed": False,
        "no_live_mutation_performed": False,
        "real_ebay_mutation_performed": True,
        "mocked_mutation_performed": False,
        "next_recommended_action": (
            "Rerun publish diagnostics/readiness. Publish still requires a separate explicit one-SKU approval."
        ),
    }


def build_stale_offer_refresh_supersede_preview(
    *,
    session: Session,
    sku: str,
    repair_plan_id: str,
    diagnostics: dict,
) -> dict:
    context = _build_supersede_context(
        session=session,
        sku=sku,
        repair_plan_id=repair_plan_id,
        diagnostics=diagnostics,
    )
    return {
        "sku": context["sku"],
        "repair_plan_id": context["repair_plan_id"],
        "eligible_for_supersede_preview": not context["refusal_reasons"],
        "action_type": SUPERSEDE_ACTION_TYPE,
        "approval_required": True,
        "typed_confirmation_required": SUPERSEDE_TYPED_CONFIRMATION,
        "read_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_publish_performed": True,
        "safe_to_execute_now": False,
        "publish_remains_blocked_after_supersede": True,
        "refresh_success_evidence": context["refresh_success_evidence"],
        "current_blocking_plan_summary": context["current_blocking_plan_summary"],
        "transition_preview": context["transition_preview"],
        "payload_hash": context["payload_hash"],
        "required_approval_fields_template": context["approval_template"],
        "blockers": context["refusal_reasons"],
        "reason": (
            ""
            if not context["refusal_reasons"]
            else context["refusal_reasons"][0]["message"]
        ),
        "next_step_warning": (
            "This preview does not publish, does not call eBay mutation APIs, and does not unblock publish broadly."
        ),
    }


def build_stale_offer_publish_decision_preview(
    *,
    session: Session,
    sku: str,
    repair_plan_id: str,
    diagnostics: dict,
) -> dict:
    context = _build_publish_decision_context(
        session=session,
        sku=sku,
        repair_plan_id=repair_plan_id,
        diagnostics=diagnostics,
    )
    return {
        "sku": context["sku"],
        "repair_plan_id": context["repair_plan_id"],
        "eligible_for_publish_decision_preview": not context["refusal_reasons"],
        "action_type": PUBLISH_DECISION_ACTION_TYPE,
        "approval_required": True,
        "typed_confirmation_required": PUBLISH_DECISION_TYPED_CONFIRMATION,
        "read_only": True,
        "no_mutation_performed": True,
        "no_ebay_mutation_performed": True,
        "no_publish_performed": True,
        "safe_to_execute_now": False,
        "final_live_listing_step_if_approved": True,
        "publish_call_preview": context["publish_call_preview"],
        "current_blocking_plan_summary": context["current_blocking_plan_summary"],
        "live_prerequisites_summary": context["live_prerequisites_summary"],
        "payload_hash": context["payload_hash"],
        "required_approval_fields_template": context["approval_template"],
        "blockers": context["refusal_reasons"],
        "reason": (
            ""
            if not context["refusal_reasons"]
            else context["refusal_reasons"][0]["message"]
        ),
        "next_step_warning": (
            "This preview does not publish, does not call eBay mutation APIs, and does not clear the repair queue. "
            "If later approved, this would be the final live listing step for the existing unpublished offer."
        ),
    }


def execute_approved_stale_offer_publish_decision(
    *,
    session: Session,
    sku: str,
    repair_plan_id: str,
    diagnostics: dict,
    approval_request: dict,
    publisher: PublishDecisionExecutor,
) -> dict:
    context = _build_publish_decision_context(
        session=session,
        sku=sku,
        repair_plan_id=repair_plan_id,
        diagnostics=diagnostics,
    )
    approval_refusals = _publish_decision_approval_refusals(
        approval_request=approval_request,
        expected=context["approval_template"],
    )
    if context["refusal_reasons"] or approval_refusals:
        return {
            "sku": context["sku"],
            "repair_plan_id": context["repair_plan_id"],
            "action_type": PUBLISH_DECISION_ACTION_TYPE,
            "execution_status": "blocked",
            "no_mutation_performed": True,
            "no_ebay_mutation_performed": True,
            "db_mutation_performed": False,
            "publish_attempted": False,
            "publish_performed": False,
            "blocker_resolved": False,
            "item_marked_listed": False,
            "listing_id_after": str(diagnostics.get("listing_id") or ""),
            "refusal_reasons": context["refusal_reasons"] + approval_refusals,
            "payload_hash": context["payload_hash"],
            "required_approval_fields_template": context["approval_template"],
        }

    publish_result = _call_publish_existing_offer_safely(
        lambda: publisher.publish_existing_offer(
            str((approval_request or {}).get("offer_id") or context["live_prerequisites_summary"].get("offer_id") or ""),
            context["sku"],
        )
    )
    if not publish_result["ok"]:
        classified_error = classify_publish_failure(context["item"], result=_as_failure_result(publish_result))
        return {
            "sku": context["sku"],
            "repair_plan_id": context["repair_plan_id"],
            "action_type": PUBLISH_DECISION_ACTION_TYPE,
            "execution_status": "publish_failed",
            "no_mutation_performed": False,
            "no_ebay_mutation_performed": False,
            "db_mutation_performed": False,
            "publish_attempted": True,
            "publish_performed": False,
            "published_existing_offer_only": False,
            "created_new_offer": False,
            "inventory_refreshed": False,
            "offer_refreshed": False,
            "batch_publish_performed": False,
            "blocker_resolved": False,
            "item_marked_listed": False,
            "listing_id_before": str(diagnostics.get("listing_id") or ""),
            "listing_id_after": str(diagnostics.get("listing_id") or ""),
            "auth_headers_prepared": bool(publish_result["details"].get("auth_headers_prepared")),
            "auth_token_source": str(publish_result["details"].get("auth_token_source") or ""),
            "oauth_token_may_have_been_refreshed": bool(publish_result["details"].get("oauth_token_may_have_been_refreshed")),
            "publish_result": publish_result,
            "classified_error": classified_error,
            "refusal_reasons": [],
        }

    listing_id = str(
        publish_result["value"].get("listing_id")
        or publish_result["value"].get("listingId")
        or context["live_prerequisites_summary"].get("offer_id")
        or ""
    ).strip()
    listing_url = str(publish_result["value"].get("listing_url") or publish_result["value"].get("listingUrl") or "").strip()
    offer_id = str(
        publish_result["value"].get("offer_id")
        or publish_result["value"].get("offerId")
        or context["live_prerequisites_summary"].get("offer_id")
        or ""
    ).strip()
    item_record = session.exec(select(ItemRecord).where(ItemRecord.sku == context["sku"])).first()
    if item_record is None:
        return {
            "sku": context["sku"],
            "repair_plan_id": context["repair_plan_id"],
            "action_type": PUBLISH_DECISION_ACTION_TYPE,
            "execution_status": "blocked",
            "no_mutation_performed": False,
            "no_ebay_mutation_performed": False,
            "db_mutation_performed": False,
            "publish_attempted": True,
            "publish_performed": False,
            "blocker_resolved": False,
            "item_marked_listed": False,
            "listing_id_after": str(diagnostics.get("listing_id") or ""),
            "refusal_reasons": [{"code": "item_not_found_after_publish", "message": "Local item disappeared before success could be recorded."}],
            "payload_hash": context["payload_hash"],
        }

    plan = context["plan"]
    before_snapshot = {
        "repair_plan_id": plan.id,
        "status": str(plan.status or ""),
        "retry_allowed": bool(plan.retry_allowed),
        "requires_review": bool(plan.requires_review),
        "classified_error_code": str(plan.classified_error_code or ""),
        "publish_attempt_id": str(plan.publish_attempt_id or ""),
        "offer_id": str(context["live_prerequisites_summary"].get("offer_id") or ""),
        "listing_id_before": str(item_record.listing_id or ""),
    }
    now = datetime.utcnow()
    item_record.listing_id = listing_id
    item_record.listing_url = listing_url or item_record.listing_url
    item_record.offer_id = offer_id or item_record.offer_id
    item_record.status = ItemStatus.LISTED
    item_record.platform = Platform.EBAY
    item_record.date_listed = now
    item_record.updated_at = now
    session.add(item_record)

    plan.status = "resolved"
    plan.retry_allowed = False
    plan.updated_at = now
    session.add(plan)

    decision = PublishRepairDecisionRecord(
        sku=context["sku"],
        repair_plan_id=plan.id,
        action="publish_decision_approved_existing_offer",
        before_value_json=json.dumps(before_snapshot, sort_keys=True),
        after_value_json=json.dumps(
            {
                "status": "resolved",
                "retry_allowed": False,
                "offer_id": offer_id,
                "listing_id": listing_id,
                "published_existing_offer_only": True,
                "created_new_offer": False,
                "inventory_refreshed": False,
                "offer_refreshed": False,
                "batch_publish_performed": False,
                "category_condition_changed": False,
                "auth_headers_prepared": bool(publish_result["value"].get("auth_headers_prepared")),
                "auth_token_source": str(publish_result["value"].get("auth_token_source") or ""),
                "oauth_token_may_have_been_refreshed": bool(publish_result["value"].get("oauth_token_may_have_been_refreshed")),
            },
            sort_keys=True,
        ),
        operator_label=str((approval_request or {}).get("operator_label") or ""),
        approved_at=now,
    )
    session.add(decision)
    session.commit()

    AuditLog()._write(
        {
            "event": "publish_decision_approved_existing_offer",
            "sku": context["sku"],
            "repair_plan_id": plan.id,
            "offer_id": offer_id,
            "listing_id": listing_id,
            "published_existing_offer_only": True,
            "created_new_offer": False,
            "inventory_refreshed": False,
            "offer_refreshed": False,
            "batch_publish_performed": False,
            "category_condition_changed": False,
            "auth_headers_prepared": bool(publish_result["value"].get("auth_headers_prepared")),
            "auth_token_source": str(publish_result["value"].get("auth_token_source") or ""),
            "oauth_token_may_have_been_refreshed": bool(publish_result["value"].get("oauth_token_may_have_been_refreshed")),
        }
    )

    return {
        "sku": context["sku"],
        "repair_plan_id": plan.id,
        "action_type": PUBLISH_DECISION_ACTION_TYPE,
        "execution_status": "publish_completed",
        "no_mutation_performed": False,
        "no_ebay_mutation_performed": False,
        "db_mutation_performed": True,
        "publish_attempted": True,
        "publish_performed": True,
        "published_existing_offer_only": True,
        "created_new_offer": False,
        "inventory_refreshed": False,
        "offer_refreshed": False,
        "batch_publish_performed": False,
        "blocker_resolved": True,
        "item_marked_listed": True,
        "listing_id_before": str(diagnostics.get("listing_id") or ""),
        "listing_id_after": listing_id,
        "listing_url_after": str(item_record.listing_url or ""),
        "item_status_after": str(item_record.status or ""),
        "platform_after": str(item_record.platform or ""),
        "date_listed_after": item_record.date_listed.isoformat() if item_record.date_listed else "",
        "auth_headers_prepared": bool(publish_result["value"].get("auth_headers_prepared")),
        "auth_token_source": str(publish_result["value"].get("auth_token_source") or ""),
        "oauth_token_may_have_been_refreshed": bool(publish_result["value"].get("oauth_token_may_have_been_refreshed")),
        "publish_result": publish_result,
        "refusal_reasons": [],
        "payload_hash": context["payload_hash"],
    }


def execute_approved_stale_offer_refresh_supersede(
    *,
    session: Session,
    sku: str,
    repair_plan_id: str,
    diagnostics: dict,
    approval_request: dict,
) -> dict:
    context = _build_supersede_context(
        session=session,
        sku=sku,
        repair_plan_id=repair_plan_id,
        diagnostics=diagnostics,
    )
    approval_refusals = _supersede_approval_refusals(
        approval_request=approval_request,
        expected=context["approval_template"],
    )
    if context["refusal_reasons"] or approval_refusals:
        return {
            "sku": context["sku"],
            "repair_plan_id": context["repair_plan_id"],
            "action_type": SUPERSEDE_ACTION_TYPE,
            "execution_status": "blocked",
            "no_mutation_performed": True,
            "no_ebay_mutation_performed": True,
            "db_mutation_performed": False,
            "no_publish_performed": True,
            "repair_queue_cleared": False,
            "publish_remains_blocked": bool(context["repair_blocker"].get("blocked_by_repair_queue")),
            "refusal_reasons": context["refusal_reasons"] + approval_refusals,
            "payload_hash": context["payload_hash"],
            "required_approval_fields_template": context["approval_template"],
        }

    plan = context["plan"]
    before_snapshot = {
        "repair_plan_id": plan.id,
        "status": str(plan.status or ""),
        "retry_allowed": bool(plan.retry_allowed),
        "requires_review": bool(plan.requires_review),
        "classified_error_code": str(plan.classified_error_code or ""),
        "publish_attempt_id": str(plan.publish_attempt_id or ""),
    }
    now = datetime.utcnow()
    plan.status = "resolved"
    plan.retry_allowed = False
    plan.updated_at = now
    session.add(plan)

    replacement_payload = deepcopy(context["replacement_blocker_preview"])
    replacement_now = now + timedelta(seconds=1)
    replacement_plan = PublishRepairPlanRecord(
        sku=context["sku"],
        publish_attempt_id=str(plan.publish_attempt_id or ""),
        status="needs_manual_review",
        affected_field="publish_decision",
        current_value_json=json.dumps(replacement_payload["current_value"], sort_keys=True),
        expected_value_json=json.dumps(replacement_payload["expected_value"], sort_keys=True),
        suggested_value_json=json.dumps(replacement_payload["suggested_value"], sort_keys=True),
        suggested_actions_json=json.dumps(replacement_payload["suggested_actions"], sort_keys=True),
        risk_level=str(replacement_payload["risk_level"] or "high"),
        safe_to_auto_apply=False,
        requires_review=True,
        retry_allowed=False,
        source="stale_offer_refresh",
        repair_layer="post_refresh_publish_decision",
        classified_error_code=REPLACEMENT_BLOCKING_ERROR_CODE,
        created_at=replacement_now,
        updated_at=replacement_now,
    )
    session.add(replacement_plan)
    session.flush()

    decision = PublishRepairDecisionRecord(
        sku=context["sku"],
        repair_plan_id=plan.id,
        action="supersede_after_refresh",
        before_value_json=json.dumps(before_snapshot, sort_keys=True),
        after_value_json=json.dumps(
            {
                "status": "resolved",
                "retry_allowed": False,
                "superseded_reason": "superseded_by_successful_stale_offer_refresh",
                "replacement_repair_plan_id": replacement_plan.id,
                "replacement_classified_error_code": REPLACEMENT_BLOCKING_ERROR_CODE,
                "previous_classified_error_code": "invalid_category_condition",
                "previous_latest_publish_attempt_id": str(context["latest_publish_attempt_id"] or ""),
                "no_ebay_mutation_performed": True,
                "no_publish_performed": True,
                "replacement_blocker_created": True,
            },
            sort_keys=True,
        ),
        operator_label=str((approval_request or {}).get("operator_label") or ""),
        approved_at=now,
    )
    session.add(decision)
    session.commit()

    repair_blocker_after = get_publish_repair_blocker(session, context["sku"])
    publish_still_blocked = bool(repair_blocker_after.get("blocked_by_repair_queue"))

    AuditLog()._write(
        {
            "event": "publish_repair_superseded_after_refresh",
            "sku": context["sku"],
            "previous_repair_plan_id": plan.id,
            "previous_error_code": "invalid_category_condition",
            "previous_latest_publish_attempt_id": str(context["latest_publish_attempt_id"] or ""),
            "reason": "superseded_by_successful_stale_offer_refresh",
            "no_ebay_mutation_performed": True,
            "no_publish_performed": True,
            "replacement_blocker_created": True,
            "replacement_repair_plan_id": replacement_plan.id,
            "replacement_classified_error_code": REPLACEMENT_BLOCKING_ERROR_CODE,
        }
    )

    return {
        "sku": context["sku"],
        "repair_plan_id": plan.id,
        "action_type": SUPERSEDE_ACTION_TYPE,
        "execution_status": "supersede_completed",
        "no_mutation_performed": False,
        "no_ebay_mutation_performed": True,
        "db_mutation_performed": True,
        "no_publish_performed": True,
        "repair_queue_cleared": False,
        "old_repair_plan_resolved": True,
        "replacement_blocker_created": True,
        "replacement_repair_plan_id": replacement_plan.id,
        "replacement_blocker": {
            "status": replacement_plan.status,
            "retry_allowed": bool(replacement_plan.retry_allowed),
            "requires_review": bool(replacement_plan.requires_review),
            "source": replacement_plan.source,
            "repair_layer": replacement_plan.repair_layer,
            "classified_error_code": replacement_plan.classified_error_code,
        },
        "publish_remains_blocked": publish_still_blocked,
        "repair_plan_id_after": str(repair_blocker_after.get("repair_plan_id") or ""),
        "classified_error_code_after": str(repair_blocker_after.get("classified_error_code") or ""),
        "item_status_after": str(diagnostics.get("local_status") or ""),
        "listing_id_after": str(diagnostics.get("listing_id") or ""),
        "item_marked_listed": str(diagnostics.get("local_status") or "").lower() == "listed",
        "refusal_reasons": [],
        "payload_hash": context["payload_hash"],
    }


def _build_publish_decision_context(
    *,
    session: Session,
    sku: str,
    repair_plan_id: str,
    diagnostics: dict,
) -> dict:
    from packages.ebay.src.inventory_client import EbayInventoryClient

    normalized_sku = str(sku or "").strip().upper()
    normalized_plan_id = str(repair_plan_id or "").strip()
    item = ItemRepository(session).get_by_sku(normalized_sku) if normalized_sku else None
    plan = session.get(PublishRepairPlanRecord, normalized_plan_id) if normalized_plan_id else None
    repair_blocker = get_publish_repair_blocker(session, normalized_sku) if normalized_sku else {}
    latest_publish_attempt_id = str(
        (plan.publish_attempt_id if plan else "")
        or diagnostics.get("latest_publish_attempt_id")
        or repair_blocker.get("latest_publish_attempt_id")
        or ""
    )
    hosted_photo_urls: list[str] = []
    if item is not None:
        hosted_photo_urls = EbayInventoryClient().extract_hosted_photo_urls(
            [str(value) for value in (item.image_paths or [])]
        )
    refusal_reasons = _publish_decision_refusals(
        sku=normalized_sku,
        repair_plan_id=normalized_plan_id,
        item=item,
        plan=plan,
        diagnostics=diagnostics,
        repair_blocker=repair_blocker,
        hosted_photo_urls=hosted_photo_urls,
    )
    offer_id = str(diagnostics.get("offer_id") or (item.offer_id if item else "") or "").strip()
    live_offer = diagnostics.get("existing_offer_diagnostics") or {}
    listing_policies = _extract_listing_policy_ids(live_offer.get("listing_policies") or {})
    merchant_location_key = str(live_offer.get("merchant_location_key") or "").strip()
    current_blocking_plan_summary = {
        "repair_plan_id": normalized_plan_id,
        "belongs_to_sku": bool(plan and str(plan.sku or "").upper() == normalized_sku),
        "status": str(plan.status or "") if plan else "",
        "retry_allowed": bool(plan.retry_allowed) if plan else False,
        "requires_review": bool(plan.requires_review) if plan else False,
        "classified_error_code": str(plan.classified_error_code or "") if plan else "",
        "repair_layer": str(plan.repair_layer or "") if plan else "",
        "publish_attempt_id": str(plan.publish_attempt_id or "") if plan else "",
        "is_current_blocking_plan": bool(
            repair_blocker.get("blocked_by_repair_queue")
            and str(repair_blocker.get("repair_plan_id") or "") == normalized_plan_id
        ),
    }
    publish_call_preview = {
        "order": 1,
        "method": "POST",
        "endpoint": f"/sell/inventory/v1/offer/{offer_id}/publish" if offer_id else "",
        "preview_only": True,
        "mutation_performed": False,
        "note": "If later approved, this would create a live eBay listing for the existing unpublished offer.",
    }
    live_prerequisites_summary = {
        "offer_id": offer_id,
        "offer_status": str(live_offer.get("status") or ""),
        "listing_id": str(diagnostics.get("listing_id") or ""),
        "merchant_location_key": merchant_location_key,
        "listing_policies": listing_policies,
        "hosted_photo_urls": hosted_photo_urls,
        "local_category_id": str(diagnostics.get("local_category_id") or ""),
        "local_condition_id": str(diagnostics.get("local_condition_id") or ""),
        "local_inventory_condition_enum": str(diagnostics.get("local_inventory_condition_enum") or ""),
        "inventory_item_exists": bool((diagnostics.get("inventory_item_diagnostics") or {}).get("inventory_item_exists")),
        "live_policy_allows_condition": (diagnostics.get("category_condition_policy_diagnostics") or {}).get("live_policy_allows_condition"),
        "planned_action": str(diagnostics.get("planned_action") or ""),
    }
    payload_hash = build_publish_decision_payload_hash(
        {
            "sku": normalized_sku,
            "repair_plan_id": normalized_plan_id,
            "latest_publish_attempt_id": latest_publish_attempt_id,
            "publish_call_preview": publish_call_preview,
            "current_blocking_plan_summary": current_blocking_plan_summary,
            "live_prerequisites_summary": live_prerequisites_summary,
        }
    )
    approval_template = _publish_decision_approval_template(
        sku=normalized_sku,
        repair_plan_id=normalized_plan_id,
        latest_publish_attempt_id=latest_publish_attempt_id,
        diagnostics=diagnostics,
        merchant_location_key=merchant_location_key,
        listing_policies=listing_policies,
        payload_hash=payload_hash,
    )
    return {
        "sku": normalized_sku,
        "repair_plan_id": normalized_plan_id,
        "item": item,
        "plan": plan,
        "repair_blocker": repair_blocker,
        "latest_publish_attempt_id": latest_publish_attempt_id,
        "current_blocking_plan_summary": current_blocking_plan_summary,
        "publish_call_preview": publish_call_preview,
        "live_prerequisites_summary": live_prerequisites_summary,
        "payload_hash": payload_hash,
        "approval_template": approval_template,
        "refusal_reasons": refusal_reasons,
    }


def _publish_decision_refusals(
    *,
    sku: str,
    repair_plan_id: str,
    item,
    plan: PublishRepairPlanRecord | None,
    diagnostics: dict,
    repair_blocker: dict,
    hosted_photo_urls: list[str],
) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    if not sku:
        refuse("missing_sku", "SKU is required.")
    if item is None:
        refuse("item_not_found", "Requested SKU was not found locally.")
    if not repair_plan_id:
        refuse("missing_repair_plan_id", "repair_plan_id is required.")
    if plan is None:
        refuse("repair_plan_not_found", "Selected repair plan was not found.")
        return refusal_reasons
    if str(plan.sku or "").upper() != sku:
        refuse("repair_plan_sku_mismatch", "Selected repair plan does not belong to the requested SKU.")
    if str(plan.classified_error_code or "") != REPLACEMENT_BLOCKING_ERROR_CODE:
        refuse(
            "selected_plan_not_publish_decision_blocker",
            "Selected repair plan is not the active requires_publish_decision_after_refresh blocker.",
        )
    if str(plan.repair_layer or "") != "post_refresh_publish_decision":
        refuse("selected_plan_wrong_repair_layer", "Selected repair plan must remain in post_refresh_publish_decision.")
    if str(plan.status or "") != "needs_manual_review":
        refuse("selected_plan_status_not_needs_manual_review", "Selected repair plan status must still be needs_manual_review.")
    if bool(plan.retry_allowed):
        refuse("selected_plan_retry_allowed_not_false", "Selected repair plan retry_allowed must remain false.")
    if not repair_blocker.get("blocked_by_repair_queue"):
        refuse("repair_queue_not_blocking", "Repair queue no longer blocks publish for this SKU.")
    if str(repair_blocker.get("repair_plan_id") or "") != repair_plan_id:
        refuse("selected_plan_not_current_blocker", "Selected repair plan is not the current blocking plan for this SKU.")
    if str(repair_blocker.get("classified_error_code") or "") != REPLACEMENT_BLOCKING_ERROR_CODE:
        refuse(
            "current_blocker_not_publish_decision_after_refresh",
            "Current blocking plan is not requires_publish_decision_after_refresh.",
        )

    if diagnostics.get("live_readonly_requested") is not True or diagnostics.get("live_readonly_performed") is not True:
        refuse("live_readonly_preflight_required", "Live read-only diagnostics are required for publish-decision preview.")
    methods = set(diagnostics.get("live_readonly_methods_called") or [])
    for method in ("get_offer", "get_inventory_item", "get_item_condition_policies"):
        if method not in methods:
            refuse("missing_live_readonly_method", f"Live read-only diagnostics did not call {method}.")
    if diagnostics.get("live_readonly_errors"):
        refuse("live_readonly_errors_present", "Live read-only diagnostics returned errors.")

    offer_id = str(diagnostics.get("offer_id") or "").strip()
    if not offer_id:
        refuse("missing_offer_id", "offer_id is required before publish-decision preview.")
    if str(diagnostics.get("listing_id") or "").strip():
        refuse("listing_id_present", "listing_id must remain empty.")
    if str(diagnostics.get("planned_action") or "") != "publish_existing_offer":
        refuse("not_existing_offer_publish_flow", "Current planned action is not publish_existing_offer.")

    offer = diagnostics.get("existing_offer_diagnostics") or {}
    if offer.get("read_available") is not True:
        refuse("offer_read_unavailable", "Live offer read must be available.")
    if offer.get("offer_exists") is not True:
        refuse("offer_not_found", "Existing offer must still exist.")
    if str(offer.get("status") or "").upper() != "UNPUBLISHED":
        refuse("offer_status_not_unpublished", "Existing offer must still be UNPUBLISHED.")
    if offer.get("category_differs_from_local") is True:
        refuse("existing_offer_category_differs_from_local", "Existing offer category differs from local category.")
    if offer.get("condition_differs_from_local") is True:
        refuse("existing_offer_condition_differs_from_local", "Existing offer condition differs from local condition.")

    inventory = diagnostics.get("inventory_item_diagnostics") or {}
    expected_inventory_enum = condition_id_to_inventory_enum(
        diagnostics.get("local_condition_id") or "",
        default="",
    )
    if inventory.get("read_available") is not True:
        refuse("inventory_read_unavailable", "Live inventory item read must be available.")
    if inventory.get("inventory_item_exists") is not True:
        refuse("inventory_item_not_found", "Live inventory item must still exist.")
    if str(inventory.get("condition_enum") or "") != expected_inventory_enum:
        refuse(
            "condition_id_enum_mapping_mismatch",
            f"Live inventory condition must match condition_id 3000 -> {expected_inventory_enum}.",
        )

    if str(diagnostics.get("local_condition_id") or "") != "3000":
        refuse("condition_id_not_confirmed", "Current condition_id must remain 3000.")
    if str(diagnostics.get("local_inventory_condition_enum") or "") != expected_inventory_enum:
        refuse(
            "inventory_condition_not_confirmed",
            f"Current inventory condition enum must remain {expected_inventory_enum}.",
        )
    if str(diagnostics.get("local_category_id") or "") != "14056":
        refuse("category_id_not_confirmed", "Current category_id must remain 14056.")

    policy = diagnostics.get("category_condition_policy_diagnostics") or {}
    if policy.get("read_available") is not True:
        refuse("category_policy_read_unavailable", "Live category policy read must be available.")
    if policy.get("live_policy_allows_condition") is not True:
        refuse("live_policy_does_not_allow_condition", "Live category policy must still allow condition 3000.")
    if policy.get("live_metadata_supports_changing_condition") is True:
        refuse("category_condition_change_appears_needed", "Live policy indicates a category or condition change may still be required.")
    for finding in (diagnostics.get("condition_mapping_diagnostics") or {}).get("findings") or []:
        refuse(str(finding.get("code") or "condition_mapping_issue"), str(finding.get("message") or "Condition mapping mismatch detected."))

    if not hosted_photo_urls:
        refuse("missing_hosted_public_image_urls", "Hosted public image URLs are required before publish-decision preview.")

    listing_policies = _extract_listing_policy_ids(offer.get("listing_policies") or {})
    if not all(_is_real_value(value) for value in listing_policies.values()):
        refuse("missing_real_listing_policy_ids", "Real fulfillment, payment, and return policy IDs are required.")
    if _contains_preview_placeholder(listing_policies):
        refuse("placeholder_listing_policy_detected", "Preview placeholder listing policy IDs are not allowed.")

    merchant_location_key = str(offer.get("merchant_location_key") or "").strip()
    if not _is_real_value(merchant_location_key):
        refuse("merchant_location_key_unresolved", "A real merchantLocationKey is required before publish-decision preview.")

    return refusal_reasons


def build_publish_decision_payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _publish_decision_approval_template(
    *,
    sku: str,
    repair_plan_id: str,
    latest_publish_attempt_id: str,
    diagnostics: dict,
    merchant_location_key: str,
    listing_policies: dict[str, str],
    payload_hash: str,
) -> dict:
    return {
        "sku": sku,
        "action_type": PUBLISH_DECISION_ACTION_TYPE,
        "repair_plan_id": repair_plan_id,
        "latest_publish_attempt_id": latest_publish_attempt_id,
        "offer_id": str(diagnostics.get("offer_id") or ""),
        "confirm_offer_status": "UNPUBLISHED",
        "confirm_listing_id_empty": True,
        "confirm_category_id": str(diagnostics.get("local_category_id") or ""),
        "confirm_condition_id": str(diagnostics.get("local_condition_id") or ""),
        "confirm_inventory_condition_enum": str(diagnostics.get("local_inventory_condition_enum") or ""),
        "confirm_blocker_classified_error_code": REPLACEMENT_BLOCKING_ERROR_CODE,
        "confirm_merchant_location_key": merchant_location_key,
        "confirm_fulfillment_policy_id": str(listing_policies.get("fulfillmentPolicyId") or ""),
        "confirm_payment_policy_id": str(listing_policies.get("paymentPolicyId") or ""),
        "confirm_return_policy_id": str(listing_policies.get("returnPolicyId") or ""),
        "confirm_publish_existing_offer_only": True,
        "confirm_publish_after_decision": True,
        "operator_approved": True,
        "typed_confirmation": PUBLISH_DECISION_TYPED_CONFIRMATION,
        "approved_payload_hash": payload_hash,
    }


def _eligibility_refusals(
    *,
    sku: str,
    diagnostics: dict,
    draft: dict,
    operator_approved: bool,
    remediation_type: str,
    publish_after_remediation: bool,
) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    diagnostics_sku = str(diagnostics.get("sku") or "").strip().upper()
    if not sku:
        refuse("missing_sku", "Requested SKU is missing.")
    if diagnostics_sku and sku and diagnostics_sku != sku:
        refuse("sku_mismatch", "Requested SKU does not match diagnostics SKU.")
    if remediation_type != REMEDIATION_TYPE:
        refuse("wrong_remediation_type", "Requested remediation type is not refresh_existing_unpublished_offer.")
    if not operator_approved:
        refuse("operator_approval_required", "Operator approval is required for mock remediation execution.")
    if publish_after_remediation:
        refuse("publish_after_remediation_not_allowed", "This phase must not publish after remediation.")

    if draft.get("remediation_type") != REMEDIATION_TYPE:
        refuse("wrong_draft_type", "Diagnostics do not include a refresh_existing_unpublished_offer draft.")
    if draft.get("status") != "draft_preview_available" or draft.get("safe_to_preview") is not True:
        refuse("draft_not_previewable", "The remediation draft is not eligible for preview execution.")
    if draft.get("live_execution_enabled") is not False:
        refuse("draft_live_execution_not_disabled", "The remediation draft must keep live execution disabled.")
    if draft.get("publish_after_remediation") is not False:
        refuse("draft_publish_after_remediation_not_allowed", "The remediation draft must not request publish after remediation.")

    if not str(draft.get("offer_id") or "").strip():
        refuse("missing_offer_id", "No existing offer_id is available.")
    if str(draft.get("listing_id") or "").strip():
        refuse("listing_id_present", "Item has a listing_id and must not use unpublished-offer remediation.")
    if str(diagnostics.get("local_status") or "").lower() == "listed":
        refuse("item_already_listed", "Item is already listed.")
    if str(draft.get("offer_status") or "").upper() != "UNPUBLISHED":
        refuse("offer_status_not_unpublished", "Existing offer status must be UNPUBLISHED.")
    if diagnostics.get("planned_action") != "publish_existing_offer":
        refuse("not_existing_offer_publish_flow", "Current planned action is not publish_existing_offer.")
    if diagnostics.get("blocked_by_repair_queue") is not True:
        refuse("repair_queue_not_blocking", "Latest repair queue state does not block publish.")
    if not str(draft.get("repair_plan_id") or "").strip():
        refuse("missing_latest_repair_plan", "Latest repair plan is missing from the draft.")

    if str(draft.get("condition_id") or "") != "3000":
        refuse("condition_id_not_supported_for_draft", "This mock remediation is limited to condition_id 3000.")
    expected_inventory_enum = condition_id_to_inventory_enum(str(draft.get("condition_id") or ""), default="")
    if str(draft.get("inventory_condition_enum") or "") != expected_inventory_enum:
        refuse("condition_id_enum_mapping_mismatch", f"Inventory condition enum must be {expected_inventory_enum}.")

    live_policy = draft.get("live_policy_result") or {}
    if live_policy.get("live_policy_allows_condition") is not True:
        refuse("live_policy_does_not_allow_condition", "Live/mock category policy must allow the current condition.")

    inventory = diagnostics.get("inventory_item_diagnostics") or {}
    local_condition = str(diagnostics.get("local_inventory_condition_enum") or draft.get("inventory_condition_enum") or "")
    live_inventory_condition = str(inventory.get("condition_enum") or "")
    if not live_inventory_condition:
        refuse("missing_inventory_condition_read", "Live/mock inventory condition must be present before mock remediation execution.")
    if live_inventory_condition and live_inventory_condition != local_condition:
        refuse("inventory_condition_differs_from_local", "Live/mock inventory condition differs from local condition.")

    offer = diagnostics.get("existing_offer_diagnostics") or {}
    offer_category = str(offer.get("category_id") or "")
    local_category = str(diagnostics.get("local_category_id") or draft.get("category_id") or "")
    if offer.get("category_differs_from_local") is True or (offer_category and local_category and offer_category != local_category):
        refuse("existing_offer_category_differs_from_local", "Existing offer category differs from local category.")

    policy = diagnostics.get("category_condition_policy_diagnostics") or {}
    if policy.get("live_metadata_supports_changing_condition") is True:
        refuse("category_condition_change_appears_needed", "Policy diagnostics indicate a condition/category change may be needed.")

    return refusal_reasons


def _live_preflight_refusals(
    *,
    sku: str,
    diagnostics: dict,
    draft: dict,
    approval_request: dict | None,
) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    if diagnostics.get("found") is not True:
        refuse("preflight_item_not_found", "Live refresh preflight could not find the item.")
    if diagnostics.get("live_readonly_requested") is not True or diagnostics.get("live_readonly_performed") is not True:
        refuse("live_readonly_preflight_required", "Live read-only diagnostics must run immediately before refresh.")
    methods = set(diagnostics.get("live_readonly_methods_called") or [])
    for method in ("get_offer", "get_inventory_item", "get_item_condition_policies"):
        if method not in methods:
            refuse("missing_live_readonly_method", f"Live read-only preflight did not call {method}.")
    if diagnostics.get("live_readonly_errors"):
        refuse("live_readonly_errors_present", "Live read-only preflight returned errors.")
    if str((approval_request or {}).get("sku") or "").strip().upper() != sku:
        refuse("approval_path_sku_mismatch", "Path SKU and approval SKU must match.")
    if diagnostics.get("retry_allowed") is not False:
        refuse("retry_allowed_not_false", "Repair queue retry_allowed must remain false before remediation.")
    if str(diagnostics.get("local_status") or "") == "listed":
        refuse("item_already_listed", "Item is already listed.")

    offer = diagnostics.get("existing_offer_diagnostics") or {}
    if offer.get("read_available") is not True:
        refuse("offer_read_unavailable", "Live offer read must be available.")
    if offer.get("offer_exists") is not True:
        refuse("offer_not_found", "Existing offer must exist before refresh.")
    if str(offer.get("offer_id") or diagnostics.get("offer_id") or "") != str(draft.get("offer_id") or ""):
        refuse("offer_id_mismatch", "Live offer ID must match the approved draft offer ID.")

    inventory = diagnostics.get("inventory_item_diagnostics") or {}
    if inventory.get("read_available") is not True:
        refuse("inventory_read_unavailable", "Live inventory item read must be available.")
    if inventory.get("inventory_item_exists") is not True:
        refuse("inventory_item_not_found", "Live inventory item must exist before refresh.")

    policy = diagnostics.get("category_condition_policy_diagnostics") or {}
    if policy.get("read_available") is not True:
        refuse("category_policy_read_unavailable", "Live category-condition policy read must be available.")

    if not draft.get("intended_inventory_item_payload_preview"):
        refuse("missing_inventory_payload_preview", "Inventory payload preview is required.")
    if not draft.get("intended_offer_payload_preview"):
        refuse("missing_offer_payload_preview", "Offer payload preview is required.")
    if build_remediation_payload_hash(draft) != str((approval_request or {}).get("approved_payload_hash") or ""):
        refuse("preflight_payload_hash_mismatch", "Fresh preflight payload hash does not match approved payload hash.")

    return refusal_reasons


def _build_supersede_context(
    *,
    session: Session,
    sku: str,
    repair_plan_id: str,
    diagnostics: dict,
) -> dict:
    normalized_sku = str(sku or "").strip().upper()
    normalized_plan_id = str(repair_plan_id or "").strip()
    plan = session.get(PublishRepairPlanRecord, normalized_plan_id) if normalized_plan_id else None
    repair_blocker = get_publish_repair_blocker(session, normalized_sku) if normalized_sku else {}
    latest_publish_attempt_id = str(
        (plan.publish_attempt_id if plan else "")
        or diagnostics.get("latest_publish_attempt_id")
        or repair_blocker.get("latest_publish_attempt_id")
        or ""
    )
    refresh_success_evidence = _resolve_refresh_success_evidence(diagnostics)
    refusal_reasons = _supersede_refusals(
        sku=normalized_sku,
        repair_plan_id=normalized_plan_id,
        plan=plan,
        diagnostics=diagnostics,
        repair_blocker=repair_blocker,
        refresh_success_evidence=refresh_success_evidence,
    )
    replacement_blocker_preview = _replacement_blocker_preview(
        diagnostics=diagnostics,
        plan=plan,
        latest_publish_attempt_id=latest_publish_attempt_id,
    )
    current_blocking_plan_summary = {
        "repair_plan_id": normalized_plan_id,
        "belongs_to_sku": bool(plan and str(plan.sku or "").upper() == normalized_sku),
        "status": str(plan.status or "") if plan else "",
        "retry_allowed": bool(plan.retry_allowed) if plan else False,
        "requires_review": bool(plan.requires_review) if plan else False,
        "classified_error_code": str(plan.classified_error_code or "") if plan else "",
        "repair_layer": str(plan.repair_layer or "") if plan else "",
        "publish_attempt_id": str(plan.publish_attempt_id or "") if plan else "",
        "is_current_blocking_plan": bool(
            repair_blocker.get("blocked_by_repair_queue")
            and str(repair_blocker.get("repair_plan_id") or "") == normalized_plan_id
        ),
    }
    transition_preview = {
        "selected_repair_plan_transition": {
            "repair_plan_id": normalized_plan_id,
            "status_before": current_blocking_plan_summary["status"],
            "status_after": "resolved",
            "retry_allowed_after": False,
            "resolution_reason": "superseded_by_successful_stale_offer_refresh",
        },
        "replacement_blocker_preview": replacement_blocker_preview,
        "no_ebay_mutation_performed": True,
        "no_publish_performed": True,
        "publish_remains_blocked": True,
    }
    payload_hash = build_supersede_payload_hash(
        {
            "sku": normalized_sku,
            "repair_plan_id": normalized_plan_id,
            "latest_publish_attempt_id": latest_publish_attempt_id,
            "current_blocking_plan_summary": current_blocking_plan_summary,
            "transition_preview": transition_preview,
            "diagnostic_confirmations": {
                "listing_id": str(diagnostics.get("listing_id") or ""),
                "offer_status": str((diagnostics.get("existing_offer_diagnostics") or {}).get("status") or ""),
                "category_id": str(diagnostics.get("local_category_id") or ""),
                "condition_id": str(diagnostics.get("local_condition_id") or ""),
                "inventory_condition_enum": str(diagnostics.get("local_inventory_condition_enum") or ""),
            },
        }
    )
    approval_template = _supersede_approval_template(
        sku=normalized_sku,
        repair_plan_id=normalized_plan_id,
        latest_publish_attempt_id=latest_publish_attempt_id,
        diagnostics=diagnostics,
        payload_hash=payload_hash,
    )
    return {
        "sku": normalized_sku,
        "repair_plan_id": normalized_plan_id,
        "plan": plan,
        "repair_blocker": repair_blocker,
        "latest_publish_attempt_id": latest_publish_attempt_id,
        "refresh_success_evidence": refresh_success_evidence,
        "current_blocking_plan_summary": current_blocking_plan_summary,
        "replacement_blocker_preview": replacement_blocker_preview,
        "transition_preview": transition_preview,
        "payload_hash": payload_hash,
        "approval_template": approval_template,
        "refusal_reasons": refusal_reasons,
    }


def _supersede_refusals(
    *,
    sku: str,
    repair_plan_id: str,
    plan: PublishRepairPlanRecord | None,
    diagnostics: dict,
    repair_blocker: dict,
    refresh_success_evidence: dict,
) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    if not sku:
        refuse("missing_sku", "SKU is required.")
    if not repair_plan_id:
        refuse("missing_repair_plan_id", "repair_plan_id is required.")
    if plan is None:
        refuse("repair_plan_not_found", "Selected repair plan was not found.")
        return refusal_reasons
    if str(plan.sku or "").upper() != sku:
        refuse("repair_plan_sku_mismatch", "Selected repair plan does not belong to the requested SKU.")
    if str(plan.classified_error_code or "") != "invalid_category_condition":
        refuse("selected_plan_not_invalid_category_condition", "Selected repair plan is not the current invalid_category_condition blocker.")
    if str(plan.status or "") != "needs_manual_review":
        refuse("selected_plan_status_not_needs_manual_review", "Selected repair plan status must still be needs_manual_review.")
    if bool(plan.retry_allowed):
        refuse("selected_plan_retry_allowed_not_false", "Selected repair plan retry_allowed must remain false.")
    if not repair_blocker.get("blocked_by_repair_queue"):
        refuse("repair_queue_not_blocking", "Repair queue no longer blocks publish for this SKU.")
    if str(repair_blocker.get("repair_plan_id") or "") != repair_plan_id:
        refuse("selected_plan_not_current_blocker", "Selected repair plan is not the current blocking plan for this SKU.")
    if str(repair_blocker.get("classified_error_code") or "") != "invalid_category_condition":
        refuse("current_blocker_not_invalid_category_condition", "Current blocking plan is not invalid_category_condition.")

    if diagnostics.get("live_readonly_requested") is not True or diagnostics.get("live_readonly_performed") is not True:
        refuse("live_readonly_preflight_required", "Live read-only diagnostics are required for supersede preview and execution.")
    methods = set(diagnostics.get("live_readonly_methods_called") or [])
    for method in ("get_offer", "get_inventory_item", "get_item_condition_policies"):
        if method not in methods:
            refuse("missing_live_readonly_method", f"Live read-only diagnostics did not call {method}.")
    if diagnostics.get("live_readonly_errors"):
        refuse("live_readonly_errors_present", "Live read-only diagnostics returned errors.")

    if str(diagnostics.get("listing_id") or "").strip():
        refuse("listing_id_present", "listing_id must remain empty.")
    if str((diagnostics.get("existing_offer_diagnostics") or {}).get("status") or "").upper() != "UNPUBLISHED":
        refuse("offer_status_not_unpublished", "Existing offer must still be UNPUBLISHED.")
    if (diagnostics.get("inventory_item_diagnostics") or {}).get("inventory_item_exists") is not True:
        refuse("inventory_item_not_found", "Live inventory item must still exist.")
    expected_inventory_enum = condition_id_to_inventory_enum(
        diagnostics.get("local_condition_id") or "",
        default="",
    )
    if str(diagnostics.get("local_condition_id") or "") != "3000":
        refuse("condition_id_not_confirmed", "Current condition_id must remain 3000.")
    if str(diagnostics.get("local_inventory_condition_enum") or "") != expected_inventory_enum:
        refuse("inventory_condition_not_confirmed", f"Current inventory condition enum must remain {expected_inventory_enum}.")
    if str((diagnostics.get("inventory_item_diagnostics") or {}).get("condition_enum") or "") != expected_inventory_enum:
        refuse("condition_id_enum_mapping_mismatch", f"Live inventory condition must remain {expected_inventory_enum}.")
    if str(diagnostics.get("local_category_id") or "") != "14056":
        refuse("category_id_not_confirmed", "Current category_id must remain 14056.")
    if (diagnostics.get("category_condition_policy_diagnostics") or {}).get("live_policy_allows_condition") is not True:
        refuse("live_policy_does_not_allow_condition", "Live category policy must still allow condition 3000.")
    if (diagnostics.get("existing_offer_diagnostics") or {}).get("offer_exists") is not True:
        refuse("offer_not_found", "Existing offer must still exist.")
    if str(diagnostics.get("local_status") or "").lower() == "listed":
        refuse("item_already_listed", "Item must not be listed.")
    for finding in (diagnostics.get("condition_mapping_diagnostics") or {}).get("findings") or []:
        refuse(str(finding.get("code") or "condition_mapping_issue"), str(finding.get("message") or "Condition mapping mismatch detected."))

    if refresh_success_evidence["available"] and refresh_success_evidence["confirmed"] is not True:
        refuse("stale_offer_refresh_not_confirmed", "Current context does not confirm a successful stale-offer refresh.")

    return refusal_reasons


def _replacement_blocker_preview(
    *,
    diagnostics: dict,
    plan: PublishRepairPlanRecord | None,
    latest_publish_attempt_id: str,
) -> dict:
    return {
        "status": "needs_manual_review",
        "retry_allowed": False,
        "requires_review": True,
        "source": "stale_offer_refresh",
        "repair_layer": "post_refresh_publish_decision",
        "classified_error_code": REPLACEMENT_BLOCKING_ERROR_CODE,
        "risk_level": "high",
        "current_value": {
            "repair_plan_id": str(plan.id if plan else ""),
            "previous_classified_error_code": "invalid_category_condition",
            "latest_publish_attempt_id": latest_publish_attempt_id,
            "listing_id": str(diagnostics.get("listing_id") or ""),
            "offer_id": str(diagnostics.get("offer_id") or ""),
            "offer_status": str((diagnostics.get("existing_offer_diagnostics") or {}).get("status") or ""),
            "category_id": str(diagnostics.get("local_category_id") or ""),
            "condition_id": str(diagnostics.get("local_condition_id") or ""),
            "inventory_condition_enum": str(diagnostics.get("local_inventory_condition_enum") or ""),
        },
        "expected_value": {
            "publish_decision_required": True,
            "confirm_listing_id_empty": True,
            "confirm_offer_status": "UNPUBLISHED",
            "confirm_category_id": str(diagnostics.get("local_category_id") or ""),
            "confirm_condition_id": str(diagnostics.get("local_condition_id") or ""),
            "confirm_inventory_condition_enum": str(diagnostics.get("local_inventory_condition_enum") or ""),
        },
        "suggested_value": {
            "next_step": "separate_one_sku_publish_decision_required",
        },
        "suggested_actions": [
            "Review the refreshed unpublished offer.",
            "Run fresh read-only diagnostics again immediately before any separate one-SKU publish decision.",
            "Do not batch publish and do not auto-publish from supersede.",
        ],
        "message": (
            "Stale unpublished offer appears refreshed successfully, but a separate one-SKU publish approval is still required."
        ),
    }


def _resolve_refresh_success_evidence(diagnostics: dict) -> dict:
    for key in ("stale_offer_refresh_status", "latest_refresh_execution"):
        payload = diagnostics.get(key)
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("execution_status") or "")
        confirmed = bool(
            status == "refresh_completed"
            and payload.get("no_publish_performed") is True
        )
        return {
            "available": True,
            "confirmed": confirmed,
            "execution_status": status,
            "source": key,
        }
    return {
        "available": False,
        "confirmed": None,
        "execution_status": "",
        "source": "not_recorded_in_current_models",
    }


def build_supersede_payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _supersede_approval_template(
    *,
    sku: str,
    repair_plan_id: str,
    latest_publish_attempt_id: str,
    diagnostics: dict,
    payload_hash: str,
) -> dict:
    return {
        "sku": sku,
        "action_type": SUPERSEDE_ACTION_TYPE,
        "repair_plan_id": repair_plan_id,
        "latest_publish_attempt_id": latest_publish_attempt_id,
        "previous_classified_error_code": "invalid_category_condition",
        "confirm_listing_id_empty": True,
        "confirm_offer_status": "UNPUBLISHED",
        "confirm_category_id": str(diagnostics.get("local_category_id") or ""),
        "confirm_condition_id": str(diagnostics.get("local_condition_id") or ""),
        "confirm_inventory_condition_enum": str(diagnostics.get("local_inventory_condition_enum") or ""),
        "confirm_publish_remains_blocked": True,
        "confirm_replacement_classified_error_code": REPLACEMENT_BLOCKING_ERROR_CODE,
        "operator_approved": True,
        "typed_confirmation": SUPERSEDE_TYPED_CONFIRMATION,
        "approved_payload_hash": payload_hash,
    }


def _supersede_approval_refusals(*, approval_request: dict, expected: dict) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    approval = approval_request or {}
    checks = [
        ("sku", "approval_sku_mismatch"),
        ("action_type", "approval_action_type_mismatch"),
        ("repair_plan_id", "approval_repair_plan_id_mismatch"),
        ("latest_publish_attempt_id", "approval_latest_publish_attempt_id_mismatch"),
        ("previous_classified_error_code", "approval_previous_classified_error_code_mismatch"),
        ("confirm_offer_status", "approval_offer_status_mismatch"),
        ("confirm_category_id", "approval_category_id_mismatch"),
        ("confirm_condition_id", "approval_condition_id_mismatch"),
        ("confirm_inventory_condition_enum", "approval_inventory_condition_enum_mismatch"),
        ("confirm_replacement_classified_error_code", "approval_replacement_classified_error_code_mismatch"),
        ("typed_confirmation", "approval_typed_confirmation_mismatch"),
        ("approved_payload_hash", "approval_payload_hash_mismatch"),
    ]
    for field, code in checks:
        if str(approval.get(field) or "") != str(expected.get(field) or ""):
            refuse(code, f"Approval field {field} does not match current supersede preview.")

    if approval.get("confirm_listing_id_empty") is not True:
        refuse("approval_listing_id_empty_not_confirmed", "Approval must confirm listing_id is empty.")
    if approval.get("confirm_publish_remains_blocked") is not True:
        refuse("approval_publish_remains_blocked_not_confirmed", "Approval must confirm publish remains blocked after supersede.")
    if approval.get("operator_approved") is not True:
        refuse("approval_operator_not_approved", "Approval must include operator_approved=true.")

    return refusal_reasons


def _publish_decision_approval_refusals(*, approval_request: dict, expected: dict) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    approval = approval_request or {}
    checks = [
        ("sku", "approval_sku_mismatch"),
        ("action_type", "approval_action_type_mismatch"),
        ("repair_plan_id", "approval_repair_plan_id_mismatch"),
        ("latest_publish_attempt_id", "approval_latest_publish_attempt_id_mismatch"),
        ("offer_id", "approval_offer_id_mismatch"),
        ("confirm_offer_status", "approval_offer_status_mismatch"),
        ("confirm_category_id", "approval_category_id_mismatch"),
        ("confirm_condition_id", "approval_condition_id_mismatch"),
        ("confirm_inventory_condition_enum", "approval_inventory_condition_enum_mismatch"),
        ("confirm_blocker_classified_error_code", "approval_blocker_classified_error_code_mismatch"),
        ("confirm_merchant_location_key", "approval_merchant_location_key_mismatch"),
        ("confirm_fulfillment_policy_id", "approval_fulfillment_policy_id_mismatch"),
        ("confirm_payment_policy_id", "approval_payment_policy_id_mismatch"),
        ("confirm_return_policy_id", "approval_return_policy_id_mismatch"),
        ("typed_confirmation", "approval_typed_confirmation_mismatch"),
        ("approved_payload_hash", "approval_payload_hash_mismatch"),
    ]
    for field, code in checks:
        if str(approval.get(field) or "") != str(expected.get(field) or ""):
            refuse(code, f"Approval field {field} does not match current publish-decision preview.")

    if approval.get("confirm_listing_id_empty") is not True:
        refuse("approval_listing_id_empty_not_confirmed", "Approval must confirm listing_id is empty.")
    if approval.get("confirm_publish_existing_offer_only") is not True:
        refuse(
            "approval_publish_existing_offer_only_not_confirmed",
            "Approval must confirm only the existing offer will be published.",
        )
    if approval.get("confirm_publish_after_decision") is not True:
        refuse("approval_publish_after_decision_not_confirmed", "Approval must explicitly confirm publish_after_decision=true.")
    if approval.get("operator_approved") is not True:
        refuse("approval_operator_not_approved", "Approval must include operator_approved=true.")

    return refusal_reasons


def _build_live_executable_offer_payload(
    *,
    preview_payload: dict,
    diagnostics: dict,
) -> tuple[dict, list[dict]]:
    """Convert the read-only preview offer payload into a live-safe PUT payload."""
    payload = deepcopy(preview_payload or {})
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        if not any(reason["code"] == code for reason in refusal_reasons):
            refusal_reasons.append({"code": code, "message": message})

    live_offer = diagnostics.get("existing_offer_diagnostics") or {}
    live_policies = _extract_listing_policy_ids(live_offer.get("listing_policies") or {})
    configured_policies = _configured_listing_policy_ids()
    resolved_policies = {
        "fulfillmentPolicyId": live_policies.get("fulfillmentPolicyId") or configured_policies.get("fulfillmentPolicyId") or "",
        "paymentPolicyId": live_policies.get("paymentPolicyId") or configured_policies.get("paymentPolicyId") or "",
        "returnPolicyId": live_policies.get("returnPolicyId") or configured_policies.get("returnPolicyId") or "",
    }
    missing_policy_fields = [key for key, value in resolved_policies.items() if not _is_real_value(value)]
    if missing_policy_fields:
        refuse(
            "missing_real_listing_policy_ids",
            "Real fulfillment, payment, and return policy IDs are required before live offer refresh.",
        )

    listing_policies = deepcopy(payload.get("listingPolicies") or {})
    listing_policies.update(resolved_policies)
    payload["listingPolicies"] = listing_policies

    merchant_location_key = str(live_offer.get("merchant_location_key") or "").strip()
    if not _is_real_value(merchant_location_key):
        if merchant_location_key:
            payload["merchantLocationKey"] = merchant_location_key
        refuse(
            "merchant_location_key_unresolved",
            "A real merchantLocationKey from the existing live offer is required before live offer refresh.",
        )
    else:
        payload["merchantLocationKey"] = merchant_location_key

    if _contains_preview_placeholder(payload):
        refuse(
            "placeholder_listing_policy_detected",
            "Live offer refresh payload contains preview placeholder policy or merchant-location values.",
        )

    return payload, refusal_reasons


def _configured_listing_policy_ids() -> dict[str, str]:
    settings = get_settings()
    return {
        "fulfillmentPolicyId": str(settings.ebay_fulfillment_policy_id or "").strip(),
        "paymentPolicyId": str(settings.ebay_payment_policy_id or "").strip(),
        "returnPolicyId": str(settings.ebay_return_policy_id or "").strip(),
    }


def _extract_listing_policy_ids(policies: dict) -> dict[str, str]:
    return {
        "fulfillmentPolicyId": str(policies.get("fulfillmentPolicyId") or policies.get("fulfillment_policy_id") or "").strip(),
        "paymentPolicyId": str(policies.get("paymentPolicyId") or policies.get("payment_policy_id") or "").strip(),
        "returnPolicyId": str(policies.get("returnPolicyId") or policies.get("return_policy_id") or "").strip(),
    }


def _contains_preview_placeholder(value) -> bool:
    if isinstance(value, dict):
        return any(_contains_preview_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_preview_placeholder(item) for item in value)
    return str(value or "").strip() in PREVIEW_PLACEHOLDER_VALUES


def _is_real_value(value: str) -> bool:
    stripped = str(value or "").strip()
    return bool(stripped) and stripped not in PREVIEW_PLACEHOLDER_VALUES


def _call_put_safely(call: Callable[[], object], *, stage: str) -> dict:
    try:
        result = call()
    except Exception as exc:
        return {"ok": False, "stage": stage, "error": str(exc), "error_code": "EXCEPTION"}
    ok = getattr(result, "ok", None)
    if ok is not None:
        return {
            "ok": bool(ok),
            "stage": stage,
            "value": deepcopy(getattr(result, "value", None) or {}),
            "error": getattr(result, "error", None) or "",
            "error_code": getattr(result, "error_code", None) or "",
            "details": deepcopy(getattr(result, "details", None) or {}),
        }
    if isinstance(result, dict):
        return {"ok": bool(result.get("ok", True)), "stage": stage, "value": deepcopy(result)}
    return {"ok": True, "stage": stage, "value": result}


def _call_publish_existing_offer_safely(call: Callable[[], object]) -> dict:
    try:
        result = call()
    except Exception as exc:
        return {"ok": False, "stage": "publish_offer", "error": str(exc), "error_code": "EXCEPTION", "details": {}}
    ok = getattr(result, "ok", None)
    if ok is not None:
        return {
            "ok": bool(ok),
            "stage": "publish_offer",
            "value": deepcopy(getattr(result, "value", None) or {}),
            "error": getattr(result, "error", None) or "",
            "error_code": getattr(result, "error_code", None) or "",
            "details": deepcopy(getattr(result, "details", None) or {}),
        }
    if isinstance(result, dict):
        details = {
            "auth_headers_prepared": result.get("auth_headers_prepared"),
            "auth_token_source": result.get("auth_token_source"),
            "oauth_token_may_have_been_refreshed": result.get("oauth_token_may_have_been_refreshed"),
        }
        return {"ok": bool(result.get("ok", True)), "stage": "publish_offer", "value": deepcopy(result), "details": details}
    return {"ok": True, "stage": "publish_offer", "value": result, "details": {}}


def _as_failure_result(payload: dict):
    class _FailureResult:
        def __init__(self, data: dict) -> None:
            self.ok = False
            self.value = {}
            self.error = data.get("error", "")
            self.error_code = data.get("error_code", "")
            self.details = dict(data.get("details") or {})
            self.details.setdefault("body", data.get("details", {}).get("body", ""))
            self.details.setdefault("stage", data.get("stage", "publish_offer"))
            self.details.setdefault("offer_id", data.get("value", {}).get("offer_id", ""))

    return _FailureResult(payload)


def _run_post_refresh_diagnostics(provider: Callable[[], dict] | None) -> dict:
    if provider is None:
        return {}
    try:
        return provider() or {}
    except Exception as exc:
        return {
            "read_only": True,
            "no_mutation_performed": True,
            "error": str(exc),
        }


def _approval_refusals(
    *,
    approval_request: dict | None,
    sku: str,
    diagnostics: dict,
    draft: dict,
    remediation_type: str,
    publish_after_remediation: bool,
    require_approval_request: bool,
) -> list[dict]:
    refusal_reasons: list[dict] = []

    def refuse(code: str, message: str) -> None:
        refusal_reasons.append({"code": code, "message": message})

    if not require_approval_request:
        return refusal_reasons
    approval = approval_request or {}
    if not approval:
        refuse("missing_approval_request", "Explicit live-remediation approval request is required.")
        return refusal_reasons

    expected = {
        "sku": sku,
        "remediation_type": REMEDIATION_TYPE,
        "repair_plan_id": draft.get("repair_plan_id") or diagnostics.get("repair_plan_id") or "",
        "latest_publish_attempt_id": draft.get("latest_publish_attempt_id") or diagnostics.get("latest_publish_attempt_id") or "",
        "offer_id": draft.get("offer_id") or diagnostics.get("offer_id") or "",
        "confirm_offer_status": "UNPUBLISHED",
        "confirm_listing_id_empty": True,
        "confirm_category_id": str(diagnostics.get("local_category_id") or draft.get("category_id") or ""),
        "confirm_condition_id": str(diagnostics.get("local_condition_id") or draft.get("condition_id") or ""),
        "confirm_inventory_condition_enum": str(
            diagnostics.get("local_inventory_condition_enum") or draft.get("inventory_condition_enum") or ""
        ),
        "confirm_publish_after_remediation": False,
        "operator_approved": True,
        "typed_confirmation": REQUIRED_TYPED_CONFIRMATION,
        "approved_payload_hash": build_remediation_payload_hash(draft),
    }
    checks = [
        ("sku", "approval_sku_mismatch"),
        ("remediation_type", "approval_remediation_type_mismatch"),
        ("repair_plan_id", "approval_repair_plan_id_mismatch"),
        ("latest_publish_attempt_id", "approval_latest_publish_attempt_id_mismatch"),
        ("offer_id", "approval_offer_id_mismatch"),
        ("confirm_offer_status", "approval_offer_status_mismatch"),
        ("confirm_category_id", "approval_category_id_mismatch"),
        ("confirm_condition_id", "approval_condition_id_mismatch"),
        ("confirm_inventory_condition_enum", "approval_inventory_condition_enum_mismatch"),
        ("typed_confirmation", "approval_typed_confirmation_mismatch"),
        ("approved_payload_hash", "approval_payload_hash_mismatch"),
    ]
    for field, code in checks:
        if str(approval.get(field) or "") != str(expected[field] or ""):
            refuse(code, f"Approval field {field} does not match current preflight diagnostics.")

    if approval.get("confirm_listing_id_empty") is not True:
        refuse("approval_listing_id_empty_not_confirmed", "Approval must confirm listing_id is empty.")
    if approval.get("confirm_publish_after_remediation") is not False:
        refuse("approval_publish_after_remediation_not_false", "Approval must confirm publish_after_remediation is false.")
    if approval.get("operator_approved") is not True:
        refuse("approval_operator_not_approved", "Approval must include operator_approved=true.")
    if remediation_type != REMEDIATION_TYPE:
        refuse("requested_remediation_type_mismatch", "Requested remediation type does not match approval gate.")
    if publish_after_remediation:
        refuse("requested_publish_after_remediation_not_allowed", "Requested publish_after_remediation must be false.")

    return refusal_reasons


def build_remediation_payload_hash(draft: dict) -> str:
    """Hash the exact remediation payload previews an operator approved."""
    payload = {
        "inventory_payload": draft.get("intended_inventory_item_payload_preview") or {},
        "offer_payload": draft.get("intended_offer_payload_preview") or {},
        "call_sequence": draft.get("intended_call_sequence_preview") or [],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_bool(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)


def _approval_template(*, sku: str, diagnostics: dict, draft: dict, payload_hash: str) -> dict:
    return {
        "sku": sku,
        "remediation_type": REMEDIATION_TYPE,
        "repair_plan_id": draft.get("repair_plan_id") or diagnostics.get("repair_plan_id") or "",
        "latest_publish_attempt_id": draft.get("latest_publish_attempt_id") or diagnostics.get("latest_publish_attempt_id") or "",
        "offer_id": draft.get("offer_id") or diagnostics.get("offer_id") or "",
        "confirm_offer_status": "UNPUBLISHED",
        "confirm_listing_id_empty": True,
        "confirm_category_id": str(diagnostics.get("local_category_id") or draft.get("category_id") or ""),
        "confirm_condition_id": str(diagnostics.get("local_condition_id") or draft.get("condition_id") or ""),
        "confirm_inventory_condition_enum": str(
            diagnostics.get("local_inventory_condition_enum") or draft.get("inventory_condition_enum") or ""
        ),
        "confirm_publish_after_remediation": False,
        "operator_approved": True,
        "typed_confirmation": REQUIRED_TYPED_CONFIRMATION,
        "approved_payload_hash": payload_hash,
    }


def _base_result(
    *,
    sku: str,
    diagnostics: dict,
    draft: dict,
    operator_approved: bool,
    publish_after_remediation: bool,
    live_remediation_enabled: bool,
    approval_request: dict | None,
    preflight_recheck_performed: bool,
    executor: StaleOfferRemediationExecutor | None,
) -> dict:
    return {
        "sku": sku,
        "remediation_type": REMEDIATION_TYPE,
        "mode": "mock_only" if executor is not None else "live_disabled",
        "live_execution_enabled": False,
        "live_remediation_feature_enabled": bool(live_remediation_enabled),
        "operator_approval_required": True,
        "operator_approval_received": bool(operator_approved),
        "approval_request_received": bool(approval_request),
        "typed_confirmation_required": REQUIRED_TYPED_CONFIRMATION,
        "approved_payload_hash": build_remediation_payload_hash(draft),
        "preflight_recheck_performed": bool(preflight_recheck_performed),
        "no_mutation_performed": True,
        "no_live_mutation_performed": True,
        "real_ebay_mutation_performed": False,
        "mocked_mutation_performed": False,
        "publish_after_remediation": bool(publish_after_remediation),
        "repair_plan_id": draft.get("repair_plan_id") or diagnostics.get("repair_plan_id") or "",
        "latest_publish_attempt_id": draft.get("latest_publish_attempt_id") or diagnostics.get("latest_publish_attempt_id") or "",
        "offer_id": draft.get("offer_id") or diagnostics.get("offer_id") or "",
        "listing_id": draft.get("listing_id") or diagnostics.get("listing_id") or "",
        "offer_status": draft.get("offer_status") or (diagnostics.get("existing_offer_diagnostics") or {}).get("status") or "",
        "inventory_payload_preview": deepcopy(draft.get("intended_inventory_item_payload_preview") or {}),
        "offer_payload_preview": deepcopy(draft.get("intended_offer_payload_preview") or {}),
        "call_sequence": deepcopy(draft.get("intended_call_sequence_preview") or []),
        "audit_log_preview": {
            "audit_event_type": "stale_offer_refresh",
            "sku": sku,
            "repair_plan_id": draft.get("repair_plan_id") or diagnostics.get("repair_plan_id") or "",
            "latest_publish_attempt_id": draft.get("latest_publish_attempt_id") or diagnostics.get("latest_publish_attempt_id") or "",
            "offer_id": draft.get("offer_id") or diagnostics.get("offer_id") or "",
            "listing_id_before": draft.get("listing_id") or diagnostics.get("listing_id") or "",
            "offer_status_before": draft.get("offer_status") or (diagnostics.get("existing_offer_diagnostics") or {}).get("status") or "",
            "category_id": diagnostics.get("local_category_id") or draft.get("category_id") or "",
            "condition_id": diagnostics.get("local_condition_id") or draft.get("condition_id") or "",
            "inventory_condition_enum": diagnostics.get("local_inventory_condition_enum") or draft.get("inventory_condition_enum") or "",
            "approved_payload_hash": build_remediation_payload_hash(draft),
            "publish_after_remediation": bool(publish_after_remediation),
            "no_publish_performed": True,
            "real_ebay_mutation_performed": False,
        },
        "execution_status": "preview_only",
        "refusal_reasons": [],
        "next_recommended_action": "Review the remediation preview; do not publish without separate approval.",
    }
