from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from apps.api.src.routes import ebay, listings, sync
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.publish_attempt_record import PublishAttemptRecord
from packages.data.src.models.publish_repair_decision_record import PublishRepairDecisionRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item
from apps.api.src.services.stale_offer_remediation import REQUIRED_TYPED_CONFIRMATION, build_remediation_payload_hash


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
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


def _seed_blocking_repair_plan(
    sku: str = "BK-000008",
    *,
    status: str = "needs_manual_review",
    retry_allowed: bool = False,
    requires_review: bool = True,
    updated_at: datetime | None = None,
    publish_attempt_id: str | None = "attempt-blocked",
) -> str:
    with Session(sqlite_db.engine) as session:
        if publish_attempt_id:
            attempt = PublishAttemptRecord(
                id=publish_attempt_id,
                sku=sku,
                stage="publish_offer",
                status="failed",
                ebay_error_id="25021",
                ebay_error_message="The selected condition ID is invalid for the exact eBay category.",
                classified_error_code="invalid_category_condition",
                repair_layer="category_compatibility",
                requires_review=True,
                retry_allowed=False,
            )
            session.add(attempt)
        plan = PublishRepairPlanRecord(
            sku=sku,
            publish_attempt_id=publish_attempt_id,
            status=status,
            affected_field="condition_id",
            current_value_json=json.dumps({"category_id": "14056", "condition_id": "3000"}),
            expected_value_json=json.dumps(
                {
                    "category_id": "14056",
                    "known": True,
                    "allowed_condition_ids": ["1000", "1500", "3000", "4000"],
                    "source": "builtin",
                }
            ),
            suggested_value_json=json.dumps({"condition_id": ""}),
            suggested_actions_json=json.dumps(["Review category/condition compatibility before retrying publish."]),
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=requires_review,
            retry_allowed=retry_allowed,
            source="ebay_error",
            repair_layer="category_compatibility",
            classified_error_code="invalid_category_condition",
            updated_at=updated_at or datetime.utcnow(),
        )
        session.add(plan)
        session.commit()
        return plan.id


def _decisions_for_sku(sku: str) -> list[PublishRepairDecisionRecord]:
    with Session(sqlite_db.engine) as session:
        return session.exec(
            select(PublishRepairDecisionRecord)
            .where(PublishRepairDecisionRecord.sku == sku)
            .order_by(PublishRepairDecisionRecord.created_at.desc())
        ).all()


def _fail_publish_if_called(*_args, **_kwargs):
    raise AssertionError("publish_item should not be called by draft/apply/recheck endpoints")


def _eligible_refresh_diagnostics() -> dict:
    inventory_payload = {
        "condition": "USED_GOOD",
        "product": {
            "title": "Repair title",
            "description": "Repair description",
            "imageUrls": ["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        },
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
    }
    offer_payload = {
        "sku": "BK-000008",
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "categoryId": "14056",
        "listingDescription": "Repair description",
        "merchantLocationKey": "preview-location",
        "listingPolicies": {
            "fulfillmentPolicyId": "preview-fulfillment-policy",
            "paymentPolicyId": "preview-payment-policy",
            "returnPolicyId": "preview-return-policy",
            "countryCode": "US",
        },
        "pricingSummary": {"price": {"currency": "USD", "value": "22.00"}},
        "includeCatalogProductDetails": False,
    }
    draft = {
        "sku": "BK-000008",
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "remediation_type": "refresh_existing_unpublished_offer",
        "live_execution_enabled": False,
        "operator_approval_required": True,
        "publish_after_remediation": False,
        "no_mutation_performed": True,
        "actionable": False,
        "safe_to_execute": False,
        "status": "draft_preview_available",
        "safe_to_preview": True,
        "refusal_reasons": [],
        "offer_id": "156719395011",
        "listing_id": "",
        "offer_status": "UNPUBLISHED",
        "category_id": "14056",
        "category_name": "Atlases",
        "condition_id": "3000",
        "inventory_condition_enum": "USED_GOOD",
        "live_policy_result": {
            "source": "live_readonly_metadata",
            "read_available": True,
            "live_policy_allows_condition": True,
            "allowed_condition_ids": ["1000", "3000"],
            "local_policy_status": "confirmed_by_live_readonly_metadata",
        },
        "stale_offer_reasoning": "Existing unpublished offer may need refresh.",
        "intended_inventory_item_payload_preview": inventory_payload,
        "intended_offer_payload_preview": offer_payload,
        "intended_call_sequence_preview": [
            {
                "order": 1,
                "method": "PUT",
                "endpoint": "/sell/inventory/v1/inventory_item/BK-000008",
                "preview_only": True,
                "mutation_performed": False,
            },
            {
                "order": 2,
                "method": "PUT",
                "endpoint": "/sell/inventory/v1/offer/156719395011",
                "preview_only": True,
                "mutation_performed": False,
            },
            {"order": 3, "method": "NONE", "endpoint": "", "preview_only": True, "mutation_performed": False},
        ],
    }
    return {
        "sku": "BK-000008",
        "found": True,
        "read_only": True,
        "no_mutation_performed": True,
        "live_readonly_requested": True,
        "live_readonly_performed": True,
        "live_readonly_methods_called": ["get_offer", "get_inventory_item", "get_item_condition_policies"],
        "live_readonly_errors": [],
        "local_status": "export_ready",
        "local_category_id": "14056",
        "local_category_name": "Atlases",
        "local_condition_id": "3000",
        "local_inventory_condition_enum": "USED_GOOD",
        "offer_id": "156719395011",
        "listing_id": "",
        "planned_action": "publish_existing_offer",
        "blocked_by_repair_queue": True,
        "retry_allowed": False,
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "existing_offer_diagnostics": {
            "read_available": True,
            "offer_id": "156719395011",
            "offer_exists": True,
            "status": "UNPUBLISHED",
            "category_id": "14056",
            "merchant_location_key": "real-location",
            "listing_policies": {
                "fulfillmentPolicyId": "287672421015",
                "paymentPolicyId": "287672342015",
                "returnPolicyId": "287672344015",
                "countryCode": "US",
            },
            "category_differs_from_local": False,
        },
        "inventory_item_diagnostics": {
            "read_available": True,
            "inventory_item_exists": True,
            "condition_enum": "USED_GOOD",
            "condition_differs_from_local": False,
        },
        "category_condition_policy_diagnostics": {
            "read_available": True,
            "live_policy_allows_condition": True,
            "live_metadata_supports_changing_condition": False,
        },
        "stale_offer_remediation_draft": draft,
    }


def _eligible_refresh_approval(diagnostics: dict) -> dict:
    draft = diagnostics["stale_offer_remediation_draft"]
    return {
        "sku": "BK-000008",
        "remediation_type": "refresh_existing_unpublished_offer",
        "repair_plan_id": draft["repair_plan_id"],
        "latest_publish_attempt_id": draft["latest_publish_attempt_id"],
        "offer_id": draft["offer_id"],
        "confirm_offer_status": "UNPUBLISHED",
        "confirm_listing_id_empty": True,
        "confirm_category_id": "14056",
        "confirm_condition_id": "3000",
        "confirm_inventory_condition_enum": "USED_GOOD",
        "confirm_publish_after_remediation": False,
        "operator_approved": True,
        "typed_confirmation": REQUIRED_TYPED_CONFIRMATION,
        "approved_payload_hash": build_remediation_payload_hash(draft),
    }


def _expected_live_refresh_offer_payload(diagnostics: dict) -> dict:
    payload = dict(diagnostics["stale_offer_remediation_draft"]["intended_offer_payload_preview"])
    payload["merchantLocationKey"] = "real-location"
    payload["listingPolicies"] = {
        "fulfillmentPolicyId": "287672421015",
        "paymentPolicyId": "287672342015",
        "returnPolicyId": "287672344015",
        "countryCode": "US",
    }
    return payload


class _FakeApprovedRefreshExecutor:
    def __init__(self) -> None:
        self.inventory_calls: list[tuple[str, dict]] = []
        self.offer_calls: list[tuple[str, dict]] = []
        self.publish_calls = 0
        self.create_calls = 0
        self.delete_calls = 0
        self.withdraw_calls = 0
        self.revise_calls = 0

    def put_inventory_item(self, sku: str, payload: dict) -> dict:
        self.inventory_calls.append((sku, payload))
        return {"ok": True, "method": "put_inventory_item"}

    def put_offer(self, offer_id: str, payload: dict) -> dict:
        self.offer_calls.append((offer_id, payload))
        return {"ok": True, "method": "put_offer"}

    def publish_offer(self, *_args, **_kwargs):  # pragma: no cover
        self.publish_calls += 1
        raise AssertionError("approved refresh must not publish")


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


def test_live_25021_on_locally_allowed_condition_marks_policy_suspect_and_blocks_retry(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="14056",
        condition_id="3000",
        condition_label="Pre-owned - Good",
        condition_notes="Cover creasing and possible discoloration/staining.",
        offer_id="156719395011",
    )

    def fake_publish_fail(_self, _item):
        return Result.failure(
            "eBay API error 400: publish_offer failed",
            error_code="API_ERROR",
            body="Error 25021: invalid item condition information. The provided condition id is invalid for the selected primary category id.",
            stage="publish_offer",
            offer_id="156719395011",
            category_id="14056",
            local_condition_id="3000",
            inventory_condition_enum="USED_GOOD",
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish_fail)

    with _client() as client:
        publish_resp = client.post("/api/ebay/publish/BK-000008")
        draft_resp = client.post("/api/ebay/repair-queue/BK-000008/draft-fix")
        recheck_resp = client.post("/api/ebay/repair-queue/BK-000008/recheck-readiness")
        detail_resp = client.get("/api/ebay/repair-queue/BK-000008")

    assert publish_resp.status_code == 500
    publish_detail = publish_resp.json()["detail"]
    assert publish_detail["stage"] == "publish_offer"
    condition_diagnostics = publish_detail["condition_diagnostics"]
    assert condition_diagnostics["local_condition_id"] == "3000"
    assert condition_diagnostics["inventory_condition_enum"] == "USED_GOOD"
    assert condition_diagnostics["category_id"] == "14056"
    assert condition_diagnostics["offer_id"] == "156719395011"
    assert condition_diagnostics["stage"] == "publish_offer"
    assert condition_diagnostics["existing_offer_id_detected"] is True
    assert condition_diagnostics["planned_action"] == "publish_existing_offer"
    assert condition_diagnostics["stale_existing_offer_hypothesis"] is True
    assert "stale category or condition state" in condition_diagnostics["stale_existing_offer_note"]
    assert "25021" in " ".join(publish_detail["raw_ebay_errors"])

    assert draft_resp.status_code == 200
    draft_body = draft_resp.json()
    assert draft_body["status"] == "draft_fix_available"
    assert draft_body["drafts"]
    draft = draft_body["drafts"][0]
    assert draft["classified_error_code"] == "invalid_category_condition"
    assert draft["retry_allowed"] is False
    assert draft["expected_value"]["local_policy_status"] == "suspect_or_stale"
    assert draft["expected_value"]["local_policy_allowed_condition_ids"] == ["1000", "1500", "3000", "4000"]
    assert not draft["suggested_value"]["allowed_options"]
    assert draft["suggested_value"]["rejected_by_live_validation"]["condition_id"] == "3000"
    assert draft["suggested_value"]["rejected_by_live_validation"]["inventory_condition_enum"] == "USED_GOOD"
    assert any("fetch live item-condition policy metadata" in action.lower() for action in draft["suggested_actions"])
    assert any("review whether the selected category is wrong" in action.lower() for action in draft["suggested_actions"])

    assert recheck_resp.status_code == 200
    assert recheck_resp.json()["ready_to_retry"] is False

    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    assert detail_body["ready_to_retry"] is False
    assert detail_body["latest_publish_attempt"]["retry_allowed"] is False
    plan = detail_body["repair_plans"][0]
    assert plan["expected_value"]["local_policy_status"] == "suspect_or_stale"
    assert plan["current_value"]["inventory_condition_enum"] == "USED_GOOD"
    assert plan["current_value"]["existing_offer_id_detected"] is True
    assert plan["current_value"]["planned_action"] == "publish_existing_offer"
    assert plan["current_value"]["stale_existing_offer_hypothesis"] is True


def test_publish_route_blocks_latest_needs_manual_review_before_mutation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000")
    plan_id = _seed_blocking_repair_plan()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("publish_item should not be called when repair queue blocks retry")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_if_called)

    with _client() as client:
        resp = client.post("/api/ebay/publish/BK-000008")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "blocked_by_repair_queue"
    assert detail["blocked_by_repair_queue"] is True
    assert detail["repair_plan_id"] == plan_id
    assert detail["latest_publish_attempt_id"] == "attempt-blocked"
    assert detail["retry_allowed"] is False
    assert detail["classified_error_code"] == "invalid_category_condition"


def test_batch_publish_skips_repair_blocked_sku_before_mutation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000005", ebay_category_id="29223", condition_id="5000")
    _seed_item(sku="BK-000008", ebay_category_id="14056", condition_id="3000")
    plan_id = _seed_blocking_repair_plan("BK-000008")
    published_skus = []

    def fake_publish(_self, item):
        published_skus.append(item.sku)
        return Result.success(
            {
                "listing_id": f"listing-{item.sku}",
                "listing_url": f"https://www.ebay.com/itm/listing-{item.sku}",
                "offer_id": f"offer-{item.sku}",
                "photo_urls": item.image_paths or [],
            }
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish)

    with _client() as client:
        resp = client.post("/api/ebay/publish/batch", params={"skus": "BK-000005,BK-000008", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert published_skus == ["BK-000005"]
    assert body["published"] == 1
    assert body["skipped"] == 1
    assert body["skipped_skus"][0]["sku"] == "BK-000008"
    assert body["skipped_skus"][0]["code"] == "blocked_by_repair_queue"
    assert body["skipped_skus"][0]["repair_plan_id"] == plan_id


def test_publish_preview_marks_would_publish_false_when_repair_queue_blocks_retry(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="14056",
        condition_id="3000",
        condition_label="Pre-owned - Good",
        offer_id="156719395011",
    )
    plan_id = _seed_blocking_repair_plan()

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["readiness"]["ready"] is True
    assert body["would_publish"] is False
    assert body["blocked_by_repair_queue"] is True
    assert body["repair_plan_id"] == plan_id
    assert body["latest_publish_attempt_id"] == "attempt-blocked"
    assert body["retry_allowed"] is False
    assert body["classified_error_code"] == "invalid_category_condition"
    assert body["condition_id"] == "3000"
    assert body["inventory_condition_enum"] == "USED_GOOD"
    assert body["category_id"] == "14056"
    assert body["offer_id"] == "156719395011"
    assert body["existing_offer_id_detected"] is True
    assert body["stale_existing_offer_hypothesis"] is True
    assert "stale category or condition state" in body["existing_offer_stale_state_diagnostic"]
    assert body["repair_queue_blocker"]["condition_diagnostics"]["planned_action"] == "publish_existing_offer"
    assert body["repair_queue_blocker"]["condition_diagnostics"]["failed_stage"] == "publish_offer"
    assert body["policy_conflict"] is True


def test_publish_diagnostics_for_blocked_existing_offer_is_local_only(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="14056",
        ebay_category_name="Atlases",
        condition_id="3000",
        offer_id="156719395011",
        listing_id=None,
        status=ItemStatus.EXPORT_READY,
    )
    plan_id = _seed_blocking_repair_plan()

    def fail_external_call(*_args, **_kwargs):
        raise AssertionError("publish diagnostics must not call eBay when live_readonly is false")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.get", fail_external_call)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.put", fail_external_call)
    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.delete", fail_external_call)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.get", fail_external_call)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.put", fail_external_call)
    monkeypatch.setattr("packages.ebay.src.inventory_client.ebay_http.post", fail_external_call)
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("diagnostics should not instantiate eBay client without live_readonly")),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics")

    assert resp.status_code == 200
    body = resp.json()
    assert body["read_only"] is True
    assert body["live_readonly_requested"] is False
    assert body["live_readonly_performed"] is False
    assert body["sku"] == "BK-000008"
    assert body["local_category_id"] == "14056"
    assert body["local_category_name"] == "Atlases"
    assert body["local_condition_id"] == "3000"
    assert body["local_inventory_condition_enum"] == "USED_GOOD"
    assert body["offer_id"] == "156719395011"
    assert body["listing_id"] == ""
    assert body["planned_action"] == "publish_existing_offer"
    assert body["existing_offer_id_detected"] is True
    assert body["repair_plan_id"] == plan_id
    assert body["latest_publish_attempt_id"] == "attempt-blocked"
    assert body["repair_status"]["status"] == "needs_manual_review"
    assert body["retry_allowed"] is False
    assert body["classified_error_code"] == "invalid_category_condition"
    assert body["blocked_by_repair_queue"] is True
    assert body["stale_existing_offer_hypothesis"] is True
    assert body["category_policy_hypothesis"] is True
    assert "Do not retry publish" in body["recommended_next_action"]

    offer_diag = body["existing_offer_diagnostics"]
    assert offer_diag["source"] == "local_only"
    assert offer_diag["read_available"] is False
    assert offer_diag["live_readonly_performed"] is False
    assert offer_diag["local_system_thinks_existing_offer"] is True
    assert offer_diag["existing_offer_publish_flow"]["updates_inventory_item_before_publish"] is True
    assert offer_diag["existing_offer_publish_flow"]["updates_existing_offer_before_publish"] is False
    assert offer_diag["existing_offer_publish_flow"]["publishes_existing_offer_id_directly"] is True
    assert offer_diag["stale_existing_offer_hypothesis"] is True

    policy_diag = body["category_condition_policy_diagnostics"]
    assert policy_diag["source"] == "builtin"
    assert policy_diag["local_policy_allows_condition"] is True
    assert policy_diag["local_policy_status"] == "suspect_or_stale"
    assert policy_diag["policy_conflict"] is True
    assert policy_diag["contradicted_by"] == "ebay_error"
    assert policy_diag["rejected_condition_id"] == "3000"
    assert policy_diag["rejected_category_id"] == "14056"


def test_publish_diagnostics_live_readonly_reads_offer_inventory_and_policy(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="14056",
        condition_id="3000",
        offer_id="156719395011",
        listing_id=None,
    )
    _seed_blocking_repair_plan()
    calls: list[str] = []

    def fake_offer(_self, offer_id):
        calls.append("get_offer")
        assert offer_id == "156719395011"
        return Result.success(
            {
                "offerId": offer_id,
                "status": "UNPUBLISHED",
                "categoryId": "14056",
                "conditionId": "5000",
                "marketplaceId": "EBAY_US",
                "listingPolicies": {"fulfillmentPolicyId": "fulfillment-1"},
            }
        )

    def fake_inventory(_self, sku):
        calls.append("get_inventory_item")
        assert sku == "BK-000008"
        return Result.success(
            {
                "sku": sku,
                "condition": "USED_GOOD",
                "conditionDescription": "Cover creasing.",
                "product": {
                    "title": "Atlas",
                    "imageUrls": ["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
                },
            }
        )

    def fake_policy(_self, category_id):
        calls.append("get_item_condition_policies")
        assert category_id == "14056"
        return Result.success(
            {
                "itemConditionPolicies": [
                    {
                        "categoryId": "14056",
                        "itemConditions": [
                            {"conditionId": "1000", "conditionDescription": "New"},
                            {"conditionId": "3000", "conditionDescription": "Used"},
                        ],
                    }
                ]
            }
        )

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("publish diagnostics must not call mutation methods")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_offer", fake_offer)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item", fake_inventory)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies", fake_policy)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail_mutation)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["live_readonly_requested"] is True
    assert body["live_readonly_performed"] is True
    assert body["no_mutation_performed"] is True
    assert body["auth_readonly_available"] is True
    assert body["token_source_used"] == "env"
    assert body["live_readonly_auth"]["refresh_allowed"] is False
    assert body["live_readonly_auth"]["no_token_refresh_performed"] is True
    assert body["live_readonly_methods_called"] == [
        "get_offer",
        "get_inventory_item",
        "get_item_condition_policies",
    ]
    assert calls == body["live_readonly_methods_called"]
    assert body["live_readonly_errors"] == []

    offer = body["existing_offer_diagnostics"]
    assert offer["source"] == "live_readonly"
    assert offer["read_available"] is True
    assert offer["offer_exists"] is True
    assert offer["status"] == "UNPUBLISHED"
    assert offer["category_id"] == "14056"
    assert offer["condition_id"] == "5000"
    assert offer["condition_differs_from_local"] is True
    assert offer["stale_existing_offer_supported_by_live_read"] is True

    inventory = body["inventory_item_diagnostics"]
    assert inventory["source"] == "live_readonly"
    assert inventory["read_available"] is True
    assert inventory["condition_enum"] == "USED_GOOD"
    assert inventory["condition_differs_from_local"] is False
    assert inventory["image_urls_are_public_hosted"] is True

    policy = body["category_condition_policy_diagnostics"]
    assert policy["source"] == "live_readonly_metadata"
    assert policy["read_available"] is True
    assert policy["live_policy_allows_condition"] is True
    assert policy["local_policy_status"] == "confirmed_by_live_readonly_metadata"
    assert policy["allowed_condition_ids"] == ["1000", "3000"]

    draft = body["stale_offer_remediation_draft"]
    assert draft["status"] == "draft_preview_available"
    assert draft["remediation_type"] == "refresh_existing_unpublished_offer"
    assert draft["live_execution_enabled"] is False
    assert draft["operator_approval_required"] is True
    assert draft["publish_after_remediation"] is False
    assert draft["no_mutation_performed"] is True
    assert draft["actionable"] is False
    assert draft["safe_to_execute"] is False
    assert draft["offer_id"] == "156719395011"
    assert draft["offer_status"] == "UNPUBLISHED"
    assert draft["listing_id"] == ""
    assert draft["category_id"] == "14056"
    assert draft["condition_id"] == "3000"
    assert draft["inventory_condition_enum"] == "USED_GOOD"
    assert draft["repair_plan_id"] == body["repair_plan_id"]
    assert draft["live_policy_result"]["live_policy_allows_condition"] is True
    assert draft["intended_inventory_item_payload_preview"]["condition"] == "USED_GOOD"
    assert draft["intended_offer_payload_preview"]["categoryId"] == "14056"
    assert draft["intended_call_sequence_preview"][0]["method"] == "PUT"
    assert draft["intended_call_sequence_preview"][1]["endpoint"].endswith("/offer/156719395011")
    assert draft["intended_call_sequence_preview"][2]["method"] == "NONE"


def test_publish_diagnostics_live_readonly_surfaces_inventory_diff_and_policy_rejection(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="156719395011")
    _seed_blocking_repair_plan()

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_offer",
        lambda *_args, **_kwargs: Result.success({"offerId": "156719395011", "categoryId": "14056"}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item",
        lambda *_args, **_kwargs: Result.success({"sku": "BK-000008", "condition": "LIKE_NEW", "product": {"imageUrls": []}}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies",
        lambda *_args, **_kwargs: Result.success(
            {
                "itemConditionPolicies": [
                    {
                        "itemConditions": [
                            {"conditionId": "1000", "conditionDescription": "New"},
                            {"conditionId": "4000", "conditionDescription": "Very Good"},
                        ]
                    }
                ]
            }
        ),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["inventory_item_diagnostics"]["condition_enum"] == "LIKE_NEW"
    assert body["inventory_item_diagnostics"]["condition_differs_from_local"] is True
    policy = body["category_condition_policy_diagnostics"]
    assert policy["live_policy_allows_condition"] is False
    assert policy["local_policy_status"] == "suspect_or_stale"
    assert policy["rejected_condition_id"] == "3000"
    assert body["category_policy_hypothesis"] is True
    refusal_codes = {reason["code"] for reason in body["stale_offer_remediation_draft"]["refusal_reasons"]}
    assert "live_policy_does_not_allow_condition" in refusal_codes
    assert "inventory_condition_differs_from_local" in refusal_codes


def test_stale_offer_remediation_refuses_when_offer_status_not_unpublished(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="156719395011")
    _seed_blocking_repair_plan()

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_offer",
        lambda *_args, **_kwargs: Result.success({"offerId": "156719395011", "status": "PUBLISHED", "categoryId": "14056"}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item",
        lambda *_args, **_kwargs: Result.success({"sku": "BK-000008", "condition": "USED_GOOD", "product": {"imageUrls": []}}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies",
        lambda *_args, **_kwargs: Result.success({"itemConditions": [{"conditionId": "3000"}]}),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    draft = resp.json()["stale_offer_remediation_draft"]
    assert draft["status"] == "refused"
    assert draft["safe_to_preview"] is False
    assert "offer_status_not_unpublished" in {reason["code"] for reason in draft["refusal_reasons"]}


def test_stale_offer_remediation_refuses_when_listing_id_present(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="156719395011", listing_id="987654321012")
    _seed_blocking_repair_plan()

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_offer",
        lambda *_args, **_kwargs: Result.success({"offerId": "156719395011", "status": "UNPUBLISHED", "categoryId": "14056"}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item",
        lambda *_args, **_kwargs: Result.success({"sku": "BK-000008", "condition": "USED_GOOD", "product": {"imageUrls": []}}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies",
        lambda *_args, **_kwargs: Result.success({"itemConditions": [{"conditionId": "3000"}]}),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    draft = resp.json()["stale_offer_remediation_draft"]
    refusal_codes = {reason["code"] for reason in draft["refusal_reasons"]}
    assert draft["status"] == "refused"
    assert "listing_id_present" in refusal_codes
    assert "not_existing_offer_publish_flow" in refusal_codes


def test_stale_offer_remediation_refuses_when_offer_id_missing(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="")
    _seed_blocking_repair_plan()

    def fail_offer_read(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("offer read should be skipped when no offer_id exists")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_offer", fail_offer_read)
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item",
        lambda *_args, **_kwargs: Result.success({"sku": "BK-000008", "condition": "USED_GOOD", "product": {"imageUrls": []}}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies",
        lambda *_args, **_kwargs: Result.success({"itemConditions": [{"conditionId": "3000"}]}),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    draft = resp.json()["stale_offer_remediation_draft"]
    refusal_codes = {reason["code"] for reason in draft["refusal_reasons"]}
    assert draft["status"] == "refused"
    assert "missing_offer_id" in refusal_codes
    assert "not_existing_offer_publish_flow" in refusal_codes


def test_stale_offer_remediation_refuses_when_repair_queue_not_blocking(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="156719395011")

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_offer",
        lambda *_args, **_kwargs: Result.success({"offerId": "156719395011", "status": "UNPUBLISHED", "categoryId": "14056"}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item",
        lambda *_args, **_kwargs: Result.success({"sku": "BK-000008", "condition": "USED_GOOD", "product": {"imageUrls": []}}),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies",
        lambda *_args, **_kwargs: Result.success({"itemConditions": [{"conditionId": "3000"}]}),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    draft = resp.json()["stale_offer_remediation_draft"]
    refusal_codes = {reason["code"] for reason in draft["refusal_reasons"]}
    assert draft["status"] == "refused"
    assert "repair_queue_not_blocking" in refusal_codes
    assert "missing_latest_repair_plan" in refusal_codes


def _eligible_approval_preview_diagnostics() -> dict:
    inventory_payload = {
        "condition": "USED_GOOD",
        "product": {
            "title": "Rand McNally Atlas",
            "imageUrls": ["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
        },
    }
    offer_payload = {
        "sku": "BK-000008",
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "categoryId": "14056",
    }
    draft = {
        "sku": "BK-000008",
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "remediation_type": "refresh_existing_unpublished_offer",
        "live_execution_enabled": False,
        "operator_approval_required": True,
        "publish_after_remediation": False,
        "no_mutation_performed": True,
        "actionable": False,
        "safe_to_execute": False,
        "status": "draft_preview_available",
        "safe_to_preview": True,
        "refusal_reasons": [],
        "offer_id": "156719395011",
        "listing_id": "",
        "offer_status": "UNPUBLISHED",
        "category_id": "14056",
        "category_name": "Atlases",
        "condition_id": "3000",
        "inventory_condition_enum": "USED_GOOD",
        "live_policy_result": {
            "source": "live_readonly_metadata",
            "read_available": True,
            "live_policy_allows_condition": True,
            "allowed_condition_ids": ["1000", "3000"],
            "local_policy_status": "confirmed_by_live_readonly_metadata",
        },
        "stale_offer_reasoning": "Existing unpublished offer may need refresh.",
        "intended_inventory_item_payload_preview": inventory_payload,
        "intended_offer_payload_preview": offer_payload,
        "intended_call_sequence_preview": [
            {"order": 1, "method": "PUT", "endpoint": "/sell/inventory/v1/inventory_item/BK-000008", "preview_only": True},
            {"order": 2, "method": "PUT", "endpoint": "/sell/inventory/v1/offer/156719395011", "preview_only": True},
            {"order": 3, "method": "NONE", "endpoint": "", "preview_only": True},
        ],
    }
    return {
        "sku": "BK-000008",
        "found": True,
        "read_only": True,
        "no_mutation_performed": True,
        "live_readonly_requested": False,
        "live_readonly_performed": False,
        "live_readonly_methods_called": [],
        "live_readonly_unavailable": [],
        "live_readonly_errors": [],
        "local_status": "export_ready",
        "local_category_id": "14056",
        "local_category_name": "Atlases",
        "local_condition_id": "3000",
        "local_inventory_condition_enum": "USED_GOOD",
        "offer_id": "156719395011",
        "listing_id": "",
        "planned_action": "publish_existing_offer",
        "existing_offer_id_detected": True,
        "repair_plan_id": "repair-plan-1",
        "latest_publish_attempt_id": "attempt-1",
        "repair_status": {"status": "needs_manual_review", "blocked_by_repair_queue": True},
        "retry_allowed": False,
        "classified_error_code": "invalid_category_condition",
        "blocked_by_repair_queue": True,
        "existing_offer_diagnostics": {"status": "UNPUBLISHED", "category_id": "14056"},
        "inventory_item_diagnostics": {"condition_enum": "USED_GOOD"},
        "category_condition_policy_diagnostics": {
            "live_policy_allows_condition": True,
            "live_metadata_supports_changing_condition": False,
        },
        "stale_offer_remediation_draft": draft,
    }


def test_stale_offer_remediation_approval_preview_returns_template_for_eligible_sku(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "BK-000008"
    assert body["eligible_for_approval_preview"] is True
    assert body["approval_required"] is True
    assert body["typed_confirmation_required"] == "REFRESH UNPUBLISHED OFFER ONLY"
    template = body["required_approval_fields_template"]
    assert template["sku"] == "BK-000008"
    assert template["remediation_type"] == "refresh_existing_unpublished_offer"
    assert template["repair_plan_id"] == "repair-plan-1"
    assert template["latest_publish_attempt_id"] == "attempt-1"
    assert template["offer_id"] == "156719395011"
    assert template["confirm_offer_status"] == "UNPUBLISHED"
    assert template["confirm_listing_id_empty"] is True
    assert template["confirm_category_id"] == "14056"
    assert template["confirm_condition_id"] == "3000"
    assert template["confirm_inventory_condition_enum"] == "USED_GOOD"
    assert template["confirm_publish_after_remediation"] is False
    assert template["operator_approved"] is True
    assert template["typed_confirmation"] == "REFRESH UNPUBLISHED OFFER ONLY"


def test_stale_offer_remediation_approval_preview_is_read_only(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    def fail_mutation(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("approval preview must not mutate eBay")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail_mutation)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    assert resp.json()["no_mutation_performed"] is True


def test_stale_offer_remediation_approval_preview_returns_payload_hash(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    expected_hash = build_remediation_payload_hash(diagnostics["stale_offer_remediation_draft"])
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["payload_hash"] == expected_hash
    assert body["required_approval_fields_template"]["approved_payload_hash"] == expected_hash


def test_stale_offer_remediation_approval_preview_requires_typed_confirmation_template(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: _eligible_approval_preview_diagnostics())

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["typed_confirmation_required"] == "REFRESH UNPUBLISHED OFFER ONLY"
    assert body["required_approval_fields_template"]["typed_confirmation"] == "REFRESH UNPUBLISHED OFFER ONLY"


def test_stale_offer_remediation_approval_preview_does_not_enable_live_execution(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: _eligible_approval_preview_diagnostics())

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["live_execution_enabled"] is False
    assert body["safe_to_execute_now"] is False
    assert body["publish_after_remediation"] is False


def test_stale_offer_remediation_approval_preview_does_not_publish_or_mutate(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: _eligible_approval_preview_diagnostics())

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["next_step_warning"] == "This preview does not publish, does not refresh eBay, and does not clear the repair queue."
    assert body["remediation_draft_summary"]["call_sequence_preview"][2]["method"] == "NONE"


def test_stale_offer_remediation_approval_preview_blocks_missing_offer_id(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    diagnostics["offer_id"] = ""
    diagnostics["stale_offer_remediation_draft"]["offer_id"] = ""
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible_for_approval_preview"] is False
    assert "missing_offer_id" in {reason["code"] for reason in body["blockers"]}


def test_stale_offer_remediation_approval_preview_blocks_listing_id_present(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    diagnostics["listing_id"] = "987654321012"
    diagnostics["stale_offer_remediation_draft"]["listing_id"] = "987654321012"
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    assert "listing_id_present" in {reason["code"] for reason in resp.json()["blockers"]}


def test_stale_offer_remediation_approval_preview_blocks_when_repair_queue_not_blocking(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    diagnostics["blocked_by_repair_queue"] = False
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    assert "repair_queue_not_blocking" in {reason["code"] for reason in resp.json()["blockers"]}


def test_stale_offer_remediation_approval_preview_surfaces_non_previewable_draft(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="BK-000008")
    diagnostics = _eligible_approval_preview_diagnostics()
    diagnostics["stale_offer_remediation_draft"]["status"] = "refused"
    diagnostics["stale_offer_remediation_draft"]["safe_to_preview"] = False
    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", lambda *_args, **_kwargs: diagnostics)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/stale-offer-remediation/approval-preview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible_for_approval_preview"] is False
    assert "draft_not_previewable" in {reason["code"] for reason in body["blockers"]}


def test_stale_offer_remediation_approval_preview_respects_route_guard(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000008")
    _seed_item(sku="BK-000009")

    with _client() as client:
        resp = client.get("/api/listings/BK-000009/stale-offer-remediation/approval-preview")

    assert resp.status_code == 403


def test_execute_approved_refresh_requires_route_guard_and_allowlist(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000008")
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)
    approval["sku"] = "BK-000009"

    def fail_diagnostics(*_args, **_kwargs):
        raise AssertionError("route guard should block before diagnostics")

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fail_diagnostics)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000009/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "live_execution_disabled"
    assert detail["no_publish_performed"] is True


def test_execute_approved_refresh_requires_dedicated_env_flag(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    monkeypatch.delenv("ALLOW_EBAY_STALE_OFFER_REFRESH", raising=False)
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)

    def fail_diagnostics(*_args, **_kwargs):
        raise AssertionError("dedicated env flag should block before live read-only diagnostics")

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fail_diagnostics)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "live_execution_disabled"
    assert detail["required_env_flag"] == "ALLOW_EBAY_STALE_OFFER_REFRESH=true"
    assert detail["no_publish_performed"] is True
    assert detail["repair_queue_cleared"] is False


def test_execute_approved_refresh_requires_exact_dedicated_env_flag_value(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    monkeypatch.setenv("ALLOW_EBAY_STALE_OFFER_REFRESH", "TRUE")
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)

    def fail_diagnostics(*_args, **_kwargs):
        raise AssertionError("non-exact dedicated env flag should block before diagnostics")

    def fail_executor():
        raise AssertionError("non-exact dedicated env flag should block before executor construction")

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fail_diagnostics)
    monkeypatch.setattr("apps.api.src.routes.listings._build_stale_offer_refresh_executor", fail_executor)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "live_execution_disabled"
    assert detail["required_env_flag"] == "ALLOW_EBAY_STALE_OFFER_REFRESH=true"
    assert detail["no_publish_performed"] is True
    assert detail["repair_queue_cleared"] is False


def test_execute_approved_refresh_blocks_when_live_remediation_disabled(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.delenv("ALLOW_LIVE_E2E", raising=False)
    monkeypatch.setenv("ALLOW_EBAY_STALE_OFFER_REFRESH", "true")
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)

    def fail_executor():
        raise AssertionError("ALLOW_LIVE_E2E should block before executor construction")

    monkeypatch.setattr("apps.api.src.routes.listings._build_stale_offer_refresh_executor", fail_executor)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "live_execution_disabled"
    assert detail["no_publish_performed"] is True
    assert detail["repair_queue_cleared"] is False


def test_execute_approved_refresh_requires_exact_typed_confirmation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    monkeypatch.setenv("ALLOW_EBAY_STALE_OFFER_REFRESH", "true")
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)
    approval["typed_confirmation"] = "REFRESH"

    def fail_diagnostics(*_args, **_kwargs):
        raise AssertionError("typed confirmation should block before diagnostics")

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fail_diagnostics)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["execution_status"] == "blocked"
    assert detail["refusal_reasons"][0]["code"] == "approval_typed_confirmation_mismatch"
    assert detail["no_publish_performed"] is True
    assert detail["repair_queue_cleared"] is False


def test_execute_approved_refresh_blocks_payload_hash_mismatch_without_refresh_calls(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    monkeypatch.setenv("ALLOW_EBAY_STALE_OFFER_REFRESH", "true")
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)
    approval["approved_payload_hash"] = "wrong-hash"
    executor = _FakeApprovedRefreshExecutor()

    def fake_diagnostics(_session, _sku, *, allow_live_readonly=False):
        assert allow_live_readonly is True
        return _eligible_refresh_diagnostics()

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fake_diagnostics)
    monkeypatch.setattr("apps.api.src.routes.listings._build_stale_offer_refresh_executor", lambda: executor)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["execution_status"] == "blocked"
    refusal_codes = {reason["code"] for reason in detail["refusal_reasons"]}
    assert "approval_payload_hash_mismatch" in refusal_codes
    assert "preflight_payload_hash_mismatch" in refusal_codes
    assert detail["no_publish_performed"] is True
    assert detail["repair_queue_cleared"] is False
    assert executor.inventory_calls == []
    assert executor.offer_calls == []
    assert executor.publish_calls == 0


def test_execute_approved_refresh_calls_inventory_put_then_offer_put_only(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    monkeypatch.setenv("ALLOW_EBAY_STALE_OFFER_REFRESH", "true")
    diagnostics = _eligible_refresh_diagnostics()
    approval = _eligible_refresh_approval(diagnostics)
    executor = _FakeApprovedRefreshExecutor()
    diagnostics_calls = []

    def fake_diagnostics(_session, sku, *, allow_live_readonly=False):
        diagnostics_calls.append((sku, allow_live_readonly))
        return _eligible_refresh_diagnostics()

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fake_diagnostics)
    monkeypatch.setattr("apps.api.src.routes.listings._build_stale_offer_refresh_executor", lambda: executor)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_status"] == "refresh_completed"
    assert body["calls_performed"] == ["put_inventory_item", "put_offer"]
    assert body["no_publish_performed"] is True
    assert body["repair_queue_cleared"] is False
    assert body["item_status_after"] == "export_ready"
    assert diagnostics_calls == [("BK-000008", True), ("BK-000008", True)]
    assert executor.inventory_calls == [
        ("BK-000008", diagnostics["stale_offer_remediation_draft"]["intended_inventory_item_payload_preview"])
    ]
    assert executor.offer_calls == [
        ("156719395011", _expected_live_refresh_offer_payload(diagnostics))
    ]
    assert executor.publish_calls == 0
    assert executor.create_calls == 0
    assert executor.delete_calls == 0
    assert executor.withdraw_calls == 0
    assert executor.revise_calls == 0


def test_execute_approved_refresh_blocks_if_offer_not_unpublished(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.setenv("ALLOW_LIVE_E2E", "true")
    monkeypatch.setenv("ALLOW_EBAY_STALE_OFFER_REFRESH", "true")
    approval = _eligible_refresh_approval(_eligible_refresh_diagnostics())
    executor = _FakeApprovedRefreshExecutor()

    def fake_diagnostics(_session, _sku, *, allow_live_readonly=False):
        diagnostics = _eligible_refresh_diagnostics()
        diagnostics["existing_offer_diagnostics"]["status"] = "PUBLISHED"
        diagnostics["stale_offer_remediation_draft"]["offer_status"] = "PUBLISHED"
        return diagnostics

    monkeypatch.setattr("apps.api.src.routes.listings.build_publish_diagnostics", fake_diagnostics)
    monkeypatch.setattr("apps.api.src.routes.listings._build_stale_offer_refresh_executor", lambda: executor)

    with _client() as client:
        resp = client.post(
            "/api/listings/BK-000008/stale-offer-remediation/execute-approved-refresh",
            json=approval,
        )

    assert resp.status_code == 409
    assert "offer_status_not_unpublished" in {r["code"] for r in resp.json()["detail"]["refusal_reasons"]}
    assert executor.inventory_calls == []
    assert executor.offer_calls == []


def test_publish_diagnostics_live_readonly_skips_reads_when_auth_unavailable(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="156719395011")
    _seed_blocking_repair_plan()

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_readonly_auth_diagnostics",
        lambda _self: {
            "auth_readonly_available": False,
            "token_source_used": "none",
            "token_present": False,
            "issue_code": "expired_or_invalid_access_token",
            "reason": "oauth_access_token_expired_refresh_not_allowed",
            "suggested_action": "Run the explicit eBay OAuth reconnect or refresh flow, then retry diagnostics.",
            "refresh_allowed": False,
            "no_token_refresh_performed": True,
        },
    )

    def fail_read(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("diagnostics must not call live read methods without readonly auth")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_offer", fail_read)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item", fail_read)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies", fail_read)

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["live_readonly_requested"] is True
    assert body["live_readonly_performed"] is False
    assert body["auth_readonly_available"] is False
    assert body["token_source_used"] == "none"
    assert body["live_readonly_auth"]["reason"] == "oauth_access_token_expired_refresh_not_allowed"
    assert body["live_readonly_methods_called"] == []
    assert body["live_readonly_unavailable"][0]["method"] == "all_live_readonly_methods"
    assert body["existing_offer_diagnostics"]["read_available"] is False
    assert body["inventory_item_diagnostics"]["read_available"] is False
    assert body["category_condition_policy_diagnostics"]["read_available"] is False


def test_publish_diagnostics_live_readonly_handles_read_errors(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000", offer_id="156719395011")
    _seed_blocking_repair_plan()

    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_offer",
        lambda *_args, **_kwargs: Result.failure("offer unavailable", error_code="API_ERROR"),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_inventory_item",
        lambda *_args, **_kwargs: Result.failure("inventory unavailable", error_code="API_ERROR"),
    )
    monkeypatch.setattr(
        "packages.ebay.src.inventory_client.EbayInventoryClient.get_item_condition_policies",
        lambda *_args, **_kwargs: Result.failure("policy unavailable", error_code="API_ERROR"),
    )

    with _client() as client:
        resp = client.get("/api/listings/BK-000008/publish-diagnostics?allow_live_readonly=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["live_readonly_performed"] is True
    assert len(body["live_readonly_errors"]) == 3
    assert body["existing_offer_diagnostics"]["read_available"] is False
    assert body["inventory_item_diagnostics"]["read_available"] is False
    assert body["category_condition_policy_diagnostics"]["read_available"] is False
    assert body["recommended_next_action"]


def test_newer_needs_manual_review_overrides_older_ready_to_retry(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(ebay_category_id="14056", condition_id="3000")
    old_plan_id = _seed_blocking_repair_plan(
        status="ready_to_retry",
        retry_allowed=True,
        requires_review=False,
        updated_at=datetime.utcnow() - timedelta(hours=1),
        publish_attempt_id="attempt-old",
    )
    new_plan_id = _seed_blocking_repair_plan(
        status="needs_manual_review",
        retry_allowed=False,
        requires_review=True,
        updated_at=datetime.utcnow(),
        publish_attempt_id="attempt-new",
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("publish_item should not be called when a newer plan blocks retry")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_if_called)

    with _client() as client:
        preview = client.get("/api/listings/BK-000008/publish-preview")
        publish = client.post("/api/ebay/publish/BK-000008")
        detail = client.get("/api/ebay/repair-queue/BK-000008")
        apply_old = client.post(
            "/api/ebay/repair-queue/BK-000008/apply-draft-fix",
            json={
                "sku": "BK-000008",
                "repair_plan_id": old_plan_id,
                "approved": True,
                "edited_value": {"condition_id": "3000"},
            },
        )

    assert preview.status_code == 200
    assert preview.json()["repair_plan_id"] == new_plan_id
    assert preview.json()["would_publish"] is False
    assert publish.status_code == 409
    assert publish.json()["detail"]["repair_plan_id"] == new_plan_id
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["repair_status"]["latest_blocking_plan_id"] == new_plan_id
    assert detail_body["repair_status"]["blocked_by_repair_queue"] is True
    plans_by_id = {plan["id"]: plan for plan in detail_body["repair_plans"]}
    assert plans_by_id[new_plan_id]["actionable"] is False
    assert plans_by_id[new_plan_id]["non_actionable_reason"] == "Latest repair plan blocks publish retry."
    assert plans_by_id[old_plan_id]["superseded"] is True
    assert plans_by_id[old_plan_id]["active"] is False
    assert plans_by_id[old_plan_id]["actionable"] is False
    assert plans_by_id[old_plan_id]["superseded_by_repair_plan_id"] == new_plan_id
    assert apply_old.status_code == 400
    assert "superseded by a newer blocking repair plan" in apply_old.json()["detail"]


def test_relist_blocks_repair_blocked_sku_before_publish_call(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        ebay_category_id="14056",
        condition_id="3000",
        status=ItemStatus.LISTED,
        listing_id="listing-1",
        offer_id="offer-1",
    )
    plan_id = _seed_blocking_repair_plan()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("relist should not call publish when repair queue blocks retry")

    monkeypatch.setattr("packages.sync.src.relister.AutoRelister.relist", fail_if_called)

    with _client() as client:
        resp = client.post("/api/sync/relist/BK-000008")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "blocked_by_repair_queue"
    assert detail["repair_plan_id"] == plan_id


def test_listings_push_blocks_repair_blocked_sku_before_ebay_mutation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        offer_id="156719395011",
        listing_id=None,
        status=ItemStatus.EXPORT_READY,
        title_final="Original title",
        list_price=22.0,
    )
    plan_id = _seed_blocking_repair_plan()

    def fail_put(*_args, **_kwargs):
        raise AssertionError("listings.push must not call eBay PUT for a repair-blocked SKU")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.put", fail_put)

    with _client() as client:
        resp = client.post(
            "/api/listings/push/BK-000008",
            json={"title": "Mutated title", "list_price": 99.99},
        )

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "blocked_by_repair_queue"
    assert detail["blocked_by_repair_queue"] is True
    assert detail["repair_plan_id"] == plan_id
    assert detail["retry_allowed"] is False
    assert detail["repair_status"]["status"] == "needs_manual_review"
    assert detail["classified_error_code"] == "invalid_category_condition"
    assert detail["reason"]
    assert detail["suggested_actions"]
    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-000008")
        assert item is not None
        assert item.title_final == "Original title"
        assert item.list_price == 22.0
        assert item.status == ItemStatus.EXPORT_READY
        assert item.listing_id is None


def test_listings_end_blocks_repair_blocked_unpublished_offer_before_withdraw(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        offer_id="156719395011",
        listing_id=None,
        status=ItemStatus.EXPORT_READY,
    )
    plan_id = _seed_blocking_repair_plan()

    def fail_delete(*_args, **_kwargs):
        raise AssertionError("listings.end must not withdraw a repair-blocked unpublished offer")

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.delete", fail_delete)

    with _client() as client:
        resp = client.delete("/api/listings/end/BK-000008")

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "blocked_by_repair_queue"
    assert detail["blocked_by_repair_queue"] is True
    assert detail["repair_plan_id"] == plan_id
    assert detail["retry_allowed"] is False
    assert detail["repair_status"]["status"] == "needs_manual_review"
    assert detail["classified_error_code"] == "invalid_category_condition"
    assert detail["reason"]
    assert detail["suggested_actions"]


def test_listings_end_allows_listed_item_with_resolved_historical_repair(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(
        sku="BK-000005",
        offer_id="O-BK-000005",
        listing_id="L-BK-000005",
        status=ItemStatus.LISTED,
    )
    _seed_blocking_repair_plan(
        sku="BK-000005",
        status="resolved",
        retry_allowed=False,
        requires_review=False,
        publish_attempt_id="attempt-resolved",
    )

    class _Resp:
        status_code = 204
        text = ""

    delete_calls: list[str] = []

    def fake_delete(url, *_args, **_kwargs):
        delete_calls.append(url)
        return _Resp()

    monkeypatch.setattr("apps.api.src.routes.listings.ebay_http.delete", fake_delete)

    with _client() as client:
        resp = client.delete("/api/listings/end/BK-000005")

    assert resp.status_code == 200
    assert delete_calls and delete_calls[0].endswith("/offer/O-BK-000005/withdraw")
    with Session(sqlite_db.engine) as session:
        item = ItemRepository(session).get_by_sku("BK-000005")
        assert item is not None
        assert item.status == ItemStatus.EXPORT_READY


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
