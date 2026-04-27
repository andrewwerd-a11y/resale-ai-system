from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items, listings
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "photo_hosting.db"
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
        sku="BK-000008",
        status=ItemStatus.EXPORT_READY,
        title_raw="Fallback title",
        title_final="Ready title",
        description_final="Ready description",
        list_price=24.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=[],
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_ebay_calls(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("unexpected eBay call during photo hosting test")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", _fail)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.post", _fail)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.put", _fail)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.delete", _fail)


def test_host_photos_missing_sku_returns_404(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)

    with _client() as client:
        resp = client.post("/api/items/BK-000008/photos/host")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item BK-000008 not found"


def test_host_photos_requires_local_paths(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["https://images.example.test/BK-000008-01.jpg"])

    with _client() as client:
        resp = client.post("/api/items/BK-000008/photos/host")

    assert resp.status_code == 400
    body = resp.json()["detail"]
    assert body["sku"] == "BK-000008"
    assert body["already_hosted"] == 1
    assert body["detail"] == "No local photo paths are available to host."


def test_host_photos_requires_cloudinary_config(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    local_photo = tmp_path / "BK-000008-01.jpg"
    local_photo.write_bytes(b"photo")
    _seed_item(image_paths=[str(local_photo)])

    with _client() as client:
        resp = client.post("/api/items/BK-000008/photos/host")

    assert resp.status_code == 503
    body = resp.json()["detail"]
    assert body["sku"] == "BK-000008"
    assert body["needs_hosting"] is True
    assert body["detail"] == "Cloudinary is not configured."


def test_host_photos_dry_run_reports_files_without_uploading(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "demo-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "demo-secret")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    local_photo = tmp_path / "BK-000008-01.jpg"
    local_photo.write_bytes(b"photo")
    _seed_item(image_paths=[str(local_photo)])

    def _fail(*_args, **_kwargs):
        raise AssertionError("dry run should not upload photos")

    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", _fail)

    with _client() as client:
        resp = client.post("/api/items/BK-000008/photos/host?dry_run=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "BK-000008"
    assert body["uploaded"] == 0
    assert body["dry_run"] is True
    assert body["would_upload"] == [str(local_photo)]


def test_host_photos_success_persists_urls_and_publish_preview_uses_them(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_ebay_calls(monkeypatch)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "demo-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "demo-secret")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    local_photo_1 = tmp_path / "BK-000008-01.jpg"
    local_photo_2 = tmp_path / "BK-000008-02.jpg"
    local_photo_1.write_bytes(b"photo-1")
    local_photo_2.write_bytes(b"photo-2")
    _seed_item(image_paths=[str(local_photo_1), str(local_photo_2)])

    uploaded = {
        str(local_photo_1): "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg",
        str(local_photo_2): "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-02.jpg",
    }

    def _mock_upload(self, image_path):
        from packages.core.src.result import Result

        return Result.success(uploaded[str(image_path)])

    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", _mock_upload)

    with _client() as client:
        before = client.get("/api/listings/BK-000008/publish-preview")
        resp = client.post("/api/items/BK-000008/photos/host")
        after = client.get("/api/listings/BK-000008/publish-preview")

    assert before.status_code == 200
    assert before.json()["readiness"]["checks"]
    assert before.json()["photo_input_summary"]["hosted_photo_urls"] == []

    assert resp.status_code == 200
    body = resp.json()
    assert body["uploaded"] == 2
    assert body["already_hosted"] == 0
    assert body["needs_hosting"] is False
    assert body["hosted_photo_urls"] == [uploaded[str(local_photo_1)], uploaded[str(local_photo_2)]]

    assert after.status_code == 200
    after_body = after.json()
    assert after_body["photo_input_summary"]["hosted_photo_urls"] == [
        uploaded[str(local_photo_1)],
        uploaded[str(local_photo_2)],
    ]
    photo_check = next(check for check in after_body["readiness"]["checks"] if check["name"] == "photo_hosting_readiness")
    assert photo_check["context"]["has_hosted_photo_urls"] is True
    assert photo_check["context"]["needs_hosting"] is False

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-000008")
        assert item is not None
        assert item.image_paths == [
            str(local_photo_1),
            str(local_photo_2),
            uploaded[str(local_photo_1)],
            uploaded[str(local_photo_2)],
        ]


def test_host_photos_is_idempotent_when_urls_already_exist(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo-cloud")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "demo-key")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "demo-secret")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    local_photo = tmp_path / "BK-000008-01.jpg"
    local_photo.write_bytes(b"photo")
    hosted_url = "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"
    _seed_item(image_paths=[str(local_photo), hosted_url])

    def _fail(*_args, **_kwargs):
        raise AssertionError("already hosted SKU should not upload again")

    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", _fail)

    with _client() as client:
        first = client.post("/api/items/BK-000008/photos/host")
        second = client.post("/api/items/BK-000008/photos/host")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["uploaded"] == 0
    assert second.json()["uploaded"] == 0
    assert first.json()["already_hosted"] == 1
    assert second.json()["already_hosted"] == 1

    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-000008")
        assert item is not None
        assert item.image_paths == [str(local_photo), hosted_url]
