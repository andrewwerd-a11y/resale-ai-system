from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.domain.src.entities.item import Item
from packages.ebay.src.category_intelligence import CategoryIntelligence, CategoryTemplate
from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
from packages.ebay.src.aspect_validation import validate_aspects
from packages.ebay.src.inventory_client import CONDITION_MAP as EBAY_CONDITION_MAP
from packages.ebay.src.inventory_client import EbayInventoryClient
from packages.ebay.src.photo_uploader import PhotoUploader
from packages.ebay.src.public_image_urls import extract_public_image_urls
from packages.testing.src.e2e_guard import is_e2e_sku_allowed, is_route_guard_enabled


@dataclass
class PublishReadinessResult:
    sku: str
    ready: bool
    checks: list[dict]
    blockers: list[str]
    warnings: list[str]
    required_actions: list[str]

    def as_dict(self) -> dict:
        return {
            "sku": self.sku,
            "ready": self.ready,
            "checks": self.checks,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "required_actions": self.required_actions,
        }


PUBLISHABLE_STATUSES = {ItemStatus.APPROVED, ItemStatus.EXPORT_READY}
CategoryTemplateProvider = Callable[[Item], Result[CategoryTemplate]]
SellerPolicyProvider = Callable[[], dict]


def evaluate_publish_readiness(
    item: Item,
    *,
    category_template_provider: CategoryTemplateProvider | None = None,
    seller_policy_provider: SellerPolicyProvider | None = None,
) -> PublishReadinessResult:
    sku = (item.sku or "").strip().upper()
    blockers: list[str] = []
    warnings: list[str] = []
    required_actions: list[str] = []
    checks: list[dict] = []
    settings = get_settings()

    def add_check(
        name: str,
        ok: bool,
        *,
        detail: str,
        blocking: bool = False,
        action: str | None = None,
        warning: str | None = None,
        context: dict | None = None,
    ) -> None:
        payload = {
            "name": name,
            "ok": ok,
            "blocking": blocking,
            "detail": detail,
        }
        if context is not None:
            payload["context"] = context
        checks.append(payload)
        if blocking and not ok:
            blockers.append(detail)
        if warning and not ok:
            warnings.append(warning)
        if action and not ok and action not in required_actions:
            required_actions.append(action)

    def add_warning(message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    def add_required_action(message: str) -> None:
        if message not in required_actions:
            required_actions.append(message)

    guard_enabled = is_route_guard_enabled()
    sku_allowed = is_e2e_sku_allowed(sku) if sku else False
    add_check(
        "environment_sku_guard",
        (not guard_enabled) or sku_allowed,
        detail=(
            "SKU is approved for guarded E2E routes."
            if (not guard_enabled) or sku_allowed
            else "SKU is not in APPROVED_E2E_SKUS while E2E_ROUTE_GUARD_ENABLED is active."
        ),
        blocking=guard_enabled,
        action="Use an approved E2E SKU or disable the route guard for this environment.",
    )

    add_check(
        "item_exists",
        True,
        detail="Item record found.",
    )

    status_ok = (item.status or "") in PUBLISHABLE_STATUSES
    add_check(
        "publishable_status",
        status_ok,
        detail=(
            f"Item status '{item.status}' is publishable."
            if status_ok
            else f"Item status '{item.status}' is not publishable. Expected approved or export_ready."
        ),
        blocking=True,
        action="Move the item to approved or export_ready status before publishing.",
    )

    blocked_from_publish = (item.needs_review or False) or ((item.status or "") == ItemStatus.REJECTED)
    add_check(
        "not_blocked_from_publish",
        not blocked_from_publish,
        detail=(
            "Item is not blocked from publish."
            if not blocked_from_publish
            else "Item is blocked from publish because it still needs review or is rejected."
        ),
        blocking=True,
        action="Resolve review blockers or change the item status before publishing.",
    )

    required_fields = {
        "title": item.title_final or item.title_raw,
        "description": item.description_final,
        "price": item.list_price,
        "category_id": item.ebay_category_id,
        "condition_id": item.condition_id,
    }
    for field_name, value in required_fields.items():
        present = _has_value(value)
        add_check(
            f"required_{field_name}",
            present,
            detail=(
                f"{field_name} is present."
                if present
                else f"Missing required field: {field_name}."
            ),
            blocking=True,
            action=f"Populate the item's {field_name} before publishing.",
        )

    image_paths = [str(path).strip() for path in (item.image_paths or []) if str(path).strip()]
    hosted_photo_urls = extract_public_image_urls(image_paths)
    local_photo_candidates = [path for path in image_paths if path not in hosted_photo_urls]
    local_photo_files = [path for path in local_photo_candidates if Path(path).is_file()]
    missing_photo_files = [path for path in local_photo_candidates if not Path(path).is_file()]
    has_photos = bool(hosted_photo_urls or local_photo_files)
    add_check(
        "photos_present",
        has_photos,
        detail=(
            f"{len(hosted_photo_urls) + len(local_photo_files)} usable photo source(s) available."
            if has_photos
            else "No photos are attached to this item."
        ),
        blocking=True,
        action="Attach local photos or hosted photo URLs before publishing.",
    )

    cloudinary_config_present = PhotoUploader().is_configured()
    needs_hosting = bool(local_photo_files) and not hosted_photo_urls
    photo_hosting_ok = bool(hosted_photo_urls) or bool(local_photo_files)
    photo_warning = None
    if needs_hosting and not cloudinary_config_present:
        photo_warning = "Local photos exist, but Cloudinary is not configured for hosting yet."
    elif needs_hosting:
        photo_warning = "Local photos exist, but hosted photo URLs still need to be prepared."
    elif missing_photo_files:
        photo_warning = "Some stored local photo paths no longer exist on disk."

    add_check(
        "photo_hosting_readiness",
        photo_hosting_ok,
        detail=(
            f"{len(hosted_photo_urls)} hosted photo URL(s) already present."
            if hosted_photo_urls
            else (
                f"{len(local_photo_files)} local photo file(s) are available and still need hosting."
                if local_photo_files
                else "No hosted photo URLs or local photo files are currently available."
            )
        ),
        blocking=not photo_hosting_ok,
        warning=photo_warning,
        action=(
            "Host local photos before sandbox or live publish."
            if needs_hosting
            else "Restore or attach valid photo files before publishing."
        ),
        context={
            "has_local_photos": bool(local_photo_files),
            "has_hosted_photo_urls": bool(hosted_photo_urls),
            "needs_hosting": needs_hosting,
            "missing_photo_files": missing_photo_files,
            "cloudinary_config_present": cloudinary_config_present,
        },
    )
    if needs_hosting:
        add_required_action("Host local photos before sandbox or live publish.")
    if photo_warning and needs_hosting:
        add_warning(photo_warning)

    condition_key = _normalize_condition_key(item.condition_id)
    add_check(
        "condition_id_supported",
        bool(condition_key),
        detail=(
            f"Condition ID '{item.condition_id}' maps to a supported eBay condition."
            if condition_key
            else f"Condition ID '{item.condition_id}' is not recognized by the eBay payload builder."
        ),
        blocking=True,
        action="Choose a supported eBay condition ID before publishing.",
    )

    aspect_validation = validate_aspects(EbayInventoryClient()._collect_item_specifics(item))
    add_check(
        "aspect_value_lengths",
        aspect_validation["ok"],
        detail=(
            "All eBay aspect values are within length limits."
            if aspect_validation["ok"]
            else "One or more eBay aspect values exceed eBay length limits."
        ),
        blocking=not aspect_validation["ok"],
        warning=(aspect_validation["warnings"][0] if aspect_validation["warnings"] else None),
        action="Shorten or manually correct the flagged aspect values before publishing.",
        context={
            "normalized_aspects": aspect_validation["normalized_aspects"],
            "issues": aspect_validation["issues"],
            "warnings": aspect_validation["warnings"],
            "max_length": 65,
        },
    )
    for warning in aspect_validation["warnings"]:
        add_warning(warning)
    for issue in aspect_validation["issues"]:
        if issue["detail"] not in blockers:
            blockers.append(issue["detail"])

    _add_category_specific_readiness_checks(
        item,
        add_check=add_check,
        add_warning=add_warning,
        add_required_action=add_required_action,
        category_template_provider=category_template_provider,
    )

    policy_state = _resolve_seller_policy_state(settings, seller_policy_provider=seller_policy_provider)
    add_check(
        "seller_policy_readiness",
        policy_state["ok"],
        detail=(
            "Seller policy IDs are configured or otherwise discoverable."
            if policy_state["ok"]
            else policy_state["detail"]
        ),
        blocking=policy_state["blocking"],
        warning=policy_state["warning"],
        action=policy_state["action"],
        context=policy_state["context"],
    )
    if policy_state["warning"] and policy_state["ok"]:
        add_warning(policy_state["warning"])
    if policy_state["action"] and policy_state["ok"] and policy_state["context"].get("needs_discovery"):
        add_required_action(policy_state["action"])

    ready = len(blockers) == 0
    return PublishReadinessResult(
        sku=sku,
        ready=ready,
        checks=checks,
        blockers=blockers,
        warnings=warnings,
        required_actions=required_actions,
    )


def not_found_publish_readiness(sku: str) -> PublishReadinessResult:
    normalized = (sku or "").strip().upper()
    detail = f"Item {normalized} not found."
    return PublishReadinessResult(
        sku=normalized,
        ready=False,
        checks=[
            {
                "name": "item_exists",
                "ok": False,
                "blocking": True,
                "detail": detail,
            }
        ],
        blockers=[detail],
        warnings=[],
        required_actions=["Create or import the item record before publishing."],
    )


def _add_category_specific_readiness_checks(
    item: Item,
    *,
    add_check: Callable[..., None],
    add_warning: Callable[[str], None],
    add_required_action: Callable[[str], None],
    category_template_provider: CategoryTemplateProvider | None,
) -> None:
    if not _has_value(item.ebay_category_id):
        return

    if category_template_provider is not None:
        template_result = category_template_provider(item)
        if template_result.ok:
            _add_template_validation_check(item, template_result.value, add_check=add_check)
            return

        warning = _category_template_warning(template_result)
        add_check(
            "category_template_validation",
            True,
            detail="Category template validation skipped because the taxonomy provider was unavailable.",
            context={
                "category_id": item.ebay_category_id,
                "error_code": str(template_result.error_code or ""),
                "message": str(template_result.error or ""),
            },
        )
        add_warning(warning)
        add_required_action("Retry category intelligence when taxonomy access is available.")
        return

    template = CategorySpreadsheet().load_template(str(item.ebay_category_id))
    if template is not None:
        _add_template_validation_check(item, template, add_check=add_check)
        return

    if item.category_template_fetched:
        missing_required = list(item.missing_required_fields or [])
        add_check(
            "category_template_validation",
            len(missing_required) == 0,
            detail=(
                "Stored category intelligence indicates required specifics are complete."
                if not missing_required
                else f"Stored category intelligence is missing required specifics: {', '.join(missing_required)}."
            ),
            blocking=bool(missing_required),
            action="Fill the missing required category specifics before publishing.",
            context={
                "category_id": item.ebay_category_id,
                "source": "stored_item",
                "missing_required": missing_required,
                "missing_recommended": list(item.missing_recommended_fields or []),
            },
        )
        return

    add_check(
        "category_template_validation",
        True,
        detail="No cached category template is available to validate category-specific specifics yet.",
        context={
            "category_id": item.ebay_category_id,
            "source": "none",
        },
    )
    add_warning("Category-specific validation has not been run locally for this item yet.")
    add_required_action("Run category intelligence or refresh the cached category template before publishing.")


def _add_template_validation_check(item: Item, template: CategoryTemplate, *, add_check: Callable[..., None]) -> None:
    validation = CategoryIntelligence().validate_item_specifics(item, template)
    detail = "Cached category template validation passed."
    if validation.missing_required:
        detail = f"Missing required category specifics: {', '.join(validation.missing_required)}."
    elif validation.invalid_fields:
        detail = f"Category specifics include unsupported values for: {', '.join(validation.invalid_fields)}."

    add_check(
        "category_template_validation",
        validation.is_publish_ready and not validation.invalid_fields,
        detail=detail,
        blocking=bool(validation.missing_required or validation.invalid_fields),
        action="Fill the missing or invalid category-specific item specifics before publishing.",
        context={
            "category_id": template.category_id,
            "category_name": template.category_name,
            "source": "cached_template",
            "missing_required": validation.missing_required,
            "missing_recommended": validation.missing_recommended,
            "invalid_fields": validation.invalid_fields,
        },
    )


def _resolve_seller_policy_state(
    settings,
    *,
    seller_policy_provider: SellerPolicyProvider | None,
) -> dict:
    policy_ids = {
        "fulfillment": str(settings.ebay_fulfillment_policy_id or "").strip(),
        "payment": str(settings.ebay_payment_policy_id or "").strip(),
        "return": str(settings.ebay_return_policy_id or "").strip(),
    }
    missing_keys = [key for key, value in policy_ids.items() if not value]
    discovery_result: dict[str, str] = {}
    discovery_error = ""

    if missing_keys and seller_policy_provider is not None:
        try:
            resolved = seller_policy_provider() or {}
            discovery_result = {
                "fulfillment": str(resolved.get("fulfillment_id") or "").strip(),
                "payment": str(resolved.get("payment_id") or "").strip(),
                "return": str(resolved.get("return_id") or "").strip(),
            }
        except Exception as exc:
            discovery_error = str(exc)

    merged_ids = {
        "fulfillment": policy_ids["fulfillment"] or discovery_result.get("fulfillment", ""),
        "payment": policy_ids["payment"] or discovery_result.get("payment", ""),
        "return": policy_ids["return"] or discovery_result.get("return", ""),
    }
    remaining_missing = [key for key, value in merged_ids.items() if not value]
    discovery_available = _has_local_policy_discovery_prereqs(settings) or seller_policy_provider is not None
    ok = len(remaining_missing) == 0 or discovery_available
    blocking = bool(remaining_missing) and not discovery_available

    detail = "Seller policy IDs are configured or otherwise discoverable."
    warning = None
    action = None
    if remaining_missing and discovery_available:
        detail = (
            "Seller policy IDs are not fully configured locally, but existing discovery fallback can still resolve them."
        )
        warning = f"Seller policy IDs still need discovery for: {', '.join(remaining_missing)}."
        action = "Verify seller policy discovery fallback before publishing."
    elif remaining_missing:
        detail = f"Seller policy IDs are missing for: {', '.join(remaining_missing)}."
        action = "Configure eBay seller policy IDs before publishing."

    if discovery_error:
        ok = False
        blocking = False
        detail = f"Seller policy discovery check failed: {discovery_error}"
        warning = detail
        action = "Retry seller policy discovery or configure the policy IDs directly."

    return {
        "ok": ok,
        "blocking": blocking,
        "detail": detail,
        "warning": warning,
        "action": action,
        "context": {
            "configured_policy_ids": policy_ids,
            "discovered_policy_ids": discovery_result,
            "missing_policy_keys": remaining_missing,
            "discovery_available": discovery_available,
            "needs_discovery": bool(remaining_missing) and discovery_available,
        },
    }


def _category_template_warning(result: Result[CategoryTemplate]) -> str:
    code = str(result.error_code or "CATEGORY_INTELLIGENCE_ERROR")
    message = str(result.error or "Category intelligence failed")
    return f"Category template validation skipped: {code} - {message}"


def _has_local_policy_discovery_prereqs(settings) -> bool:
    return bool(
        str(settings.ebay_app_id or "").strip()
        and str(settings.ebay_cert_id or "").strip()
        and str(settings.ebay_user_token or "").strip()
    )


def _normalize_condition_key(value: object) -> str:
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())[:4]
    if digits and digits in EBAY_CONDITION_MAP:
        return digits
    text = str(value).strip().upper()
    if text in EBAY_CONDITION_MAP:
        return text
    return ""


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
