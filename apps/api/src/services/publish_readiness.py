from __future__ import annotations

from dataclasses import dataclass

from packages.core.src.config import get_settings
from packages.core.src.constants import ItemStatus
from packages.domain.src.entities.item import Item
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


def evaluate_publish_readiness(item: Item) -> PublishReadinessResult:
    sku = (item.sku or "").strip().upper()
    blockers: list[str] = []
    warnings: list[str] = []
    required_actions: list[str] = []
    checks: list[dict] = []
    settings = get_settings()

    def add_check(name: str, ok: bool, *, detail: str, blocking: bool = False, action: str | None = None, warning: str | None = None) -> None:
        checks.append(
            {
                "name": name,
                "ok": ok,
                "blocking": blocking,
                "detail": detail,
            }
        )
        if blocking and not ok:
            blockers.append(detail)
        if warning and not ok:
            warnings.append(warning)
        if action and not ok and action not in required_actions:
            required_actions.append(action)

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
    has_photos = bool(image_paths)
    hosted_photo_urls = [path for path in image_paths if path.startswith("http://") or path.startswith("https://")]
    add_check(
        "photos_present",
        has_photos,
        detail=(
            f"{len(image_paths)} photo path(s) available."
            if has_photos
            else "No photos are attached to this item."
        ),
        blocking=True,
        action="Attach local photos or hosted photo URLs before publishing.",
    )
    add_check(
        "hosted_photos_ready",
        bool(hosted_photo_urls),
        detail=(
            f"{len(hosted_photo_urls)} hosted photo URL(s) already present."
            if hosted_photo_urls
            else "Hosted photo URLs are not present yet."
        ),
        warning="Hosted photo URLs still need to be prepared before publish.",
        action="Host item photos before sandbox or live publish if local-only paths remain.",
    )

    policy_ids = {
        "fulfillment": str(settings.ebay_fulfillment_policy_id or "").strip(),
        "payment": str(settings.ebay_payment_policy_id or "").strip(),
        "return": str(settings.ebay_return_policy_id or "").strip(),
    }
    policies_configured = all(policy_ids.values())
    add_check(
        "seller_policies_configured",
        policies_configured,
        detail=(
            "Seller policy IDs are configured in settings."
            if policies_configured
            else "One or more seller policy IDs are not configured locally."
        ),
        warning="Seller policy discovery may still be needed before publish.",
        action="Configure seller policy IDs or verify discovery fallback before publishing.",
    )

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


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
