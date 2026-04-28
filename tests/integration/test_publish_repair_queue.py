from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from apps.api.src.routes import ebay, listings
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.publish_attempt_record import PublishAttemptRecord
from packages.data.src.models.publish_repair_decision_record import PublishRepairDecisionRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "publish_repair_queue.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")

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
        title_raw="Repair raw title",
        title_final="Repair title",
        description_final="Repair description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        item_specifics={},
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _plans_for_sku(sku: str) -> list[PublishRepairPlanRecord]:
    with Session(sqlite_db.engine) as session:
        return session.exec(
            select(PublishRepairPlanRecord)
            .where(PublishRepairPlanRecord.sku == sku)
            .order_by(PublishRepairPlanRecord.updated_at.desc())
        ).all()


def _attempts_for_sku(sku: str) -> list[PublishAttemptRecord]:
    with Session(sqlite_db.engine) as session:
        return session.exec(
            select(PublishAttemptRecord)
            .where(PublishAttemptRecord.sku == sku)
            .order_by(PublishAttemptRecord.attempted_at.desc())
        ).all()


def _decisions_for_sku(sku: str) -> list[PublishRepairDecisionRecord]:
    with Session(sqlite_db.engine) as session:
        return session.exec(
            select(PublishRepairDecisionRecord)
            .where(PublishRepairDecisionRecord.sku == sku)
            .order_by(PublishRepairDecisionRecord.created_at.desc())
        ).all()


def _fail_publish_if_called(*_args, **_kwargs):
    raise AssertionError("publish_item should not be called by draft/apply/recheck endpoints")


def test_failed_publish_creates_repair_queue_entry(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body="Error 25021: invalid item condition information. The provided condition id is invalid for the selected primary category id.",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "publish_readiness_blocked"
    assert detail["repair_plan"]
    assert _attempts_for_sku("BK-000008")
    assert _plans_for_sku("BK-000008")


def test_25021_creates_high_risk_condition_repair_plan(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body="Error 25021: invalid item condition information. The provided condition id is invalid for the selected primary category id.",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")

    plan = _plans_for_sku("BK-000008")[0]
    assert plan.classified_error_code == "invalid_category_condition"
    assert plan.risk_level == "high"
    assert plan.affected_field == "condition_id"


def test_invalid_image_url_creates_low_risk_photo_repair_plan(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000008-01.jpg"])

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay image URL validation failed before publish.",
            error_code="INVALID_IMAGE_URL",
            invalid_image_urls=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000008-01.jpg"],
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 400
    plan = _plans_for_sku("BK-000008")[0]
    assert plan.classified_error_code == "invalid_image_url"
    assert plan.risk_level == "low"
    assert plan.safe_to_auto_apply is True


def test_offer_already_exists_creates_offer_recovery_plan(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 409: create_offer failed",
            error_code="API_ERROR",
            body='{"errors":[{"message":"Offer entity already exists","parameters":[{"name":"offerId","value":"156719395011"}]}]}',
            offer_id="156719395011",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")

    plan = _plans_for_sku("BK-000008")[0]
    assert plan.classified_error_code == "offer_already_exists"
    assert plan.safe_to_auto_apply is True


def test_already_published_creates_listing_sync_plan(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(offer_id="156719395011")

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 409: publish_offer failed",
            error_code="API_ERROR",
            body="Offer is already published",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")

    plan = _plans_for_sku("BK-000008")[0]
    assert plan.classified_error_code == "already_published"
    assert plan.repair_layer == "listing_sync"


def test_auth_failure_creates_auth_repair_plan(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay credentials are not ready for authenticated requests.",
            error_code="AUTH_NOT_READY",
            auth_issue_code="missing_token",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 503
    plan = _plans_for_sku("BK-000008")[0]
    assert plan.classified_error_code == "auth_failure"


def test_rate_limit_creates_transient_repair_plan(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item()

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 429: publish_offer failed",
            error_code="API_ERROR",
            body="Rate limit exceeded",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")

    plan = _plans_for_sku("BK-000008")[0]
    assert plan.classified_error_code == "ebay_rate_limited"
    assert plan.retry_allowed is False


def test_publish_preview_surfaces_last_repair_status(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body="Error 25021: invalid item condition information. The provided condition id is invalid for the selected primary category id.",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")
        preview = client.get("/api/listings/BK-000008/publish-preview")

    assert preview.status_code == 200
    body = preview.json()
    assert body["repair_status"]["has_open_repair"] is True
    assert body["repair_status"]["risk_level"] == "high"


def test_recheck_readiness_marks_ready_to_retry_when_blockers_clear(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body="Error 25021: invalid item condition information. The provided condition id is invalid for the selected primary category id.",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")

    with Session(sqlite_db.engine) as session:
        repo = ItemRepository(session)
        item = repo.get_by_sku("BK-000008")
        item.condition_id = "3000"
        repo.upsert(item)

    with _client() as client:
        recheck = client.post("/api/ebay/repair-queue/BK-000008/recheck-readiness")

    assert recheck.status_code == 200
    assert recheck.json()["ready_to_retry"] is True


def test_draft_fix_generates_high_risk_condition_draft_without_previous_publish_attempt(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        resp = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        detail = client.get("/api/ebay/repair-queue/BK-000008")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft_fix_available"
    assert body["drafts"]
    draft = body["drafts"][0]
    assert draft["affected_field"] == "condition_id"
    assert draft["repair_layer"] == "category_compatibility"
    assert draft["risk_level"] == "high"
    assert draft["safe_to_auto_apply"] is False
    assert draft["requires_review"] is True
    assert draft["retry_allowed"] is False
    assert draft["current_value"]["category_id"] == "14056"
    assert draft["current_value"]["condition_id"] == "5000"
    assert draft["expected_value"]["allowed_condition_ids"] == ["1000", "1500", "3000", "4000"]
    assert draft["expected_value"]["policy_source"]
    assert draft["suggested_value"]["allowed_options"]
    assert any(option["id"] == "3000" and option["name"] == "Used" for option in draft["suggested_value"]["allowed_options"])
    assert any(option["id"] == "4000" and option["name"] == "Very Good" for option in draft["suggested_value"]["allowed_options"])
    assert detail.status_code == 200
    assert detail.json()["latest_publish_attempt"] is None
    assert _attempts_for_sku("BK-000008") == []


def test_draft_fix_warning_only_public_image_urls_do_not_generate_repair_draft(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=[
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg",
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-02.jpg",
            r"C:\Users\Andrew\Desktop\BK-000008-01.jpg",
        ],
    )
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        resp = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_blockers"
    assert body["drafts"] == []


def test_repeated_draft_fix_calls_do_not_create_duplicate_open_plans(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        first = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        second = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")

    assert first.status_code == 200
    assert second.status_code == 200
    plans = _plans_for_sku("BK-000008")
    matching = [plan for plan in plans if plan.affected_field == "condition_id" and plan.classified_error_code == "invalid_category_condition"]
    assert len(matching) == 1
    assert first.json()["drafts"][0]["id"] == second.json()["drafts"][0]["id"]


def test_bulk_draft_fixes_uses_readiness_derived_drafts_and_unresolved_blockers(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008", ebay_category_id="14056", condition_id="5000")
    local_photo = tmp_path / "BK-000009-local.jpg"
    local_photo.write_bytes(b"local")
    _seed_item(sku="BK-000009", image_paths=[str(local_photo)])
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        resp = client.post(
            "/api/ebay/repair-queue/bulk-draft-fixes",
            json={
                "skus": ["BK-000008", "BK-000009"],
                "mode": "draft_only",
                "allow_low_risk_auto_apply": False,
                "allow_medium_risk_drafts": True,
                "allow_high_risk_drafts": True,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert any(entry["sku"] == "BK-000008" for entry in body["high_risk_manual_review_fixes"])
    assert any(entry["sku"] == "BK-000009" and entry["reason"] == "unresolved_blockers" for entry in body["unresolved_errors"])


def test_recheck_endpoint_does_not_call_publish(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        resp = client.post("/api/ebay/repair-queue/BK-000008/recheck-readiness")

    assert resp.status_code == 200
    assert resp.json()["ready_to_retry"] is False


def test_repair_queue_bk_000008_mixed_hosted_and_local_urls_only_blocks_on_condition(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="14056",
        condition_id="5000",
        image_paths=[
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg",
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-02.jpg",
            "https://res.cloudinary.com/demo/image/upload/v1/BK-000008-03.jpg",
            r"C:\Users\Andrew\Desktop\BK-000008-01.jpg",
            r"C:\Users\Andrew\Desktop\BK-000008-02.jpg",
            r"C:\Users\Andrew\Desktop\BK-000008-03.jpg",
        ],
    )

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body="Error 25021: invalid item condition information. The provided condition id is invalid for the selected primary category id.",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")
        detail = client.get("/api/ebay/repair-queue/BK-000008")
        recheck = client.post("/api/ebay/repair-queue/BK-000008/recheck-readiness")

    assert detail.status_code == 200
    detail_body = detail.json()
    public_image_check = next(
        check
        for check in detail_body["compatibility_summary"]["checks"]
        if check["name"] == "public_image_urls"
    )
    assert public_image_check["ok"] is True
    assert public_image_check["blocking"] is False
    assert "only hosted public URLs will be sent to eBay" in str(public_image_check["warning"] or "")

    assert recheck.status_code == 200
    recheck_body = recheck.json()
    assert recheck_body["ready_to_retry"] is False
    assert recheck_body["compatibility"]["blockers"] == [
        "Condition ID '5000' is not allowed for category '14056'."
    ]
    recheck_public_image_check = next(
        check
        for check in recheck_body["compatibility"]["checks"]
        if check["name"] == "public_image_urls"
    )
    assert recheck_public_image_check["ok"] is True
    assert recheck_public_image_check["blocking"] is False


def test_apply_high_risk_fix_without_explicit_value_is_rejected(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        draft = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        plan_id = draft.json()["drafts"][0]["id"]
        resp = client.post(
            "/api/ebay/repair-queue/BK-000008/apply-draft-fix",
            json={"sku": "BK-000008", "repair_plan_id": plan_id, "approved": True},
        )

    assert resp.status_code == 400


def test_apply_approved_high_risk_fix_stores_before_after_audit(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="5000")
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", _fail_publish_if_called)

    with _client() as client:
        draft = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        plan_id = draft.json()["drafts"][0]["id"]
        resp = client.post(
            "/api/ebay/repair-queue/BK-000008/apply-draft-fix",
            json={"sku": "BK-000008", "repair_plan_id": plan_id, "approved": True, "edited_value": "3000"},
        )

    assert resp.status_code == 200
    assert resp.json()["recheck"]["ready_to_retry"] is True
    decisions = _decisions_for_sku("BK-000008")
    assert decisions
    assert "5000" in decisions[0].before_value_json
    assert "3000" in decisions[0].after_value_json


def test_apply_low_risk_fix_records_before_after_audit(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(image_paths=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000008-01.jpg"])

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay image URL validation failed before publish.",
            error_code="INVALID_IMAGE_URL",
            invalid_image_urls=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000008-01.jpg"],
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000008")
        client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        plan_id = _plans_for_sku("BK-000008")[0].id
        resp = client.post(
            "/api/ebay/repair-queue/BK-000008/apply-draft-fix",
            json={"sku": "BK-000008", "repair_plan_id": plan_id, "approved": True},
        )

    assert resp.status_code == 200
    decisions = _decisions_for_sku("BK-000008")
    assert decisions
    assert "https:\\\\" in decisions[0].before_value_json
    assert "https://res.cloudinary.com" in decisions[0].after_value_json


def test_bulk_draft_fixes_groups_low_medium_high_without_publishing(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000005", image_paths=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000005-01.jpg"])
    _seed_item(sku="BK-000008", ebay_category_id="14056", condition_id="5000")
    _seed_item(sku="BK-000009", missing_required_fields=["Brand"])

    publish_calls = {"count": 0}

    def fake_publish_fail(_self, item):
        publish_calls["count"] += 1
        if item.sku == "BK-000005":
            return Result.failure("bad image", error_code="INVALID_IMAGE_URL", invalid_image_urls=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000005-01.jpg"])
        if item.sku == "BK-000008":
            return Result.failure("bad condition", error_code="API_ERROR", body="Error 25021: invalid item condition information.")
        return Result.failure("missing required aspect", error_code="API_ERROR", body="Missing required aspect Brand")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000005")
        client.post("/api/ebay/publish/BK-000008")
        client.post("/api/ebay/publish/BK-000009")
        resp = client.post(
            "/api/ebay/repair-queue/bulk-draft-fixes",
            json={
                "skus": ["BK-000005", "BK-000008", "BK-000009"],
                "mode": "draft_only",
                "allow_low_risk_auto_apply": False,
                "allow_medium_risk_drafts": True,
                "allow_high_risk_drafts": True,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["safe_low_risk_fixes"]
    assert body["medium_risk_review_fixes"]
    assert body["high_risk_manual_review_fixes"]
    assert publish_calls["count"] == 1


def test_bulk_apply_only_applies_explicit_approvals(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000005", image_paths=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000005-01.jpg"])
    _seed_item(sku="BK-000008", ebay_category_id="14056", condition_id="5000")

    def fake_publish_fail(_self, item):
        if item.sku == "BK-000005":
            return Result.failure("bad image", error_code="INVALID_IMAGE_URL", invalid_image_urls=["https:\\\\res.cloudinary.com\\demo\\image\\upload\\v1\\BK-000005-01.jpg"])
        return Result.failure("bad condition", error_code="API_ERROR", body="Error 25021: invalid item condition information.")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        client.post("/api/ebay/publish/BK-000005")
        client.post("/api/ebay/publish/BK-000008")
        client.post("/api/ebay/repair-queue/BK-000005/draft-fix")
        client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        plans = {plan.sku: plan for plan in _plans_for_sku("BK-000005") + _plans_for_sku("BK-000008")}
        resp = client.post(
            "/api/ebay/repair-queue/bulk-apply-approved-fixes",
            json={
                "approvals": [
                    {"sku": "BK-000005", "repair_plan_id": plans["BK-000005"].id, "approved": True},
                    {"sku": "BK-000008", "repair_plan_id": plans["BK-000008"].id, "approved": False},
                ]
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["applied"]) == 1
    assert len(body["rejected"]) == 1


def test_publish_route_refuses_before_mutation_when_blockers_exist(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(description_final="")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("live publish should not be called when preflight blockers exist")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_if_called)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "publish_readiness_blocked"
    assert detail["repair_queue_entry_created"] is True
