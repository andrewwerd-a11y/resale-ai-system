from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apps.api.src.services.ebay_auth_diagnostics import classify_ebay_auth_failure, get_ebay_auth_readiness
from packages.core.src import config as core_config
from packages.ebay.src.auth import EbayAuth


def test_auth_readiness_missing_token_produces_structured_failure(monkeypatch):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    core_config.get_settings.cache_clear()
    monkeypatch.setattr("apps.api.src.services.ebay_auth_diagnostics._load_tokens", lambda: {})
    monkeypatch.setattr("packages.ebay.src.auth._load_tokens", lambda: {})

    readiness = get_ebay_auth_readiness()

    assert readiness["code"] == "missing_token"
    assert readiness["category"] == "auth"
    assert readiness["checks"]["access_token_present"] is False
    assert "token" in readiness["message"].lower()


def test_invalid_token_style_response_maps_to_expired_or_invalid_access_token():
    detail = classify_ebay_auth_failure(
        status_code=401,
        text="Error 1001: Invalid access token. Check the value of the Authorization HTTP request header.",
        auth_readiness={"checks": {"sandbox_production_mismatch_detected": False}},
    )

    assert detail["code"] == "expired_or_invalid_access_token"
    assert "token" in detail["message"].lower()


def test_refresh_failure_maps_without_leaking_secrets(monkeypatch):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    monkeypatch.delenv("DRY_RUN", raising=False)
    core_config.get_settings.cache_clear()

    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        "packages.ebay.src.auth._load_tokens",
        lambda: {
            "access_token": "expired-access-token",
            "refresh_token": "super-secret-refresh-token",
            "expires_at": expired,
        },
    )
    monkeypatch.setattr(EbayAuth, "_refresh_access_token", lambda _self, _refresh_token: None)

    auth = EbayAuth()
    token_state = auth.resolve_user_token()

    assert token_state["token"] == ""
    assert token_state["issue_code"] == "refresh_failed"
    assert "secret" not in (token_state["issue_message"] or "").lower()
    assert "refresh-token" not in (token_state["issue_message"] or "").lower()


def test_sandbox_production_mismatch_is_surfaced(monkeypatch):
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    monkeypatch.setenv("EBAY_PROD_APP_ID", "prod-app")
    monkeypatch.setenv("EBAY_PROD_CERT_ID", "prod-cert")
    monkeypatch.setenv("EBAY_PROD_USER_TOKEN", "prod-token")
    core_config.get_settings.cache_clear()
    monkeypatch.setattr("apps.api.src.services.ebay_auth_diagnostics._load_tokens", lambda: {})
    monkeypatch.setattr("packages.ebay.src.auth._load_tokens", lambda: {})

    readiness = get_ebay_auth_readiness()

    assert readiness["checks"]["sandbox_production_mismatch_detected"] is True
    assert readiness["code"] == "missing_token" or readiness["code"] == "sandbox_production_mismatch"
