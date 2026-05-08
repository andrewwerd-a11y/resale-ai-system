from __future__ import annotations

from copy import deepcopy
from typing import Protocol

REMEDIATION_TYPE = "refresh_existing_unpublished_offer"


class StaleOfferRemediationExecutor(Protocol):
    """Test-only executor for previewed stale-offer remediation calls."""

    def put_inventory_item(self, sku: str, payload: dict) -> dict:
        ...

    def put_offer(self, offer_id: str, payload: dict) -> dict:
        ...


def execute_refresh_existing_unpublished_offer(
    *,
    sku: str,
    diagnostics: dict,
    operator_approved: bool,
    remediation_type: str = REMEDIATION_TYPE,
    publish_after_remediation: bool = False,
    execute_live: bool = False,
    executor: StaleOfferRemediationExecutor | None = None,
) -> dict:
    """Recheck and mock-execute a stale unpublished-offer remediation draft.

    This service intentionally has no live eBay client dependency. Without an
    injected test executor, it always fails closed with live execution disabled.
    """
    normalized_sku = str(sku or "").strip().upper()
    draft = diagnostics.get("stale_offer_remediation_draft") or {}
    refusal_reasons = _eligibility_refusals(
        sku=normalized_sku,
        diagnostics=diagnostics,
        draft=draft,
        operator_approved=operator_approved,
        remediation_type=remediation_type,
        publish_after_remediation=publish_after_remediation,
    )
    base = _base_result(
        sku=normalized_sku,
        diagnostics=diagnostics,
        draft=draft,
        operator_approved=operator_approved,
        publish_after_remediation=publish_after_remediation,
        executor=executor,
    )

    if execute_live:
        return base | {
            "code": "live_execution_disabled",
            "execution_status": "live_execution_disabled",
            "refusal_reasons": refusal_reasons
            + [
                {
                    "code": "live_execution_disabled",
                    "message": "Live stale-offer remediation execution is disabled in this phase.",
                }
            ],
            "next_recommended_action": "Use mock-only remediation tests or preview diagnostics; live execution requires a later approved phase.",
        }

    if refusal_reasons:
        return base | {
            "execution_status": "blocked",
            "refusal_reasons": refusal_reasons,
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


def _base_result(
    *,
    sku: str,
    diagnostics: dict,
    draft: dict,
    operator_approved: bool,
    publish_after_remediation: bool,
    executor: StaleOfferRemediationExecutor | None,
) -> dict:
    return {
        "sku": sku,
        "remediation_type": REMEDIATION_TYPE,
        "mode": "mock_only" if executor is not None else "live_disabled",
        "live_execution_enabled": False,
        "operator_approval_required": True,
        "operator_approval_received": bool(operator_approved),
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
        "execution_status": "preview_only",
        "refusal_reasons": [],
        "next_recommended_action": "Review the remediation preview; do not publish without separate approval.",
    }
