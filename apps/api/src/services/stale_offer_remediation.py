from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from collections.abc import Callable
from typing import Protocol

REMEDIATION_TYPE = "refresh_existing_unpublished_offer"
REQUIRED_TYPED_CONFIRMATION = "REFRESH UNPUBLISHED OFFER ONLY"


class StaleOfferRemediationExecutor(Protocol):
    """Test-only executor for previewed stale-offer remediation calls."""

    def put_inventory_item(self, sku: str, payload: dict) -> dict:
        ...

    def put_offer(self, offer_id: str, payload: dict) -> dict:
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
    if str(draft.get("inventory_condition_enum") or "") != "USED_GOOD":
        refuse("inventory_condition_not_used_good", "Inventory condition enum must be USED_GOOD.")

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
