from __future__ import annotations

import pytest

from packages.testing.src.e2e_guard import (
    E2ESafetyError,
    assert_e2e_sku_allowed,
    assert_live_e2e_allowed,
    assert_mutation_allowed,
    assert_route_sku_allowed,
    assert_route_skus_allowed,
    get_approved_e2e_skus,
    is_live_e2e_enabled,
    is_route_guard_enabled,
    parse_sku_list,
    redact_mapping,
    redact_secret,
    summarize_env_safely,
)


def test_approved_sku_passes():
    assert_e2e_sku_allowed("BK-000005")


def test_unapproved_sku_fails():
    with pytest.raises(E2ESafetyError):
        assert_e2e_sku_allowed("BK-999999")


def test_approved_sku_list_loads_from_env(monkeypatch):
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-111111, BK-222222")
    assert get_approved_e2e_skus() == {"BK-111111", "BK-222222"}


def test_live_mutation_blocked_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_LIVE_E2E", raising=False)
    assert is_live_e2e_enabled() is False
    with pytest.raises(E2ESafetyError):
        assert_live_e2e_allowed("BK-000005")


def test_live_mutation_allowed_only_when_flag_true(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    assert_live_e2e_allowed("BK-000005")


def test_sandbox_mutation_allowed_only_for_approved_sku():
    assert_mutation_allowed("BK-000005", "sandbox", "test_action")
    with pytest.raises(E2ESafetyError):
        assert_mutation_allowed("BK-123123", "sandbox", "test_action")


def test_mock_mode_still_blocks_unapproved_sku():
    with pytest.raises(E2ESafetyError):
        assert_mutation_allowed("NOPE-001", "mock", "test_action")


def test_redaction_for_obvious_secret_values():
    assert redact_secret(None) is None
    assert redact_secret("") == ""
    assert redact_secret("short") == "***"
    assert redact_secret("abcdefghijklmnopqrstuvwxyz").startswith("abcd...")


def test_redact_mapping_recursive_sensitive_keys():
    payload = {
        "Authorization": "Bearer abcdefghijklmnop",
        "nested": {
            "api_key": "super-secret-key",
            "ok": "value",
        },
        "items": [{"refresh_token": "refresh_123456789"}],
    }
    redacted = redact_mapping(payload)
    assert redacted["Authorization"] != payload["Authorization"]
    assert redacted["nested"]["api_key"] != payload["nested"]["api_key"]
    assert redacted["nested"]["ok"] == "value"
    assert redacted["items"][0]["refresh_token"] != payload["items"][0]["refresh_token"]


def test_summarize_env_safely_does_not_leak_values(monkeypatch):
    monkeypatch.setenv("EBAY_PROD_USER_TOKEN", "very-secret-token-value")
    summary = summarize_env_safely()
    text = str(summary)
    assert "very-secret-token-value" not in text
    assert "ebay_prod_user_token_present" in summary


def test_route_guard_allows_approved_sku_when_enabled(monkeypatch):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    assert is_route_guard_enabled() is True
    assert_route_sku_allowed("BK-000005", "route_action")


def test_route_guard_blocks_unapproved_sku_when_enabled(monkeypatch):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    with pytest.raises(E2ESafetyError):
        assert_route_sku_allowed("BK-999999", "route_action")


def test_route_guard_requires_nonempty_skus_for_global_route(monkeypatch):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    with pytest.raises(E2ESafetyError):
        assert_route_skus_allowed([], "global_action", require_non_empty=True)


def test_parse_sku_list_normalizes_values():
    assert parse_sku_list("bk-000005, BK-000008, ,bk-000009") == [
        "BK-000005",
        "BK-000008",
        "BK-000009",
    ]

