from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.core.src import config as core_config
from packages.ebay.src.auth import EbayAuth
from packages.ebay.src.condition_mapping import inventory_enum_to_condition_id, validate_condition_id_enum_pair
from packages.domain.src.entities.item import Item
from packages.ebay.src.inventory_client import EbayInventoryClient


def _build_item() -> Item:
    return Item(
        sku="BK-000005",
        title_final="Test Title",
        description_final="Test Description",
        list_price=22.0,
        category_key="books",
        ebay_category_id="29223",
    )


def test_build_offer_payload_uses_explicit_location_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = EbayInventoryClient()
    item = _build_item()

    def _unexpected_lookup() -> str:
        raise AssertionError("location lookup should not run when explicit key is passed")

    monkeypatch.setattr(client, "get_merchant_location_key", _unexpected_lookup)

    payload = client._build_offer_payload(
        item,
        {"fulfillment_id": "f1", "payment_id": "p1", "return_id": "r1"},
        merchant_location_key="default",
    )

    assert payload["merchantLocationKey"] == "default"


def test_build_offer_payload_falls_back_to_location_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    client = EbayInventoryClient()
    item = _build_item()

    monkeypatch.setattr(client, "get_merchant_location_key", lambda: "warehouse-1")

    payload = client._build_offer_payload(
        item,
        {"fulfillment_id": "f1", "payment_id": "p1", "return_id": "r1"},
    )

    assert payload["merchantLocationKey"] == "warehouse-1"


@pytest.mark.parametrize(
    ("condition_id", "expected_enum"),
    [
        ("3000", "USED_EXCELLENT"),
        ("4000", "USED_VERY_GOOD"),
        ("5000", "USED_GOOD"),
    ],
)
def test_condition_id_maps_to_expected_inventory_enum(condition_id: str, expected_enum: str) -> None:
    client = EbayInventoryClient()
    item = _build_item()
    item.condition_id = condition_id

    assert client._resolve_inventory_condition(item) == expected_enum


def test_condition_id_3000_is_used_excellent_and_not_used_good_fallback() -> None:
    from apps.api.src.services.publish_repair import CONDITION_LABEL_FALLBACKS

    assert "3000" in CONDITION_LABEL_FALLBACKS["USED_EXCELLENT"]
    assert "3000" not in CONDITION_LABEL_FALLBACKS["LIKE_NEW"]
    assert "3000" not in CONDITION_LABEL_FALLBACKS["USED_GOOD"]
    assert "5000" in CONDITION_LABEL_FALLBACKS["USED_GOOD"]
    assert "4000" in CONDITION_LABEL_FALLBACKS["USED_VERY_GOOD"]


def test_inventory_enum_maps_back_to_expected_condition_id() -> None:
    assert inventory_enum_to_condition_id("USED_EXCELLENT") == "3000"
    assert inventory_enum_to_condition_id("USED_VERY_GOOD") == "4000"
    assert inventory_enum_to_condition_id("USED_GOOD") == "5000"


def test_condition_id_enum_pair_validation_accepts_used_excellent_and_rejects_used_good() -> None:
    assert validate_condition_id_enum_pair("3000", "USED_EXCELLENT") is True
    assert validate_condition_id_enum_pair("3000", "USED_GOOD") is False


def test_live_readonly_methods_use_get_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client = EbayInventoryClient.__new__(EbayInventoryClient)

    class _Auth:
        api_base = "https://api.sandbox.ebay.com"
        marketplace_id = "EBAY_US"
        resolve_calls: list[bool] = []

        def resolve_user_token(self, *, allow_refresh: bool = True) -> dict:
            self.resolve_calls.append(allow_refresh)
            return {"token": "token", "source": "oauth", "issue_code": None, "issue_message": None}

    class _Resp:
        status_code = 200
        text = ""
        content = b"{}"

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    client.auth = _Auth()
    calls: list[tuple[str, dict]] = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/inventory_item/BK-000008"):
            return _Resp({"sku": "BK-000008", "condition": "USED_EXCELLENT"})
        if url.endswith("/offer/156719395011"):
            return _Resp({"offerId": "156719395011", "categoryId": "14056"})
        if "get_item_condition_policies" in url:
            return _Resp({"itemConditionPolicies": [{"itemConditions": [{"conditionId": "3000"}]}]})
        raise AssertionError(f"unexpected GET URL: {url}")

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("read-only methods must not call mutation HTTP verbs")

    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", fake_get)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.put", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.post", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.delete", fail_mutation)

    inventory = client.get_inventory_item("BK-000008")
    offer = client.get_offer("156719395011")
    policy = client.get_item_condition_policies("14056")

    assert inventory.ok
    assert inventory.value["condition"] == "USED_EXCELLENT"
    assert offer.ok
    assert offer.value["offerId"] == "156719395011"
    assert policy.ok
    assert policy.value["itemConditionPolicies"][0]["itemConditions"][0]["conditionId"] == "3000"
    assert len(calls) == 3
    assert calls[2][1]["params"] == {"filter": "categoryIds:{14056}"}
    assert client.auth.resolve_calls == [False, False, False]


def test_live_readonly_auth_uses_env_fallback_without_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "env-token-value")
    core_config.get_settings.cache_clear()

    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        "packages.ebay.src.auth._load_tokens",
        lambda: {
            "access_token": "expired-oauth-token",
            "refresh_token": "refresh-token",
            "expires_at": expired,
        },
    )

    def fail_refresh(_self, _refresh_token):  # pragma: no cover
        raise AssertionError("read-only diagnostics must not refresh OAuth tokens")

    monkeypatch.setattr(EbayAuth, "_refresh_access_token", fail_refresh)

    client = EbayInventoryClient()
    diagnostics = client.get_readonly_auth_diagnostics()
    headers = client._readonly_headers()

    assert diagnostics["auth_readonly_available"] is True
    assert diagnostics["token_source_used"] == "env_fallback"
    assert diagnostics["refresh_allowed"] is False
    assert diagnostics["no_token_refresh_performed"] is True
    assert headers.ok
    assert headers.details["token_source_used"] == "env_fallback"
    assert headers.value["Authorization"] == "Bearer env-token-value"


def test_live_readonly_auth_expired_oauth_without_env_fails_without_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "")
    core_config.get_settings.cache_clear()

    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        "packages.ebay.src.auth._load_tokens",
        lambda: {
            "access_token": "expired-oauth-token",
            "refresh_token": "refresh-token",
            "expires_at": expired,
        },
    )

    def fail_refresh(_self, _refresh_token):  # pragma: no cover
        raise AssertionError("read-only diagnostics must not refresh OAuth tokens")

    def fail_get(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("read-only diagnostics must not call eBay without usable auth")

    monkeypatch.setattr(EbayAuth, "_refresh_access_token", fail_refresh)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", fail_get)

    client = EbayInventoryClient()
    diagnostics = client.get_readonly_auth_diagnostics()
    result = client.get_inventory_item("BK-000008")

    assert diagnostics["auth_readonly_available"] is False
    assert diagnostics["reason"] == "oauth_access_token_expired_refresh_not_allowed"
    assert diagnostics["token_source_used"] == "none"
    assert diagnostics["refresh_allowed"] is False
    assert result.ok is False
    assert result.error_code == "AUTH_NOT_READY"
    assert result.details["auth_issue_code"] == "oauth_access_token_expired_refresh_not_allowed"
