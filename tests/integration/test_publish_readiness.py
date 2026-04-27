from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import listings
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "publish_readiness.db"
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
        sku="BK-000005",
        status=ItemStatus.APPROVED,
        title_raw="Fallback title",
        title_final="Ready title",
        description_final="Ready description",
        list_price=24.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=["C:/tmp/BK-000005-01.jpg"],
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_external_calls(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("unexpected external call during readiness check")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", _fail)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.post", _fail)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.put", _fail)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.delete", _fail)
    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", _fail)
    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload_all", _fail)


def test_publish_readiness_returns_ready_true_for_complete_approved_item(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    local_photo = tmp_path / "BK-000005-01.jpg"
    local_photo.write_bytes(b"ready")
    _seed_item(image_paths=[str(local_photo)])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "BK-000005"
    assert body["ready"] is True
    assert body["blockers"] == []
    assert any(check["name"] == "photos_present" and check["ok"] is True for check in body["checks"])


def test_publish_readiness_returns_blockers_for_missing_required_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(
        title_raw="",
        title_final="",
        description_final="",
        list_price=None,
        ebay_category_id="",
        condition_id="",
        image_paths=[],
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert "Missing required field: title." in body["blockers"]
    assert "Missing required field: price." in body["blockers"]
    assert "Missing required field: category_id." in body["blockers"]
    assert "Missing required field: condition_id." in body["blockers"]
    assert "No photos are attached to this item." in body["blockers"]


def test_publish_readiness_returns_clear_not_found_result(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["sku"] == "BK-000005"
    assert detail["ready"] is False
    assert detail["blockers"] == ["Item BK-000005 not found."]


def test_publish_readiness_blocks_non_approved_sku_when_guard_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(sku="BK-999999")

    with _client() as client:
        resp = client.get("/api/listings/BK-999999/publish-readiness")

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")


def test_publish_readiness_keeps_backward_compatibility_when_guard_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(sku="BK-999999", status=ItemStatus.NEEDS_REVIEW)

    with _client() as client:
        resp = client.get("/api/listings/BK-999999/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "BK-999999"
    assert body["ready"] is False
    assert any("not publishable" in blocker for blocker in body["blockers"])


def test_publish_readiness_hosted_photo_urls_satisfy_photo_hosting(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(image_paths=["https://images.example.test/BK-000005-01.jpg"])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    photo_check = next(check for check in resp.json()["checks"] if check["name"] == "photo_hosting_readiness")
    assert photo_check["ok"] is True
    assert photo_check["context"]["has_hosted_photo_urls"] is True
    assert photo_check["context"]["needs_hosting"] is False


def test_publish_readiness_local_only_photos_require_hosting_without_upload(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    local_photo = tmp_path / "BK-000005-local.jpg"
    local_photo.write_bytes(b"local")
    _seed_item(image_paths=[str(local_photo)])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    photo_check = next(check for check in body["checks"] if check["name"] == "photo_hosting_readiness")
    assert body["ready"] is True
    assert photo_check["ok"] is True
    assert photo_check["context"]["has_local_photos"] is True
    assert photo_check["context"]["needs_hosting"] is True
    assert photo_check["context"]["cloudinary_config_present"] is False
    assert "Host local photos before sandbox or live publish." in body["required_actions"]
    assert "Cloudinary is not configured" in " ".join(body["warnings"])


def test_publish_readiness_missing_local_photo_files_are_surfaced(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    missing_path = tmp_path / "missing-photo.jpg"
    _seed_item(image_paths=[str(missing_path)])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    photo_check = next(check for check in body["checks"] if check["name"] == "photo_hosting_readiness")
    assert body["ready"] is False
    assert photo_check["ok"] is False
    assert photo_check["context"]["missing_photo_files"] == [str(missing_path)]
    assert "Some stored local photo paths no longer exist on disk." in body["warnings"]
