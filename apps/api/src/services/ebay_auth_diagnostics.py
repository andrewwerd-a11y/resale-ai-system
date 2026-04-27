from __future__ import annotations

from datetime import datetime, timezone

from packages.core.src.config import get_settings
from packages.ebay.src.auth import EbayAuth, _load_tokens, _parse_dt
from packages.testing.src.e2e_guard import is_live_e2e_enabled


def get_ebay_auth_readiness() -> dict:
    settings = get_settings()
    auth = EbayAuth()
    token_state = auth.resolve_user_token(allow_refresh=False)
    tokens = _load_tokens() or {}

    env_mode = (settings.ebay_environment or "sandbox").strip().lower() or "sandbox"
    selected = "sandbox" if env_mode != "production" else "production"
    other = "production" if selected == "sandbox" else "sandbox"

    selected_fields = _env_key_presence(settings, selected)
    other_fields = _env_key_presence(settings, other)
    oauth_state = _oauth_token_state(tokens)
    mismatch_detected = (
        not selected_fields["app_id_present"]
        and not selected_fields["cert_id_present"]
        and not selected_fields["user_token_present"]
        and (other_fields["app_id_present"] or other_fields["cert_id_present"] or other_fields["user_token_present"])
    )

    checks = {
        "environment": selected,
        "marketplace_id": settings.ebay_marketplace_id,
        "access_token_present": bool(token_state["token"]),
        "token_source": token_state["source"],
        "refresh_token_present": oauth_state["refresh_token_present"],
        "client_id_present": selected_fields["app_id_present"],
        "client_secret_present": selected_fields["cert_id_present"],
        "dev_id_present": selected_fields["dev_id_present"],
        "user_token_present": selected_fields["user_token_present"],
        "oauth_token_file_present": oauth_state["oauth_token_file_present"],
        "oauth_access_token_present": oauth_state["oauth_access_token_present"],
        "oauth_access_token_expired": oauth_state["oauth_access_token_expired"],
        "oauth_refresh_token_expired": oauth_state["oauth_refresh_token_expired"],
        "token_refresh_available": bool(
            oauth_state["refresh_token_present"]
            and selected_fields["app_id_present"]
            and selected_fields["cert_id_present"]
        ),
        "sandbox_production_mismatch_detected": mismatch_detected,
        "mutation_allowed": False,
        "allow_live_e2e": is_live_e2e_enabled(),
    }

    blockers: list[str] = []
    warnings: list[str] = []
    next_actions: list[str] = []
    code = "ready"
    message = "eBay auth configuration looks locally complete."

    if not checks["access_token_present"]:
        code = token_state["issue_code"] or "missing_token"
        message = token_state["issue_message"] or "No usable eBay access token is configured."
        blockers.append(message)
        next_actions.append("Provide a valid user token or reconnect eBay OAuth before retrying.")
    elif token_state["issue_code"] == "refresh_failed":
        code = "refresh_failed"
        message = token_state["issue_message"] or "OAuth token refresh failed."
        warnings.append(message)
        next_actions.append("Reconnect eBay OAuth or replace the environment token before retrying.")

    if not checks["client_id_present"] or not checks["client_secret_present"]:
        blockers.append("Selected eBay environment is missing client credentials.")
        next_actions.append("Set the client ID and cert ID for the active eBay environment.")
        if code == "ready":
            code = "missing_token"
            message = "Selected eBay environment is missing client credentials."

    if mismatch_detected:
        warnings.append(
            "Active eBay environment appears to be using the opposite environment's credential set."
        )
        next_actions.append("Align EBAY_ENVIRONMENT with the matching sandbox or production credential set.")
        if code == "ready":
            code = "sandbox_production_mismatch"
            message = "Credential presence suggests a sandbox/production mismatch."

    warnings.append("Mutation readiness is diagnostic only; real eBay mutation remains operator-controlled.")
    next_actions.append("Use the auth readiness result plus a manual eBay token check before live revise or publish attempts.")

    return {
        "code": code,
        "category": "auth",
        "message": message,
        "next_action": next_actions[0] if next_actions else "Review eBay auth configuration.",
        "checks": checks,
        "blockers": _dedupe(blockers),
        "warnings": _dedupe(warnings),
        "next_actions": _dedupe(next_actions),
    }


def classify_ebay_auth_failure(
    *,
    status_code: int,
    text: str,
    auth_readiness: dict | None = None,
    token_issue_code: str | None = None,
) -> dict:
    lower = (text or "").lower()
    readiness = auth_readiness or get_ebay_auth_readiness()

    if token_issue_code == "refresh_failed":
        return _failure_detail(
            "refresh_failed",
            "eBay token refresh failed before the request could be authenticated.",
            "Reconnect eBay OAuth or replace the expired token, then retry.",
        )
    if readiness.get("checks", {}).get("sandbox_production_mismatch_detected"):
        return _failure_detail(
            "sandbox_production_mismatch",
            "The active eBay environment appears to be mismatched with the configured credential set.",
            "Use sandbox credentials with sandbox mode or production credentials with production mode.",
        )
    if (
        "invalid access token" in lower
        or "error 1001" in lower
        or ("authorization http request header" in lower and "invalid" in lower)
        or ("access token" in lower and "invalid" in lower)
    ):
        return _failure_detail(
            "expired_or_invalid_access_token",
            "eBay rejected the access token for this request.",
            "Reconnect eBay OAuth or replace the active token for the selected environment, then retry.",
        )
    if "scope" in lower or "insufficient permissions" in lower or "insufficient scope" in lower:
        return _failure_detail(
            "insufficient_scope",
            "The current eBay token does not have the required scopes for this operation.",
            "Reconnect eBay OAuth with inventory and account scopes, then retry.",
        )
    if status_code in (401, 403):
        return _failure_detail(
            "unknown_auth_error",
            "eBay rejected the request as an authentication or authorization failure.",
            "Review token freshness, environment selection, and scopes before retrying.",
        )
    return _failure_detail(
        "unknown_auth_error",
        "The request failed with an eBay auth-related error that could not be classified more specifically.",
        "Review eBay auth readiness and the active environment before retrying.",
    )


def _failure_detail(code: str, message: str, next_action: str) -> dict:
    return {
        "code": code,
        "category": "auth",
        "message": message,
        "next_action": next_action,
    }


def _oauth_token_state(tokens: dict) -> dict[str, bool]:
    now = datetime.now(timezone.utc)
    access_expired = False
    refresh_expired = False
    if tokens.get("expires_at"):
        try:
            access_expired = now >= _parse_dt(tokens["expires_at"])
        except Exception:
            access_expired = False
    if tokens.get("refresh_expires_at"):
        try:
            refresh_expired = now >= _parse_dt(tokens["refresh_expires_at"])
        except Exception:
            refresh_expired = False
    return {
        "oauth_token_file_present": bool(tokens),
        "oauth_access_token_present": bool(tokens.get("access_token")),
        "refresh_token_present": bool(tokens.get("refresh_token")),
        "oauth_access_token_expired": access_expired,
        "oauth_refresh_token_expired": refresh_expired,
    }


def _env_key_presence(settings, mode: str) -> dict[str, bool]:
    prefix = "prod" if mode == "production" else "sandbox"
    return {
        "app_id_present": bool(getattr(settings, f"ebay_{prefix}_app_id", "").strip()),
        "cert_id_present": bool(getattr(settings, f"ebay_{prefix}_cert_id", "").strip()),
        "dev_id_present": bool(getattr(settings, f"ebay_{prefix}_dev_id", "").strip()),
        "user_token_present": bool(getattr(settings, f"ebay_{prefix}_user_token", "").strip()),
    }


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out
