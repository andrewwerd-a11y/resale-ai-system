from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "photo_metadata_routes.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item(**overrides) -> None:
    base = dict(
        sku="BK-ROUTE",
        status=ItemStatus.NEEDS_REVIEW,
        title_final="A Book",
        category_key="books",
        image_paths=["front-cover.jpg", "spine.jpg", "flaw.jpg"],
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_ebay_calls(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("photo metadata routes must not call eBay clients")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_inventory_item", _fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_offer", _fail)
    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", _fail)


def test_get_photo_metadata_works_without_stored_labels(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    with _client() as client:
        resp = client.get("/api/items/BK-ROUTE/photos/metadata")

    assert resp.status_code == 200
    body = resp.json()
    assert body["local_only"] is True
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_publish_performed"] is True
    assert body["manual_approval_required"] is True
    assert [photo["image_path"] for photo in body["photos"]] == ["front-cover.jpg", "spine.jpg", "flaw.jpg"]
    assert {"value": "title_page", "label": "Title page"} in body["photo_type_options"]


def test_patch_photo_metadata_labels_front_cover_spine_and_flaw(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_ebay_calls(monkeypatch)
    _seed_item()

    with _client() as client:
        resp = client.patch(
            "/api/items/BK-ROUTE/photos/metadata",
            json={
                "updates": [
                    {"image_path": "front-cover.jpg", "photo_type": "front"},
                    {"image_path": "spine.jpg", "photo_type": "spine"},
                    {"image_path": "flaw.jpg", "photo_type": "flaw"},
                ]
            },
        )

    assert resp.status_code == 200
    photos = {photo["image_path"]: photo for photo in resp.json()["photos"]}
    assert photos["front-cover.jpg"]["photo_type"] == "front"
    assert photos["front-cover.jpg"]["label_source"] == "user_labeled"
    assert photos["spine.jpg"]["photo_type"] == "spine"
    assert photos["flaw.jpg"]["photo_type"] == "flaw"


def test_patch_photo_metadata_accepts_friendly_title_page_label(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_ebay_calls(monkeypatch)
    _seed_item()

    with _client() as client:
        resp = client.patch(
            "/api/items/BK-ROUTE/photos/metadata",
            json={"updates": [{"image_path": "front-cover.jpg", "photo_type": "title page"}]},
        )

    assert resp.status_code == 200
    photos = {photo["image_path"]: photo for photo in resp.json()["photos"]}
    assert photos["front-cover.jpg"]["photo_type"] == "title_page"


def test_patch_photo_metadata_rejects_invalid_photo_type(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    with _client() as client:
        resp = client.patch(
            "/api/items/BK-ROUTE/photos/metadata",
            json={"updates": [{"image_path": "spine.jpg", "photo_type": "banana"}]},
        )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "Invalid photo_type" in detail
    assert "friendly label" in detail
    assert "title_page (Title page)" in detail


def test_photo_metadata_update_does_not_change_item_status(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(status=ItemStatus.EXPORT_READY)

    with _client() as client:
        resp = client.patch(
            "/api/items/BK-ROUTE/photos/metadata",
            json={"updates": [{"image_path": "spine.jpg", "photo_type": "spine"}]},
        )

    assert resp.status_code == 200
    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-ROUTE")
        assert item is not None
        assert item.status == ItemStatus.EXPORT_READY
