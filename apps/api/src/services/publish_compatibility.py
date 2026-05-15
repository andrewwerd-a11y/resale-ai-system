from __future__ import annotations

import json
from pathlib import Path

from packages.domain.src.entities.item import Item
from packages.ebay.src.aspect_validation import validate_aspects
from packages.ebay.src.category_spreadsheet import CategorySpreadsheet
from packages.ebay.src.condition_mapping import CONDITION_ID_TO_ENUM
from packages.ebay.src.public_image_urls import (
    extract_public_image_urls,
    is_valid_public_image_url,
    looks_like_public_image_url_candidate,
    normalize_public_image_url,
)

INVALID_CONDITION_ID_FORMAT_ACTION = "Normalize condition_id to a valid eBay numeric condition ID before publishing."

_DEFAULT_CATEGORY_CONDITION_POLICIES: dict[str, dict] = {
    "29223": {"allowed_condition_ids": ["3000", "4000", "5000", "6000"], "source": "builtin"},
    "53159": {"allowed_condition_ids": ["3000", "4000", "5000", "6000"], "source": "builtin"},
    "57990": {"allowed_condition_ids": ["3000", "4000", "5000", "6000"], "source": "builtin"},
    "93427": {"allowed_condition_ids": ["3000", "4000", "5000", "6000"], "source": "builtin"},
    "95672": {"allowed_condition_ids": ["3000", "4000", "5000", "6000"], "source": "builtin"},
    "40143": {"allowed_condition_ids": ["3000", "4000", "5000", "6000"], "source": "builtin"},
    "48084": {"allowed_condition_ids": ["1000", "1500", "3000", "4000"], "source": "builtin"},
    "14056": {"allowed_condition_ids": ["1000", "1500", "3000", "4000"], "source": "builtin"},
}


def get_category_condition_policy(category_id: str) -> dict:
    normalized_category_id = str(category_id or "").strip()
    if not normalized_category_id:
        return {
            "category_id": "",
            "known": False,
            "allowed_condition_ids": [],
            "source": "missing_category",
        }

    spreadsheet = CategorySpreadsheet()
    candidate = spreadsheet._dir / f"{normalized_category_id}_condition_policy.json"
    if candidate.exists():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            allowed = [str(v).strip() for v in payload.get("allowed_condition_ids", []) if str(v).strip()]
            return {
                "category_id": normalized_category_id,
                "known": bool(allowed),
                "allowed_condition_ids": allowed,
                "source": str(payload.get("source") or "cached_policy"),
            }
        except Exception:
            pass

    default = _DEFAULT_CATEGORY_CONDITION_POLICIES.get(normalized_category_id)
    if default:
        return {
            "category_id": normalized_category_id,
            "known": True,
            "allowed_condition_ids": list(default.get("allowed_condition_ids", [])),
            "source": str(default.get("source") or "builtin"),
        }

    return {
        "category_id": normalized_category_id,
        "known": False,
        "allowed_condition_ids": [],
        "source": "unknown",
    }


def evaluate_publish_compatibility(item: Item, *, strict_condition_policy: bool = False) -> dict:
    checks: list[dict] = []
    blockers: list[str] = []
    warnings: list[str] = []
    required_actions: list[str] = []

    def add_check(name: str, ok: bool, detail: str, *, blocking: bool = False, warning: str | None = None, action: str | None = None, context: dict | None = None) -> None:
        checks.append(
            {
                "name": name,
                "ok": ok,
                "detail": detail,
                "blocking": blocking,
                "warning": warning,
                "action": action,
                "context": context or {},
            }
        )
        if blocking and not ok:
            blockers.append(detail)
        if warning:
            warnings.append(warning)
        if action and (blocking and not ok or warning):
            required_actions.append(action)

    raw_image_values = [str(path).strip() for path in (item.image_paths or []) if str(path).strip()]
    hosted_urls = extract_public_image_urls(raw_image_values)
    malformed_public_candidates = []
    for value in raw_image_values:
        if looks_like_public_image_url_candidate(value):
            normalized = normalize_public_image_url(value)
            if not is_valid_public_image_url(normalized):
                malformed_public_candidates.append(value)
    local_image_paths = [value for value in raw_image_values if not looks_like_public_image_url_candidate(value)]
    windows_paths = [value for value in local_image_paths if ":" in value[:3] or "\\" in value]

    has_hosted_urls = bool(hosted_urls)
    has_malformed_hosted_urls = bool(malformed_public_candidates)
    has_local_image_paths = bool(local_image_paths)
    image_urls_ok = has_hosted_urls and not has_malformed_hosted_urls
    image_blocking = not image_urls_ok
    image_warning = None
    image_action = None

    if has_hosted_urls and not has_malformed_hosted_urls:
        image_detail = f"{len(hosted_urls)} hosted public image URL(s) are valid."
        if windows_paths:
            image_warning = "Local Windows photo paths are stored alongside hosted URLs; only hosted public URLs will be sent to eBay."
    elif has_malformed_hosted_urls:
        image_detail = "Hosted public image URLs are malformed for eBay publish."
        image_warning = "Some hosted image URL candidates are malformed and need repair."
        image_action = "Repair malformed hosted image URLs before retrying publish."
    else:
        image_detail = "Hosted public image URLs are missing for eBay publish."
        image_action = (
            "Host local photos before publish."
            if has_local_image_paths
            else "Attach or host public image URLs before retrying publish."
        )
    add_check(
        "public_image_urls",
        image_urls_ok,
        image_detail,
        blocking=image_blocking,
        warning=image_warning,
        action=image_action,
        context={
            "hosted_photo_urls": hosted_urls,
            "malformed_public_candidates": malformed_public_candidates,
            "local_windows_paths": windows_paths,
        },
    )

    policy = get_category_condition_policy(str(item.ebay_category_id or ""))
    condition_id = str(item.condition_id or "").strip()
    condition_id_format_valid = _is_valid_condition_id_format(condition_id)
    condition_context = policy | {
        "condition_id": condition_id,
        "condition_id_valid": condition_id_format_valid,
    }
    if not condition_id_format_valid:
        add_check(
            "condition_id_format",
            False,
            (
                "Condition ID is missing for the selected category."
                if not condition_id
                else f"Condition ID '{condition_id}' is not a clean numeric eBay condition ID."
            ),
            blocking=True,
            action=INVALID_CONDITION_ID_FORMAT_ACTION,
            context=condition_context,
        )
    elif not policy["known"]:
        add_check(
            "condition_id_format",
            True,
            f"Condition ID '{condition_id}' is a clean numeric eBay condition ID.",
            context=condition_context,
        )
        add_check(
            "category_condition_policy",
            not strict_condition_policy,
            "Condition policy for the selected category is not cached locally.",
            blocking=strict_condition_policy,
            warning=(
                "Condition compatibility for the exact category is unknown locally."
                if not strict_condition_policy
                else None
            ),
            action="Fetch or confirm category-specific condition compatibility before retrying publish.",
            context=condition_context,
        )
    else:
        add_check(
            "condition_id_format",
            True,
            f"Condition ID '{condition_id}' is a clean numeric eBay condition ID.",
            context=condition_context,
        )
        allowed_condition_ids = [str(v) for v in policy["allowed_condition_ids"]]
        condition_ok = condition_id in allowed_condition_ids
        add_check(
            "category_condition_policy",
            condition_ok,
            (
                f"Condition ID '{condition_id}' is allowed for category '{policy['category_id']}'."
                if condition_ok
                else f"Condition ID '{condition_id}' is not allowed for category '{policy['category_id']}'."
            ),
            blocking=True,
            action="Choose an allowed category-specific condition ID before retrying publish.",
            context=condition_context,
        )

    template = None
    if str(item.ebay_category_id or "").strip():
        template = CategorySpreadsheet().load_template(str(item.ebay_category_id or "").strip())

    collected_item_specifics = _collect_local_item_specifics(item, template)
    aspect_validation = validate_aspects(collected_item_specifics)
    add_check(
        "aspect_value_constraints",
        aspect_validation["ok"],
        (
            "Aspect values satisfy local eBay length and normalization constraints."
            if aspect_validation["ok"]
            else "Aspect values still violate local eBay constraints."
        ),
        blocking=not aspect_validation["ok"],
        action="Repair invalid or overlong item specifics before retrying publish.",
        context={
            "normalized_aspects": aspect_validation["normalized_aspects"],
            "issues": aspect_validation["issues"],
        },
    )

    missing_required = []
    missing_recommended = []
    if template is not None:
        flattened = _flatten_values(collected_item_specifics)
        for field_name in template.required_fields:
            if not flattened.get(field_name):
                missing_required.append(field_name)
        for field_name in template.recommended_fields:
            if not flattened.get(field_name):
                missing_recommended.append(field_name)
    else:
        missing_required = list(item.missing_required_fields or [])
        missing_recommended = list(item.missing_recommended_fields or [])

    add_check(
        "category_template_requirements",
        not missing_required,
        (
            "Required category aspects are present."
            if not missing_required
            else f"Missing required category aspects: {', '.join(missing_required)}."
        ),
        blocking=bool(missing_required),
        warning=(
            f"Missing recommended category aspects: {', '.join(missing_recommended)}."
            if missing_recommended
            else None
        ),
        action="Populate missing category-specific required aspects before retrying publish.",
        context={
            "missing_required": missing_required,
            "missing_recommended": missing_recommended,
            "category_template_cached": template is not None,
        },
    )

    price_ok = bool(item.list_price and float(item.list_price or 0) > 0)
    add_check(
        "offer_basics",
        price_ok,
        "Offer basics are present for publish." if price_ok else "Offer basics are incomplete for publish.",
        blocking=not price_ok,
        action="Set a valid listing price before retrying publish.",
        context={"list_price": item.list_price, "quantity": 1, "format": "FIXED_PRICE"},
    )

    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "required_actions": list(dict.fromkeys(required_actions)),
        "checks": checks,
        "strict_condition_policy": strict_condition_policy,
    }


def _is_valid_condition_id_format(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and text.isdigit() and text in CONDITION_ID_TO_ENUM)


def _flatten_values(values: dict[str, list[str]]) -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key, raw_values in values.items():
        if not raw_values:
            continue
        first = str(raw_values[0] or "").strip()
        if first:
            flattened[key] = first
    return flattened


def _collect_local_item_specifics(item: Item, template=None) -> dict[str, list[str]]:
    specifics: dict[str, list[str]] = {}
    stored: dict = {}
    if isinstance(item.item_specifics, dict):
        stored = item.item_specifics
    elif isinstance(item.item_specifics, str):
        try:
            stored = json.loads(item.item_specifics)
        except Exception:
            stored = {}

    field_map = {
        "Brand": item.brand,
        "Type": item.type,
        "Color": item.color,
        "Size": item.size,
        "Material": item.material,
        "Style": item.style,
        "Pattern": item.pattern,
        "Department": item.department,
    }
    for ebay_field, value in field_map.items():
        if value:
            specifics[ebay_field] = [str(value)]

    for key, value in stored.items():
        if not value:
            continue
        specifics[str(key)] = [str(value)] if not isinstance(value, list) else [str(entry) for entry in value]

    if template is not None:
        required_fields = getattr(template, "required_fields", [])
        field_constraints = getattr(template, "field_constraints", {})
        for field_name in required_fields:
            if field_name not in specifics:
                defaults = field_constraints.get(field_name, [])
                if defaults:
                    specifics[field_name] = [defaults[0]]

    return specifics
