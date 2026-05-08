from __future__ import annotations

import pytest

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


def test_condition_id_3000_resolves_to_used_good() -> None:
    client = EbayInventoryClient()
    item = _build_item()
    item.condition_id = "3000"

    assert client._resolve_inventory_condition(item) == "USED_GOOD"


def test_condition_id_3000_is_not_excellent_or_like_new_fallback() -> None:
    from apps.api.src.services.publish_repair import CONDITION_LABEL_FALLBACKS

    assert "3000" not in CONDITION_LABEL_FALLBACKS["USED_EXCELLENT"]
    assert "3000" not in CONDITION_LABEL_FALLBACKS["LIKE_NEW"]
    assert "3000" in CONDITION_LABEL_FALLBACKS["USED_GOOD"]


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
            return _Resp({"sku": "BK-000008", "condition": "USED_GOOD"})
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
    assert inventory.value["condition"] == "USED_GOOD"
    assert offer.ok
    assert offer.value["offerId"] == "156719395011"
    assert policy.ok
    assert policy.value["itemConditionPolicies"][0]["itemConditions"][0]["conditionId"] == "3000"
    assert len(calls) == 3
    assert calls[2][1]["params"] == {"filter": "categoryIds:{14056}"}
    assert client.auth.resolve_calls == [False, False, False]
