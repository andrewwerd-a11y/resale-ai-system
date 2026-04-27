from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.core.src import config as core_config
from packages.ebay.src.auth import EbayAuth


def test_get_user_token_dry_run_uses_env_token_without_refresh(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "env-token-value")
    core_config.get_settings.cache_clear()

    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        "packages.ebay.src.auth._load_tokens",
        lambda: {
            "access_token": "expired-access-token",
            "refresh_token": "refresh-token",
            "expires_at": expired,
        },
    )

    def fail_refresh(_self, _refresh_token):  # pragma: no cover
        raise AssertionError("Dry-run mode should not attempt token refresh")

    monkeypatch.setattr(EbayAuth, "_refresh_access_token", fail_refresh)

    auth = EbayAuth()
    token = auth.get_user_token()
    assert token == "env-token-value"
