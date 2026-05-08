from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session

from apps.api.src.services.publish_compatibility import get_category_condition_policy
from apps.api.src.services.publish_repair import get_publish_repair_blocker
from packages.data.src.repositories.item_repo import ItemRepository
from packages.ebay.src.inventory_client import EbayInventoryClient

_READONLY_METHODS = {
    "get_offer",
    "get_inventory_item",
    "get_item_condition_policies",
}


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
            "no_mutation_performed": True,
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

    live = _build_empty_live_diagnostics()
    offer_diagnostics = _local_offer_diagnostics(
        item,
        condition_diagnostics=condition_diagnostics,
        existing_offer_id_detected=existing_offer_id_detected,
        inventory_condition_enum=inventory_condition_enum,
        stale_offer_hypothesis=stale_offer_hypothesis,
    )
    inventory_diagnostics = _local_inventory_diagnostics(item, inventory_condition_enum)
    category_policy_diagnostics = _local_policy_diagnostics(
        category_id=category_id,
        condition_id=condition_id,
        policy=policy,
        allowed_condition_ids=allowed_condition_ids,
        local_policy_allows_condition=local_policy_allows_condition,
        local_policy_status=local_policy_status,
        policy_conflict=policy_conflict,
    )

    if allow_live_readonly:
        live = _run_live_readonly_diagnostics(
            item,
            category_id=category_id,
            condition_id=condition_id,
            local_inventory_condition_enum=inventory_condition_enum,
            local_allowed_condition_ids=allowed_condition_ids,
        )
        offer_diagnostics = live["offer_diagnostics"]
        inventory_diagnostics = live["inventory_item_diagnostics"]
        category_policy_diagnostics = _merge_live_policy_diagnostics(
            category_policy_diagnostics,
            live["category_condition_policy_diagnostics"],
            condition_id=condition_id,
        )
        stale_offer_hypothesis = _coalesce_live_stale_offer_hypothesis(
            local_value=stale_offer_hypothesis,
            offer_diagnostics=offer_diagnostics,
        )
        category_policy_hypothesis = bool(
            category_policy_hypothesis
            or category_policy_diagnostics.get("live_policy_disagrees_with_local_policy")
            or category_policy_diagnostics.get("live_policy_allows_condition") is False
        )

    if repair_blocker.get("blocked_by_repair_queue"):
        recommended_next_action = (
            "Do not retry publish. Use read-only diagnostics to decide whether the existing offer is stale or category policy is wrong."
        )
    elif category_policy_hypothesis:
        recommended_next_action = "Verify category-condition policy before publish."
    else:
        recommended_next_action = "Review diagnostics before any live publish attempt."

    return {
        "sku": normalized,
        "found": True,
        "read_only": True,
        "no_mutation_performed": True,
        "live_readonly_requested": bool(allow_live_readonly),
        "live_readonly_performed": bool(live["methods_called"]),
        "live_readonly_methods_called": live["methods_called"],
        "live_readonly_unavailable": live["unavailable"],
        "live_readonly_errors": live["errors"],
        "live_readonly_warning": (
            ""
            if allow_live_readonly and live["methods_called"]
            else (
                "Live read-only eBay inspection was requested but no read methods were available for this SKU."
                if allow_live_readonly
                else "Live read-only eBay inspection was not requested; no external call was made."
            )
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
        "existing_offer_diagnostics": offer_diagnostics,
        "inventory_item_diagnostics": inventory_diagnostics,
        "category_condition_policy_diagnostics": category_policy_diagnostics,
        "repair_queue_blocker": repair_blocker,
    }


def _build_empty_live_diagnostics() -> dict:
    return {
        "methods_called": [],
        "unavailable": [],
        "errors": [],
        "offer_diagnostics": {},
        "inventory_item_diagnostics": {},
        "category_condition_policy_diagnostics": {},
    }


def _run_live_readonly_diagnostics(
    item,
    *,
    category_id: str,
    condition_id: str,
    local_inventory_condition_enum: str,
    local_allowed_condition_ids: list[str],
) -> dict:
    client = EbayInventoryClient()
    live = _build_empty_live_diagnostics()

    offer_id = str(item.offer_id or "").strip()
    if offer_id:
        result = _call_readonly(client, "get_offer", offer_id)
        live["methods_called"].append("get_offer")
        if result.ok:
            live["offer_diagnostics"] = _live_offer_diagnostics(
                result.value or {},
                local_category_id=category_id,
                local_condition_id=condition_id,
                offer_id=offer_id,
            )
        else:
            live["errors"].append(_error_entry("get_offer", result))
            live["offer_diagnostics"] = _unavailable_offer_diagnostics(offer_id, result.error or "")
    else:
        live["unavailable"].append({"method": "get_offer", "reason": "No local offer_id is stored."})
        live["offer_diagnostics"] = _unavailable_offer_diagnostics("", "No local offer_id is stored.")

    result = _call_readonly(client, "get_inventory_item", item.sku or "")
    live["methods_called"].append("get_inventory_item")
    if result.ok:
        live["inventory_item_diagnostics"] = _live_inventory_diagnostics(
            result.value or {},
            sku=str(item.sku or ""),
            local_inventory_condition_enum=local_inventory_condition_enum,
        )
    else:
        live["errors"].append(_error_entry("get_inventory_item", result))
        live["inventory_item_diagnostics"] = _unavailable_inventory_diagnostics(item.sku or "", result.error or "")

    if category_id:
        result = _call_readonly(client, "get_item_condition_policies", category_id)
        live["methods_called"].append("get_item_condition_policies")
        if result.ok:
            live["category_condition_policy_diagnostics"] = _live_policy_diagnostics(
                result.value or {},
                category_id=category_id,
                condition_id=condition_id,
                local_allowed_condition_ids=local_allowed_condition_ids,
            )
        else:
            live["errors"].append(_error_entry("get_item_condition_policies", result))
            live["category_condition_policy_diagnostics"] = _unavailable_policy_diagnostics(
                category_id,
                condition_id,
                result.error or "",
            )
    else:
        live["unavailable"].append({"method": "get_item_condition_policies", "reason": "No local category_id is stored."})
        live["category_condition_policy_diagnostics"] = _unavailable_policy_diagnostics(category_id, condition_id, "No local category_id is stored.")

    return live


def _call_readonly(client: EbayInventoryClient, method_name: str, *args):
    if method_name not in _READONLY_METHODS:
        raise RuntimeError(f"Refusing non-read-only diagnostic method: {method_name}")
    method: Callable = getattr(client, method_name)
    return method(*args)


def _error_entry(method: str, result) -> dict:
    return {
        "method": method,
        "error": result.error or "",
        "error_code": result.error_code or "",
        "details": result.details or {},
    }


def _local_offer_diagnostics(
    item,
    *,
    condition_diagnostics: dict,
    existing_offer_id_detected: bool,
    inventory_condition_enum: str,
    stale_offer_hypothesis: bool,
) -> dict:
    return {
        "source": "local_only",
        "read_available": False,
        "live_readonly_performed": False,
        "message": "eBay read-only offer inspection was not requested; no mutation performed.",
        "local_system_thinks_existing_offer": existing_offer_id_detected,
        "existing_offer_publish_flow": _existing_offer_flow(existing_offer_id_detected),
        "failed_stage": condition_diagnostics.get("failed_stage") or "",
        "offer_id": str(item.offer_id or ""),
        "current_category_id": str(item.ebay_category_id or ""),
        "current_condition_id": str(item.condition_id or ""),
        "current_inventory_condition_enum": inventory_condition_enum,
        "stale_existing_offer_hypothesis": stale_offer_hypothesis,
        "stale_existing_offer_supported_by_live_read": "unknown",
    }


def _unavailable_offer_diagnostics(offer_id: str, reason: str) -> dict:
    return {
        "source": "live_readonly",
        "read_available": False,
        "live_readonly_performed": True,
        "offer_id": offer_id,
        "offer_exists": "unknown",
        "message": reason,
        "stale_existing_offer_supported_by_live_read": "unknown",
    }


def _live_offer_diagnostics(payload: dict, *, local_category_id: str, local_condition_id: str, offer_id: str) -> dict:
    category_id = str(payload.get("categoryId") or payload.get("category_id") or "")
    condition_id = str(payload.get("conditionId") or payload.get("condition_id") or "")
    category_differs = bool(category_id and local_category_id and category_id != local_category_id)
    condition_differs = bool(condition_id and local_condition_id and condition_id != local_condition_id)
    return {
        "source": "live_readonly",
        "read_available": True,
        "live_readonly_performed": True,
        "offer_id": str(payload.get("offerId") or offer_id),
        "offer_exists": True,
        "status": str(payload.get("status") or ""),
        "category_id": category_id,
        "condition_id": condition_id,
        "marketplace_id": str(payload.get("marketplaceId") or ""),
        "listing_policies": payload.get("listingPolicies") or {},
        "category_differs_from_local": category_differs,
        "condition_differs_from_local": condition_differs,
        "condition_fields_available": bool(condition_id),
        "stale_existing_offer_supported_by_live_read": bool(category_differs or condition_differs) if (category_id or condition_id) else "unknown",
        "raw_summary": {
            "sku": payload.get("sku") or "",
            "format": payload.get("format") or "",
            "available_quantity": payload.get("availableQuantity"),
        },
    }


def _existing_offer_flow(existing_offer_id_detected: bool) -> dict:
    return {
        "updates_inventory_item_before_publish": True,
        "updates_existing_offer_before_publish": False,
        "publishes_existing_offer_id_directly": existing_offer_id_detected,
    }


def _local_inventory_diagnostics(item, inventory_condition_enum: str) -> dict:
    return {
        "source": "local_only",
        "read_available": False,
        "live_readonly_performed": False,
        "sku": str(item.sku or ""),
        "local_inventory_condition_enum": inventory_condition_enum,
        "message": "eBay read-only inventory item inspection was not requested; no mutation performed.",
    }


def _unavailable_inventory_diagnostics(sku: str, reason: str) -> dict:
    return {
        "source": "live_readonly",
        "read_available": False,
        "live_readonly_performed": True,
        "sku": sku,
        "inventory_item_exists": "unknown",
        "message": reason,
    }


def _live_inventory_diagnostics(payload: dict, *, sku: str, local_inventory_condition_enum: str) -> dict:
    product = payload.get("product") or {}
    image_urls = [str(url) for url in product.get("imageUrls") or []]
    ebay_condition = str(payload.get("condition") or "")
    return {
        "source": "live_readonly",
        "read_available": True,
        "live_readonly_performed": True,
        "sku": str(payload.get("sku") or sku),
        "inventory_item_exists": True,
        "condition_enum": ebay_condition,
        "condition_description": str(payload.get("conditionDescription") or ""),
        "title": str(product.get("title") or ""),
        "image_urls": image_urls,
        "image_urls_are_public_hosted": all(url.startswith(("http://", "https://")) for url in image_urls),
        "condition_differs_from_local": bool(ebay_condition and ebay_condition != local_inventory_condition_enum),
    }


def _local_policy_diagnostics(
    *,
    category_id: str,
    condition_id: str,
    policy: dict,
    allowed_condition_ids: list[str],
    local_policy_allows_condition: bool,
    local_policy_status: str,
    policy_conflict: bool,
) -> dict:
    return {
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
    }


def _unavailable_policy_diagnostics(category_id: str, condition_id: str, reason: str) -> dict:
    return {
        "source": "live_readonly_metadata",
        "read_available": False,
        "live_readonly_metadata_performed": True,
        "category_id": category_id,
        "condition_id": condition_id,
        "message": reason,
    }


def _live_policy_diagnostics(payload: dict, *, category_id: str, condition_id: str, local_allowed_condition_ids: list[str]) -> dict:
    conditions = _extract_condition_policy_conditions(payload)
    live_allowed_ids = [condition["id"] for condition in conditions if condition["id"]]
    live_allows_condition = condition_id in live_allowed_ids if live_allowed_ids else None
    return {
        "source": "live_readonly_metadata",
        "read_available": True,
        "live_readonly_metadata_performed": True,
        "category_id": category_id,
        "condition_id": condition_id,
        "allowed_condition_ids": live_allowed_ids,
        "allowed_conditions": conditions,
        "live_policy_allows_condition": live_allows_condition,
        "local_policy_agrees_with_live_policy": set(local_allowed_condition_ids) == set(live_allowed_ids) if live_allowed_ids else "unknown",
        "live_policy_disagrees_with_local_policy": bool(live_allowed_ids and set(local_allowed_condition_ids) != set(live_allowed_ids)),
        "live_metadata_supports_changing_condition": live_allows_condition is False,
        "live_metadata_supports_changing_category": "unknown",
    }


def _merge_live_policy_diagnostics(local: dict, live: dict, *, condition_id: str) -> dict:
    if not live:
        return local
    merged = dict(local)
    merged.update(live)
    if live.get("read_available"):
        live_allows = live.get("live_policy_allows_condition")
        if live_allows is True:
            merged["local_policy_status"] = "confirmed_by_live_readonly_metadata"
        elif live_allows is False:
            merged["local_policy_status"] = "suspect_or_stale"
            merged["rejected_condition_id"] = condition_id
    return merged


def _extract_condition_policy_conditions(payload: dict) -> list[dict]:
    policies = payload.get("itemConditionPolicies") or payload.get("item_condition_policies") or []
    raw_conditions = []
    for policy in policies:
        raw_conditions.extend(policy.get("itemConditions") or policy.get("item_conditions") or [])
    if not raw_conditions:
        raw_conditions = payload.get("itemConditions") or payload.get("item_conditions") or []

    conditions = []
    seen = set()
    for condition in raw_conditions:
        condition_id = str(condition.get("conditionId") or condition.get("condition_id") or condition.get("id") or "")
        if not condition_id or condition_id in seen:
            continue
        seen.add(condition_id)
        conditions.append(
            {
                "id": condition_id,
                "name": str(
                    condition.get("conditionDescription")
                    or condition.get("conditionName")
                    or condition.get("name")
                    or ""
                ),
            }
        )
    return conditions


def _coalesce_live_stale_offer_hypothesis(*, local_value: bool, offer_diagnostics: dict) -> bool:
    supported = offer_diagnostics.get("stale_existing_offer_supported_by_live_read")
    if supported is True:
        return True
    if supported is False:
        return bool(local_value)
    return bool(local_value)
