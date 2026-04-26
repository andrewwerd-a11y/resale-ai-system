from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from packages.core.src.result import Result
from packages.data.src.models.item_record import ItemRecord
from packages.domain.src.entities.item import Item
from packages.sync.src.relister import AutoRelister


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_is_listing_active_treats_active_status_as_active(monkeypatch):
    class FakeClient:
        def get_listing_status(self, _listing_id: str):
            return Result.success({"status": "ACTIVE"})

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient", FakeClient)

    relister = AutoRelister()
    item = Item(sku="BK-000005", listing_id="123")
    assert relister._is_listing_active(item) is True


def test_is_listing_active_treats_ended_status_as_inactive(monkeypatch):
    class FakeClient:
        def get_listing_status(self, _listing_id: str):
            return Result.success({"status": "ENDED"})

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient", FakeClient)

    relister = AutoRelister()
    item = Item(sku="BK-000005", listing_id="123")
    assert relister._is_listing_active(item) is False


def test_is_listing_active_client_error_uses_safe_fallback(monkeypatch):
    class FakeClient:
        def get_listing_status(self, _listing_id: str):
            return Result.failure("upstream timeout")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient", FakeClient)

    relister = AutoRelister()
    item = Item(sku="BK-000005", listing_id="123")
    assert relister._is_listing_active(item) is True


def test_get_ended_listings_excludes_uncertain_status_items(monkeypatch):
    class FakeClient:
        def get_listing_status(self, listing_id: str):
            if listing_id == "L-ENDED":
                return Result.success({"status": "ENDED"})
            return Result.failure("unknown upstream failure")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient", FakeClient)

    with _session() as session:
        session.add(
            ItemRecord(
                sku="BK-000005",
                status="listed",
                listing_id="L-UNCERTAIN",
                title_final="Uncertain Item",
                item_specifics="{}",
            )
        )
        session.add(
            ItemRecord(
                sku="BK-000008",
                status="listed",
                listing_id="L-ENDED",
                title_final="Ended Item",
                item_specifics="{}",
            )
        )
        session.commit()

        relister = AutoRelister()
        ended = relister.get_ended_listings(session)

    ended_skus = {i.sku for i in ended}
    assert ended_skus == {"BK-000008"}
