from __future__ import annotations

from sqlmodel import Session

from apps.api.src.services.publish_compatibility import get_category_condition_policy
from apps.api.src.services.publish_repair import get_publish_repair_blocker
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.inventory_client import EbayInventoryClient


def build_publish_diagnostics(
    session: Session,
    sku: str,
    *,
    allow_live_readonly: bool = False,
) -> dict:
    """Return publish-blocker diagnostics without mutating eBay or local data."""
    normalized = (sku or "").strip().upper()
    item = ItemRepository(session).get_by_sku(normalized)
    if not item:
        return {
            "sku": normalized,
            "found": False,
            "read_only": True,
            "live_readonly_requested": bool(allow_live_readonly),
            "live_readonly_performed": False,
            "error": f"Item {normalized} not found",
        }

    repair_blocker = get_publish_repair_blocker(session, normalized)
    condition_diagnostics = repair_blocker.get("condition_diagnostics") or {}
    condition_id = str(item.condition_id or "")
    category_id = str(item.ebay_category_id or "")
    policy = get_category_condition_policy(category_id)
    allowed_condition_ids = [str(value) for value in policy.get("allowed_condition_ids") or []]
    local_policy_allows_condition = bool(condition_id and condition_id in allowed_condition_ids)
    policy_conflict = bool(repair_blocker.get("policy_conflict"))
    local_policy_status = "suspect_or_stale" if policy_conflict else "current_local_policy"
    existing_offer_id_detected = bool(
        str(item.offer_id or "").strip()
        and not str(item.listing_id or "").strip()
        and str(item.status or "") != "listed"
    )
    planned_action = "publish_existing_offer" if existing_offer_id_detected else "create_offer_then_publish"
    inventory_condition_enum = EbayInventoryClient._resolve_inventory_condition(item)
    stale_offer_hypothesis = bool(
        condition_diagnostics.get("stale_existing_offer_hypothesis")
        or (
            existing_offer_id_detected
            and str(condition_diagnostics.get("failed_stage") or "") == "publish_offer"
        )
    )

    category_policy_hypothesis = bool(
        policy_conflict
        or (
            local_policy_allows_condition
            and repair_blocker.get("classified_error_code") == "invalid_category_condition"
        )
    )

    if repair_blocker.get("blocked_by_repair_queue"):
        recommended_next_action = (
            "Do not retry publish. Run explicit read-only eBay offer/category diagnostics before choosing a remediation."
        )
    elif category_policy_hypothesis:
        recommended_next_action = "Verify category-condition policy with read-only eBay metadata before publish."
    else:
        recommended_next_action = "Review diagnostics before any live publish attempt."

    return {
        "sku": normalized,
        "found": True,
        "read_only": True,
        "live_readonly_requested": bool(allow_live_readonly),
        "live_readonly_performed": False,
        "live_readonly_warning": (
            "Live read-only eBay inspection was requested but is not implemented in this phase; no external call was made."
            if allow_live_readonly
            else "Live read-only eBay inspection was not requested; no external call was made."
        ),
        "local_status": str(item.status or ""),
        "local_category_id": category_id,
        "local_category_name": str(item.ebay_category_name or ""),
        "local_condition_id": condition_id,
        "local_inventory_condition_enum": inventory_condition_enum,
        "offer_id": str(item.offer_id or ""),
        "listing_id": str(item.listing_id or ""),
        "planned_action": planned_action,
        "existing_offer_id_detected": existing_offer_id_detected,
        "repair_plan_id": repair_blocker.get("repair_plan_id") or "",
        "latest_publish_attempt_id": repair_blocker.get("latest_publish_attempt_id") or "",
        "repair_status": repair_blocker.get("repair_status") or {},
        "retry_allowed": bool(repair_blocker.get("retry_allowed")),
        "classified_error_code": repair_blocker.get("classified_error_code") or "",
        "blocked_by_repair_queue": bool(repair_blocker.get("blocked_by_repair_queue")),
        "stale_existing_offer_hypothesis": stale_offer_hypothesis,
        "category_policy_hypothesis": category_policy_hypothesis,
        "recommended_next_action": recommended_next_action,
        "existing_offer_diagnostics": {
            "source": "local_only",
            "read_available": False,
            "live_readonly_performed": False,
            "message": "eBay read-only offer inspection by offer_id is not available in this phase; no mutation performed.",
            "local_system_thinks_existing_offer": existing_offer_id_detected,
            "existing_offer_publish_flow": {
                "updates_inventory_item_before_publish": True,
                "updates_existing_offer_before_publish": False,
                "publishes_existing_offer_id_directly": existing_offer_id_detected,
            },
            "failed_stage": condition_diagnostics.get("failed_stage") or "",
            "offer_id": str(item.offer_id or ""),
            "current_category_id": category_id,
            "current_condition_id": condition_id,
            "current_inventory_condition_enum": inventory_condition_enum,
            "stale_existing_offer_hypothesis": stale_offer_hypothesis,
        },
        "category_condition_policy_diagnostics": {
            "source": policy.get("source") or "",
            "category_id": category_id,
            "condition_id": condition_id,
            "allowed_condition_ids": allowed_condition_ids,
            "local_policy_allows_condition": local_policy_allows_condition,
            "local_policy_status": local_policy_status,
            "policy_conflict": policy_conflict,
            "contradicted_by": "ebay_error" if policy_conflict else "",
            "rejected_condition_id": condition_id if policy_conflict else "",
            "rejected_category_id": category_id if policy_conflict else "",
            "live_readonly_metadata_performed": False,
        },
        "repair_queue_blocker": repair_blocker,
    }
