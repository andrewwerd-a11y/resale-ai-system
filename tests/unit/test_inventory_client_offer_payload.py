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
