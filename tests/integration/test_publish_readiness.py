from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import listings
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.publish_attempt_record import PublishAttemptRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
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


def _seed_blocking_repair_plan(
    *,
    sku: str = "BK-000005",
    publish_attempt_id: str = "attempt-blocked",
    repair_plan_id: str | None = None,
    classified_error_code: str = "requires_publish_decision_after_refresh",
) -> str:
    with Session(sqlite_db.engine) as session:
        attempt = PublishAttemptRecord(
            id=publish_attempt_id,
            sku=sku,
            stage="publish_offer",
            status="failed",
            classified_error_code=classified_error_code,
            repair_layer="post_refresh_publish_decision",
            requires_review=True,
            retry_allowed=False,
        )
        session.add(attempt)
        plan = PublishRepairPlanRecord(
            id=repair_plan_id,
            sku=sku,
            publish_attempt_id=publish_attempt_id,
            status="needs_manual_review",
            affected_field="publish_readiness",
            current_value_json="{}",
            expected_value_json="{}",
            suggested_value_json="{}",
            suggested_actions_json='["Review the active repair plan and complete the required manual publish decision."]',
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=True,
            retry_allowed=False,
            source="stale_offer_refresh",
            repair_layer="post_refresh_publish_decision",
            classified_error_code=classified_error_code,
        )
        session.add(plan)
        session.commit()
        return plan.id


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
    _seed_item(image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000005-01.jpg"])

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
    compatibility_check = next(check for check in body["checks"] if check["name"] == "category_publish_compatibility")
    public_image_check = next(
        check for check in compatibility_check["context"]["checks"] if check["name"] == "public_image_urls"
    )
    assert body["ready"] is False
    assert photo_check["ok"] is True
    assert photo_check["context"]["has_local_photos"] is True
    assert photo_check["context"]["needs_hosting"] is True
    assert photo_check["context"]["cloudinary_config_present"] is False
    assert compatibility_check["ok"] is False
    assert public_image_check["ok"] is False
    assert public_image_check["action"] == "Host local photos before publish."
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


def test_publish_readiness_mixed_hosted_and_local_urls_warns_but_does_not_block(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(
        image_paths=[
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000005-01.jpg",
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000005-02.jpg",
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000005-03.jpg",
            r"C:\Users\Andrew\Desktop\BK-000005-01.jpg",
            r"C:\Users\Andrew\Desktop\BK-000005-02.jpg",
            r"C:\Users\Andrew\Desktop\BK-000005-03.jpg",
        ]
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    compatibility_check = next(check for check in body["checks"] if check["name"] == "category_publish_compatibility")
    public_image_check = next(
        check for check in compatibility_check["context"]["checks"] if check["name"] == "public_image_urls"
    )
    assert compatibility_check["ok"] is True
    assert public_image_check["ok"] is True
    assert public_image_check["blocking"] is False
    assert "only hosted public URLs will be sent to eBay" in str(public_image_check["warning"] or "")
    assert "Repair malformed hosted image URLs before retrying publish." not in body["required_actions"]


def test_publish_readiness_local_only_paths_block_compatibility(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    local_photo = tmp_path / "BK-000005-local.jpg"
    local_photo.write_bytes(b"local")
    _seed_item(image_paths=[str(local_photo)])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    compatibility_check = next(check for check in body["checks"] if check["name"] == "category_publish_compatibility")
    public_image_check = next(
        check for check in compatibility_check["context"]["checks"] if check["name"] == "public_image_urls"
    )
    assert body["ready"] is False
    assert compatibility_check["ok"] is False
    assert public_image_check["ok"] is False
    assert public_image_check["action"] == "Host local photos before publish."


def test_publish_readiness_malformed_hosted_url_blocks_compatibility(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(image_paths=["https://", r"C:\Users\Andrew\Desktop\BK-000005-01.jpg"])

    with _client() as client:
        resp = client.get("/api/listings/BK-000005/publish-readiness")

    assert resp.status_code == 200
    body = resp.json()
    compatibility_check = next(check for check in body["checks"] if check["name"] == "category_publish_compatibility")
    public_image_check = next(
        check for check in compatibility_check["context"]["checks"] if check["name"] == "public_image_urls"
    )
    assert body["ready"] is False
    assert compatibility_check["ok"] is False
    assert public_image_check["ok"] is False
    assert public_image_check["action"] == "Repair malformed hosted image URLs before retrying publish."


def test_publish_readiness_publish_preview_and_diagnostics_agree_on_active_repair_blocker(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    _configure_temp_db(monkeypatch, tmp_path)
    _block_external_calls(monkeypatch)
    _seed_item(
        status=ItemStatus.EXPORT_READY,
        ebay_category_id="14056",
        condition_id="3000",
        offer_id="156719395011",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000005-01.jpg"],
    )
    repair_plan_id = _seed_blocking_repair_plan(sku="BK-000005")

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("publish readiness/preview/diagnostics must not perform live eBay mutation")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail_mutation)

    with _client() as client:
        readiness_resp = client.get("/api/listings/BK-000005/publish-readiness")
        preview_resp = client.get("/api/listings/BK-000005/publish-preview")
        diagnostics_resp = client.get("/api/listings/BK-000005/publish-diagnostics")

    assert readiness_resp.status_code == 200
    assert preview_resp.status_code == 200
    assert diagnostics_resp.status_code == 200

    readiness = readiness_resp.json()
    preview = preview_resp.json()
    diagnostics = diagnostics_resp.json()

    assert readiness["ready"] is False
    assert readiness["blocked_by_repair_queue"] is True
    assert "blocked_by_repair_queue" in readiness["blockers"]
    assert readiness["repair_plan_id"] == repair_plan_id
    assert readiness["retry_allowed"] is False
    assert readiness["classified_error_code"] == "requires_publish_decision_after_refresh"
    assert any(
        action == "Resolve or supersede the active repair plan in the repair queue before publishing."
        for action in readiness["required_actions"]
    )
    not_blocked_check = next(check for check in readiness["checks"] if check["name"] == "not_blocked_from_publish")
    assert not_blocked_check["ok"] is False
    assert not_blocked_check["context"]["repair_plan_id"] == repair_plan_id

    assert preview["blocked_by_repair_queue"] is True
    assert preview["would_publish"] is False
    assert preview["mutation_allowed"] is False
    assert preview["retry_allowed"] is False
    assert preview["repair_plan_id"] == repair_plan_id
    assert preview["classified_error_code"] == "requires_publish_decision_after_refresh"

    assert diagnostics["blocked_by_repair_queue"] is True
    assert diagnostics["ready_to_retry"] is False
    assert diagnostics["repair_plan_id"] == repair_plan_id
    assert diagnostics["retry_allowed"] is False
    assert diagnostics["classified_error_code"] == "requires_publish_decision_after_refresh"
    assert diagnostics["local_publish_ready"] is True
    assert diagnostics["effective_publish_ready"] is False
    assert diagnostics["publish_block_summary"] == "Item data is locally publish-ready, but publish is blocked by active repair plan."
    assert "blocked_by_repair_queue" in diagnostics["effective_publish_blockers"]

    assert readiness["repair_plan_id"] == preview["repair_plan_id"] == diagnostics["repair_plan_id"]
    assert readiness["retry_allowed"] == preview["retry_allowed"] == diagnostics["retry_allowed"]
    assert readiness["classified_error_code"] == preview["classified_error_code"] == diagnostics["classified_error_code"]
