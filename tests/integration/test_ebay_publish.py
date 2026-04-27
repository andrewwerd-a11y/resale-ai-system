"""
Integration tests for eBay publish flow.
ALL eBay and Cloudinary API calls are mocked — no real network requests.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from packages.ebay.src.inventory_client import EbayInventoryClient, _EbayApiError
from packages.core.src.result import Result
from packages.data.src.repositories.item_repo import ItemRepository
from packages.core.src.constants import ItemStatus
from tests.fixtures.sample_items import make_clothing_item
from tests.fixtures.mock_ebay import (
    OFFER_CREATE_SUCCESS,
    PUBLISH_SUCCESS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_mock_client(
    listing_id: str = "test-listing-456",
    listing_url: str = "https://www.sandbox.ebay.com/itm/test-listing-456",
    photo_urls: list | None = None,
    fail: bool = False,
    fail_message: str = "api_error",
) -> EbayInventoryClient:
    """Return an EbayInventoryClient with _publish_via_api mocked."""
    client = EbayInventoryClient.__new__(EbayInventoryClient)

    mock_auth = MagicMock()
    mock_auth.is_configured.return_value = True
    mock_auth.api_base = "https://api.sandbox.ebay.com"
    mock_auth.user_token = "fake-token"
    mock_auth.marketplace_id = "EBAY_US"
    mock_auth.settings = MagicMock()
    mock_auth.settings.ebay_environment = "sandbox"
    client.auth = mock_auth

    mock_uploader = MagicMock()
    mock_uploader.is_configured.return_value = True
    mock_uploader.upload_all.return_value = photo_urls or []
    client.uploader = mock_uploader

    if fail:
        client._publish_via_api = MagicMock(side_effect=Exception(fail_message))
    else:
        client._publish_via_api = MagicMock(return_value=(listing_id, listing_url))

    return client


# ── publish_item flow ─────────────────────────────────────────────────────────

def test_publish_item_returns_success(test_session):
    client = _make_mock_client(
        listing_id="test-listing-456",
        listing_url="https://www.sandbox.ebay.com/itm/test-listing-456",
        photo_urls=["https://cdn.example.com/photo1.jpg"],
    )
    item = make_clothing_item(sku="CL-000001", status="approved")
    result = client.publish_item(item)

    assert result.ok
    assert result.value["listing_id"] == "test-listing-456"
    assert result.value["listing_url"] == "https://www.sandbox.ebay.com/itm/test-listing-456"


def test_publish_item_returns_photo_urls(test_session):
    expected_photos = ["https://cdn.example.com/01.jpg", "https://cdn.example.com/02.jpg"]
    client = _make_mock_client(photo_urls=expected_photos)
    item = make_clothing_item(sku="CL-000002")
    result = client.publish_item(item)

    assert result.ok
    assert result.value["photo_urls"] == expected_photos


def test_unconfigured_client_returns_failure():
    client = EbayInventoryClient.__new__(EbayInventoryClient)
    mock_auth = MagicMock()
    mock_auth.is_configured.return_value = False
    client.auth = mock_auth
    client.uploader = MagicMock()

    item = make_clothing_item(sku="CL-000003")
    result = client.publish_item(item)

    assert not result.ok
    assert result.error_code == "NOT_CONFIGURED"


def test_failed_inventory_creation_returns_failure():
    client = _make_mock_client(fail=True, fail_message="inventory_item_error")
    item = make_clothing_item(sku="CL-000004")
    result = client.publish_item(item)

    assert not result.ok
    assert result.error_code == "API_ERROR"


def test_publish_failure_does_not_update_db(test_session):
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000005", status="approved")
    repo.upsert(item)

    client = _make_mock_client(fail=True, fail_message="timeout")
    result = client.publish_item(item)
    assert not result.ok

    # Status must remain unchanged
    fetched = repo.get_by_sku("CL-000005")
    assert fetched.status == "approved"
    assert fetched.listing_id is None


# ── item status updated after publish ─────────────────────────────────────────

def test_item_status_updated_to_listed_after_publish(test_session):
    import datetime
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000010", status="approved")
    repo.upsert(item)

    client = _make_mock_client(
        listing_id="listing-010",
        listing_url="https://www.sandbox.ebay.com/itm/listing-010",
    )
    result = client.publish_item(item)
    assert result.ok

    # Manually apply the status update (as the route does)
    data = result.value
    fetched = repo.get_by_sku("CL-000010")
    fetched.listing_id = data["listing_id"]
    fetched.listing_url = data["listing_url"]
    fetched.status = ItemStatus.LISTED
    fetched.platform = "ebay"
    fetched.date_listed = datetime.datetime.utcnow()
    repo.upsert(fetched)

    final = repo.get_by_sku("CL-000010")
    assert final.status == ItemStatus.LISTED
    assert final.listing_id == "listing-010"
    assert final.listing_url is not None


def test_listing_id_and_url_saved_to_db(test_session):
    import datetime
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000011", status="approved")
    repo.upsert(item)

    client = _make_mock_client(
        listing_id="specific-id-789",
        listing_url="https://www.sandbox.ebay.com/itm/specific-id-789",
    )
    result = client.publish_item(item)
    assert result.ok

    fetched = repo.get_by_sku("CL-000011")
    fetched.listing_id = result.value["listing_id"]
    fetched.listing_url = result.value["listing_url"]
    fetched.status = ItemStatus.LISTED
    repo.upsert(fetched)

    final = repo.get_by_sku("CL-000011")
    assert final.listing_id == "specific-id-789"
    assert "specific-id-789" in final.listing_url


# ── batch publishing ──────────────────────────────────────────────────────────

def test_batch_publish_counts_successes(test_session):
    repo = ItemRepository(test_session)
    for i in range(3):
        repo.upsert(make_clothing_item(sku=f"CL-0002{i}", status="approved"))

    items = repo.list_by_status("approved")
    published = 0
    failed = 0

    for item in items:
        client = _make_mock_client(listing_id=f"lst-{item.sku}")
        result = client.publish_item(item)
        if result.ok:
            published += 1
        else:
            failed += 1

    assert published == 3
    assert failed == 0


def test_batch_publish_partial_failure_counts_correctly(test_session):
    repo = ItemRepository(test_session)
    repo.upsert(make_clothing_item(sku="CL-000030", status="approved"))
    repo.upsert(make_clothing_item(sku="CL-000031", status="approved"))

    results = []
    clients = [
        _make_mock_client(listing_id="ok-listing"),
        _make_mock_client(fail=True, fail_message="rate_limit"),
    ]
    items = repo.list_by_status("approved")

    for item, client in zip(items, clients):
        results.append(client.publish_item(item))

    assert sum(1 for r in results if r.ok) == 1
    assert sum(1 for r in results if not r.ok) == 1


def test_publish_item_blocks_invalid_overlong_aspect_before_upload():
    client = EbayInventoryClient.__new__(EbayInventoryClient)

    mock_auth = MagicMock()
    mock_auth.is_configured.return_value = True
    mock_auth.resolve_user_token.return_value = {"token": "fake-token", "issue_code": None}
    mock_auth.settings = MagicMock()
    mock_auth.settings.ebay_app_id = "app-id"
    mock_auth.settings.ebay_cert_id = "cert-id"
    mock_auth.settings.ebay_environment = "sandbox"
    client.auth = mock_auth

    client.uploader = MagicMock()
    client.uploader.upload_all.side_effect = AssertionError("upload should not run when aspect validation fails")
    client._publish_via_api = MagicMock(side_effect=AssertionError("publish API should not run when aspect validation fails"))

    item = make_clothing_item(
        sku="CL-009999",
        item_specifics={"Theme": "x" * 70},
    )
    result = client.publish_item(item)

    assert not result.ok
    assert result.error_code == "ASPECT_VALIDATION"
    assert any("Aspect 'Theme' value exceeds eBay's 65-character limit" in blocker for blocker in result.details["blockers"])


def test_publish_item_recovers_existing_offer_and_continues(monkeypatch):
    client = EbayInventoryClient()
    client.auth.settings.ebay_sandbox_app_id = "app-id"
    client.auth.settings.ebay_sandbox_cert_id = "cert-id"
    client.auth.settings.ebay_sandbox_user_token = "fake-token"
    monkeypatch.setattr(
        client.auth,
        "resolve_user_token",
        lambda: {"token": "fake-token", "issue_code": None},
    )
    client.auth.settings.ebay_environment = "sandbox"
    monkeypatch.setattr(client.uploader, "upload_all", lambda _paths: [])
    monkeypatch.setattr(client, "get_seller_policies", lambda: {"fulfillment_id": "f", "payment_id": "p", "return_id": "r"})
    monkeypatch.setattr(client, "get_merchant_location_key", lambda: "default")
    monkeypatch.setattr(client, "_put", lambda *_args, **_kwargs: {})

    calls = {"publish_offer": 0}

    def fake_post(_url, _headers, _payload, **kwargs):
        step = kwargs.get("step", "")
        if step == "create_offer":
            raise _EbayApiError(
                409,
                "create_offer failed",
                '{"errors":[{"message":"Offer entity already exists","parameters":[{"name":"offerId","value":"156719395011"}]}]}',
            )
        if step == "publish_offer":
            calls["publish_offer"] += 1
            return {"listingId": "123456789012"}
        return {}

    monkeypatch.setattr(client, "_post", fake_post)

    result = client.publish_item(make_clothing_item(sku="CL-010001"))

    assert result.ok
    assert result.value["offer_id"] == "156719395011"
    assert result.value["recovered_existing_offer"] is True
    assert result.value["listing_id"] == "123456789012"
    assert calls["publish_offer"] == 1


def test_publish_item_existing_offer_without_offer_id_stays_failure(monkeypatch):
    client = EbayInventoryClient()
    client.auth.settings.ebay_sandbox_app_id = "app-id"
    client.auth.settings.ebay_sandbox_cert_id = "cert-id"
    client.auth.settings.ebay_sandbox_user_token = "fake-token"
    monkeypatch.setattr(
        client.auth,
        "resolve_user_token",
        lambda: {"token": "fake-token", "issue_code": None},
    )
    monkeypatch.setattr(client.uploader, "upload_all", lambda _paths: [])
    monkeypatch.setattr(client, "get_seller_policies", lambda: {"fulfillment_id": "f", "payment_id": "p", "return_id": "r"})
    monkeypatch.setattr(client, "get_merchant_location_key", lambda: "default")
    monkeypatch.setattr(client, "_put", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        client,
        "_post",
        lambda _url, _headers, _payload, **kwargs: (_ for _ in ()).throw(
            _EbayApiError(409, "create_offer failed", '{"errors":[{"message":"Offer entity already exists"}]}')
        ) if kwargs.get("step") == "create_offer" else {},
    )

    result = client.publish_item(make_clothing_item(sku="CL-010002"))

    assert not result.ok
    assert "no offerId was returned" in result.error
