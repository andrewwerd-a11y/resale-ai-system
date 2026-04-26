from __future__ import annotations

import os
from typing import Any

DEFAULT_APPROVED_E2E_SKUS = ("BK-000005", "BK-000008", "BK-000009")
SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "key",
    "app_id",
    "cert_id",
    "dev_id",
    "authorization",
    "bearer",
    "refresh",
)


class E2ESafetyError(RuntimeError):
    """Raised when an E2E action violates safety constraints."""


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_approved_e2e_skus() -> set[str]:
    raw = os.getenv("APPROVED_E2E_SKUS", "")
    if not raw.strip():
        return set(DEFAULT_APPROVED_E2E_SKUS)
    return {sku.strip().upper() for sku in raw.split(",") if sku.strip()}


def is_e2e_sku_allowed(sku: str) -> bool:
    return (sku or "").strip().upper() in get_approved_e2e_skus()


def assert_e2e_sku_allowed(sku: str) -> None:
    if not is_e2e_sku_allowed(sku):
        approved = ", ".join(sorted(get_approved_e2e_skus()))
        raise E2ESafetyError(
            f"Blocked mutation for SKU '{sku}'. Only approved E2E SKUs are allowed: {approved}."
        )


def is_live_e2e_enabled() -> bool:
    return _parse_bool(os.getenv("ALLOW_LIVE_E2E"))


def is_route_guard_enabled() -> bool:
    return _parse_bool(os.getenv("E2E_ROUTE_GUARD_ENABLED"))


def assert_live_e2e_allowed(sku: str) -> None:
    assert_e2e_sku_allowed(sku)
    if not is_live_e2e_enabled():
        raise E2ESafetyError(
            f"Live E2E mutation blocked for SKU '{sku}'. Set ALLOW_LIVE_E2E=true explicitly."
        )


def assert_mutation_allowed(sku: str, mode: str, action: str) -> None:
    normalized_mode = (mode or "").strip().lower()
    assert_e2e_sku_allowed(sku)
    if normalized_mode == "live-gated":
        assert_live_e2e_allowed(sku)
        return
    if normalized_mode == "sandbox":
        return
    if normalized_mode == "mock":
        return
    raise E2ESafetyError(
        f"Blocked '{action}' for SKU '{sku}'. Unknown E2E mode '{mode}'."
    )


def parse_sku_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def assert_route_sku_allowed(sku: str, action: str) -> None:
    if not is_route_guard_enabled():
        return
    assert_e2e_sku_allowed(sku)


def assert_route_skus_allowed(
    skus: list[str],
    action: str,
    *,
    require_non_empty: bool = False,
) -> list[str]:
    normalized = [s.strip().upper() for s in (skus or []) if s and s.strip()]
    if not is_route_guard_enabled():
        return normalized
    if require_non_empty and not normalized:
        raise E2ESafetyError(
            f"E2E route guard blocked '{action}'. Explicit SKU constraints are required."
        )
    for sku in normalized:
        assert_e2e_sku_allowed(sku)
    return normalized


def redact_secret(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    if text == "":
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"


def _should_redact_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in SENSITIVE_KEY_PARTS)


def redact_mapping(mapping: Any) -> Any:
    if isinstance(mapping, dict):
        out: dict[Any, Any] = {}
        for k, v in mapping.items():
            if _should_redact_key(str(k)):
                out[k] = redact_secret(v)
            else:
                out[k] = redact_mapping(v)
        return out
    if isinstance(mapping, list):
        return [redact_mapping(v) for v in mapping]
    if isinstance(mapping, tuple):
        return tuple(redact_mapping(v) for v in mapping)
    return mapping


def summarize_env_safely() -> dict[str, Any]:
    try:
        from packages.core.src.config import get_settings

        s = get_settings()
        env_summary = {
            "approved_e2e_skus": sorted(get_approved_e2e_skus()),
            "allow_live_e2e": is_live_e2e_enabled(),
            "ebay_environment": (s.ebay_environment or "sandbox").lower(),
            "ebay_marketplace_id": s.ebay_marketplace_id,
            "cloudinary_cloud_name_present": bool((s.cloudinary_cloud_name or "").strip()),
            "cloudinary_api_key_present": bool((s.cloudinary_api_key or "").strip()),
            "cloudinary_api_secret_present": bool((s.cloudinary_api_secret or "").strip()),
            "ebay_prod_app_id_present": bool((s.ebay_prod_app_id or "").strip()),
            "ebay_prod_cert_id_present": bool((s.ebay_prod_cert_id or "").strip()),
            "ebay_prod_dev_id_present": bool((s.ebay_prod_dev_id or "").strip()),
            "ebay_prod_user_token_present": bool((s.ebay_prod_user_token or "").strip()),
            "ebay_sandbox_app_id_present": bool((s.ebay_sandbox_app_id or "").strip()),
            "ebay_sandbox_cert_id_present": bool((s.ebay_sandbox_cert_id or "").strip()),
            "ebay_sandbox_dev_id_present": bool((s.ebay_sandbox_dev_id or "").strip()),
            "ebay_sandbox_user_token_present": bool((s.ebay_sandbox_user_token or "").strip()),
            "http_proxy_present": bool(os.getenv("HTTP_PROXY", "").strip()),
            "https_proxy_present": bool(os.getenv("HTTPS_PROXY", "").strip()),
            "no_proxy_present": bool(os.getenv("NO_PROXY", "").strip()),
        }
    except Exception:
        env_summary = {
            "approved_e2e_skus": sorted(get_approved_e2e_skus()),
            "allow_live_e2e": is_live_e2e_enabled(),
            "ebay_environment": os.getenv("EBAY_ENVIRONMENT", "").strip().lower() or "sandbox",
            "ebay_marketplace_id": os.getenv("EBAY_MARKETPLACE_ID", "").strip(),
            "http_proxy_present": bool(os.getenv("HTTP_PROXY", "").strip()),
            "https_proxy_present": bool(os.getenv("HTTPS_PROXY", "").strip()),
            "no_proxy_present": bool(os.getenv("NO_PROXY", "").strip()),
        }
    return redact_mapping(env_summary)
