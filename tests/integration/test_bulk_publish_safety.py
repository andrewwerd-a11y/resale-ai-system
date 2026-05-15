from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from apps.api.src.routes import ebay
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.core.src.result import Result
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.publish_attempt_record import PublishAttemptRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "bulk_publish_safety.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv(
        "APPROVED_E2E_SKUS",
        ",".join(
            [
                "SKU-CLEAN",
                "SKU-REPAIR",
                "SKU-PHOTO-MISSING",
                "SKU-LOCAL-ONLY-PHOTOS",
                "SKU-INVALID-CONDITION",
                "SKU-MISSING-ASPECTS",
                "SKU-ALREADY-LISTED",
                "SKU-STALE-OFFER",
                "SKU-AUTH-BLOCKED",
                "SKU-BAD-CONDITION-LIST",
                "SKU-BAD-CONDITION-TEXT",
                "SKU-BAD-CONDITION-BLANK",
                "SKU-VALID-CONDITION",
                "SKU-BAD-ASPECT",
                "SKU-MISSING-CONDITION-DATA",
                "SKU-EXISTING-OFFER",
            ]
        ),
    )
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_SANDBOX_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_SANDBOX_CERT_ID", "cert-id")
    monkeypatch.setenv("EBAY_SANDBOX_USER_TOKEN", "user-token")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")

    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _seed_item(**overrides) -> None:
    base = dict(
        sku="SKU-CLEAN",
        status=ItemStatus.EXPORT_READY,
        title_raw="Bulk raw title",
        title_final="Bulk title",
        description_final="Bulk description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="29223",
        condition_id="5000",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/SKU-CLEAN-01.jpg"],
        item_specifics={},
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _seed_repair_plan(
    sku: str,
    *,
    classified_error_code: str = "requires_publish_decision_after_refresh",
    repair_layer: str = "post_refresh_publish_decision",
    status: str = "needs_manual_review",
    retry_allowed: bool = False,
) -> str:
    with Session(sqlite_db.engine) as session:
        attempt = PublishAttemptRecord(
            id=f"attempt-{sku}",
            sku=sku,
            stage="publish_offer",
            status="failed",
            classified_error_code=classified_error_code,
            repair_layer=repair_layer,
            requires_review=True,
            retry_allowed=retry_allowed,
        )
        session.add(attempt)
        plan = PublishRepairPlanRecord(
            sku=sku,
            publish_attempt_id=attempt.id,
            status=status,
            affected_field="publish_readiness",
            current_value_json=json.dumps({"sku": sku}),
            expected_value_json="{}",
            suggested_value_json="{}",
            suggested_actions_json=json.dumps(["Review the active repair plan before publishing."]),
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=True,
            retry_allowed=retry_allowed,
            source="test",
            repair_layer=repair_layer,
            classified_error_code=classified_error_code,
            updated_at=datetime.utcnow(),
        )
        session.add(plan)
        session.commit()
        return plan.id


def _seed_mixed_batch(tmp_path) -> dict[str, str]:
    local_photo = tmp_path / "SKU-LOCAL-ONLY-PHOTOS.jpg"
    local_photo.write_bytes(b"local-photo")
    _seed_item(sku="SKU-CLEAN")
    _seed_item(sku="SKU-REPAIR")
    repair_plan_id = _seed_repair_plan(
        "SKU-REPAIR",
        classified_error_code="blocked_by_repair_queue",
        repair_layer="preflight_repair_queue",
    )
    _seed_item(sku="SKU-PHOTO-MISSING", image_paths=[])
    _seed_item(sku="SKU-LOCAL-ONLY-PHOTOS", image_paths=[str(local_photo)])
    _seed_item(sku="SKU-INVALID-CONDITION", ebay_category_id="14056", condition_id="5000")
    _seed_item(sku="SKU-MISSING-ASPECTS", missing_required_fields=["Brand"], ebay_category_id="999999")
    _seed_item(
        sku="SKU-ALREADY-LISTED",
        status=ItemStatus.LISTED,
        listing_id="listing-existing",
        offer_id="offer-existing",
    )
    _seed_item(
        sku="SKU-STALE-OFFER",
        ebay_category_id="14056",
        condition_id="3000",
        offer_id="offer-stale",
        listing_id=None,
    )
    stale_plan_id = _seed_repair_plan("SKU-STALE-OFFER")
    return {"SKU-REPAIR": repair_plan_id, "SKU-STALE-OFFER": stale_plan_id}


def _preview_batch(client: TestClient, *, skus: list[str] | None = None, statuses: list[str] | None = None, persist_report: bool = True):
    return client.post(
        "/api/ebay/publish/batch-preview",
        json={
            "skus": skus or [],
            "statuses": statuses or [],
            "persist_report": persist_report,
        },
    )


def test_bulk_publish_classifies_mixed_skus_before_mutation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    plan_ids = _seed_mixed_batch(tmp_path)
    attempted: list[str] = []

    def fake_publish(_self, item):
        attempted.append(item.sku)
        return Result.success(
            {
                "listing_id": f"listing-{item.sku}",
                "listing_url": f"https://www.ebay.com/itm/listing-{item.sku}",
                "offer_id": f"offer-{item.sku}",
                "photo_urls": item.image_paths or [],
            }
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish)

    skus = ",".join(
        [
            "SKU-CLEAN",
            "SKU-REPAIR",
            "SKU-PHOTO-MISSING",
            "SKU-LOCAL-ONLY-PHOTOS",
            "SKU-INVALID-CONDITION",
            "SKU-MISSING-ASPECTS",
            "SKU-ALREADY-LISTED",
            "SKU-STALE-OFFER",
        ]
    )
    with _client() as client:
        resp = client.post("/api/ebay/publish/batch", params={"skus": skus, "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert attempted == ["SKU-CLEAN"]
    assert body["summary"] == {
        "total": 8,
        "attempted_count": 1,
        "published_count": 1,
        "skipped_count": 7,
        "failed_count": 0,
    }
    assert body["attempted"] == [{"sku": "SKU-CLEAN", "stage": "publish_item", "planned_action": "create_offer_then_publish"}]
    assert body["published_items"][0]["sku"] == "SKU-CLEAN"

    skipped = {entry["sku"]: entry for entry in body["skipped_items"]}
    assert skipped["SKU-REPAIR"]["reason_code"] == "blocked_by_repair_queue"
    assert skipped["SKU-REPAIR"]["repair_plan_id"] == plan_ids["SKU-REPAIR"]
    assert skipped["SKU-REPAIR"]["retry_allowed"] is False
    assert skipped["SKU-REPAIR"]["requires_review"] is True
    assert skipped["SKU-PHOTO-MISSING"]["reason_code"] == "photo_hosting_required"
    assert "No photos are attached to this item." in skipped["SKU-PHOTO-MISSING"]["details"]["blockers"]
    assert skipped["SKU-LOCAL-ONLY-PHOTOS"]["reason_code"] == "photo_hosting_required"
    assert any("Hosted public image URLs are missing" in blocker for blocker in skipped["SKU-LOCAL-ONLY-PHOTOS"]["details"]["blockers"])
    assert skipped["SKU-INVALID-CONDITION"]["reason_code"] == "invalid_category_condition"
    assert any("Condition ID '5000' is not allowed" in blocker for blocker in skipped["SKU-INVALID-CONDITION"]["details"]["blockers"])
    assert skipped["SKU-MISSING-ASPECTS"]["reason_code"] == "missing_required_aspects"
    assert skipped["SKU-ALREADY-LISTED"]["reason_code"] == "already_listed"
    assert skipped["SKU-STALE-OFFER"]["reason_code"] == "blocked_by_repair_queue"
    assert skipped["SKU-STALE-OFFER"]["classified_error"] == "requires_publish_decision_after_refresh"
    assert skipped["SKU-STALE-OFFER"]["repair_plan_id"] == plan_ids["SKU-STALE-OFFER"]

    with Session(sqlite_db.engine) as session:
        repair_plans = session.exec(select(PublishRepairPlanRecord)).all()
    active_by_sku = {plan.sku: plan for plan in repair_plans if plan.status != "resolved"}
    assert active_by_sku["SKU-REPAIR"].status == "needs_manual_review"
    assert active_by_sku["SKU-STALE-OFFER"].status == "needs_manual_review"


def test_bulk_publish_auth_failure_skips_all_without_publish_call(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="SKU-CLEAN")
    _seed_item(sku="SKU-AUTH-BLOCKED")

    def fail_publish(*_args, **_kwargs):
        raise AssertionError("batch publish must not call publish_item when auth readiness blocks")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_publish)
    auth_block = {
        "code": "expired_or_invalid_access_token",
        "message": "No usable eBay access token is configured.",
        "next_action": "Reconnect eBay OAuth before publishing.",
        "blockers": ["No usable eBay access token is configured."],
    }
    monkeypatch.setattr("apps.api.src.routes.ebay.get_ebay_auth_readiness", lambda: auth_block)
    monkeypatch.setattr("apps.api.src.services.bulk_publish_preview.get_ebay_auth_readiness", lambda: auth_block)

    with _client() as client:
        resp = client.post(
            "/api/ebay/publish/batch",
            params={"skus": "SKU-CLEAN,SKU-AUTH-BLOCKED", "e2e_only": "true"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["attempted_count"] == 0
    assert body["summary"]["skipped_count"] == 2
    assert {entry["reason_code"] for entry in body["skipped_items"]} == {"expired_or_invalid_access_token"}


def test_bulk_publish_preview_is_read_only_and_returns_structured_operator_decisions(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    plan_ids = _seed_mixed_batch(tmp_path)

    def fail_publish(*_args, **_kwargs):
        raise AssertionError("batch publish preview must not call publish_item")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_publish)

    with _client() as client:
        resp = _preview_batch(
            client,
            skus=[
                "SKU-CLEAN",
                "SKU-REPAIR",
                "SKU-PHOTO-MISSING",
                "SKU-LOCAL-ONLY-PHOTOS",
                "SKU-INVALID-CONDITION",
                "SKU-MISSING-ASPECTS",
                "SKU-ALREADY-LISTED",
                "SKU-STALE-OFFER",
            ],
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["no_mutation_performed"] is True
    assert body["no_ebay_mutation_performed"] is True
    assert body["summary"] == {
        "total": 8,
        "would_publish_count": 1,
        "skip_count": 2,
        "repair_count": 2,
        "review_count": 2,
        "already_listed_count": 1,
        "auth_blocked_count": 0,
        "missing_photo_count": 2,
        "stale_offer_count": 1,
        "invalid_category_condition_count": 1,
        "invalid_condition_id_format_count": 0,
        "reason_counts": {
            "already_listed": 1,
            "invalid_category_condition": 1,
            "missing_required_aspects": 1,
            "photo_hosting_required": 2,
            "requires_publish_decision_after_refresh": 1,
            "blocked_by_repair_queue": 1,
            "would_publish": 1,
        },
        "photo_hosting_states": {
            "hosted_public": 6,
            "local_only": 1,
            "missing": 1,
        },
    }

    decisions = {entry["sku"]: entry for entry in body["decisions"]}
    assert decisions["SKU-CLEAN"]["decision"] == "WOULD_PUBLISH"
    assert decisions["SKU-CLEAN"]["effective_publish_ready"] is True
    assert decisions["SKU-CLEAN"]["primary_reason_code"] == "would_publish"
    assert decisions["SKU-CLEAN"]["secondary_blockers"] == []
    assert decisions["SKU-CLEAN"]["condition_id_valid"] is True
    assert decisions["SKU-CLEAN"]["category_policy_known"] is True
    assert decisions["SKU-REPAIR"]["decision"] == "REPAIR"
    assert decisions["SKU-REPAIR"]["repair_plan_id"] == plan_ids["SKU-REPAIR"]
    assert decisions["SKU-REPAIR"]["classified_error_code"] == "blocked_by_repair_queue"
    assert decisions["SKU-REPAIR"]["blocked_by_repair_queue"] is True
    assert decisions["SKU-PHOTO-MISSING"]["decision"] == "SKIP"
    assert decisions["SKU-PHOTO-MISSING"]["photo_hosting_state"] == "missing"
    assert decisions["SKU-PHOTO-MISSING"]["primary_reason_code"] == "photo_hosting_required"
    assert [entry["group"] for entry in decisions["SKU-PHOTO-MISSING"]["next_action_sequence"]] == [
        "Host photos",
        "Add missing photos / run image review",
    ]
    assert decisions["SKU-LOCAL-ONLY-PHOTOS"]["decision"] == "SKIP"
    assert decisions["SKU-LOCAL-ONLY-PHOTOS"]["photo_hosting_state"] == "local_only"
    assert decisions["SKU-INVALID-CONDITION"]["decision"] == "REPAIR"
    assert decisions["SKU-MISSING-ASPECTS"]["decision"] == "REVIEW"
    assert decisions["SKU-ALREADY-LISTED"]["decision"] == "ALREADY_LISTED"
    assert decisions["SKU-STALE-OFFER"]["decision"] == "REVIEW"
    assert decisions["SKU-STALE-OFFER"]["repair_plan_id"] == plan_ids["SKU-STALE-OFFER"]
    assert decisions["SKU-STALE-OFFER"]["classified_error_code"] == "requires_publish_decision_after_refresh"
    assert any(entry["code"] == "existing_unpublished_offer" for entry in decisions["SKU-STALE-OFFER"]["secondary_blockers"])
    assert decisions["SKU-CLEAN"]["next_best_action_group"] == "Would publish"
    assert body["grouped_next_best_actions"]["Would publish"] == ["SKU-CLEAN"]
    for sku, decision in decisions.items():
        if decision["decision"] != "WOULD_PUBLISH":
            assert decision["next_action"], sku
    report_path = Path(body["persisted_report_path"])
    assert report_path.exists()
    assert report_path.name.startswith("bulk_publish_preview_")
    report_text = report_path.read_text(encoding="utf-8")
    assert "Bulk Publish Preview" in report_text
    assert "## Next Best Action Plan" in report_text
    assert "Next Best Action Group" in report_text
    assert "Next Action Sequence" in report_text


def test_bulk_publish_preview_and_publish_batch_share_same_safety_gate(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_mixed_batch(tmp_path)
    attempted: list[str] = []

    def fake_publish(_self, item):
        attempted.append(item.sku)
        return Result.success(
            {
                "listing_id": f"listing-{item.sku}",
                "listing_url": f"https://www.ebay.com/itm/listing-{item.sku}",
                "offer_id": f"offer-{item.sku}",
                "photo_urls": item.image_paths or [],
            }
        )

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fake_publish)

    skus = [
        "SKU-CLEAN",
        "SKU-REPAIR",
        "SKU-PHOTO-MISSING",
        "SKU-LOCAL-ONLY-PHOTOS",
        "SKU-INVALID-CONDITION",
        "SKU-MISSING-ASPECTS",
        "SKU-ALREADY-LISTED",
        "SKU-STALE-OFFER",
    ]
    with _client() as client:
        preview_resp = _preview_batch(client, skus=skus, persist_report=False)
        publish_resp = client.post("/api/ebay/publish/batch", params={"skus": ",".join(skus), "e2e_only": "true"})

    assert preview_resp.status_code == 200
    assert publish_resp.status_code == 200
    preview_body = preview_resp.json()
    publish_body = publish_resp.json()
    preview_would_publish = sorted(
        entry["sku"]
        for entry in preview_body["decisions"]
        if entry["decision"] == "WOULD_PUBLISH"
    )
    attempted_skus = sorted(entry["sku"] for entry in publish_body["attempted"])
    assert attempted == preview_would_publish
    assert attempted_skus == preview_would_publish == ["SKU-CLEAN"]


def test_bulk_publish_preview_accepts_status_filters_without_mutation(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    _seed_item(sku="SKU-CLEAN", status=ItemStatus.EXPORT_READY)
    _seed_item(sku="SKU-ALREADY-LISTED", status=ItemStatus.LISTED, listing_id="listing-1")

    def fail_publish(*_args, **_kwargs):
        raise AssertionError("batch publish preview must not call publish_item")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_publish)

    with _client() as client:
        resp = _preview_batch(client, statuses=["export_ready", "listed"], persist_report=False)

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total"] == 2
    decisions = {entry["sku"]: entry for entry in body["decisions"]}
    assert decisions["SKU-CLEAN"]["decision"] == "WOULD_PUBLISH"
    assert decisions["SKU-ALREADY-LISTED"]["decision"] == "ALREADY_LISTED"


def test_bulk_publish_preview_requires_explicit_skus_when_route_guard_enabled(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_item(sku="SKU-CLEAN", status=ItemStatus.EXPORT_READY)

    with _client() as client:
        resp = _preview_batch(client, statuses=["export_ready"], persist_report=False)

    assert resp.status_code == 403
    assert "Explicit SKUs are required for batch publish preview" in resp.json()["detail"]


def test_bulk_publish_preview_surfaces_malformed_condition_ids_ahead_of_photo_hosting(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    local_photo = tmp_path / "local-photo.jpg"
    local_photo.write_bytes(b"photo")
    _seed_item(
        sku="SKU-BAD-CONDITION-LIST",
        ebay_category_id="14056",
        condition_id="['1000', '3000']",
        image_paths=[str(local_photo)],
    )
    _seed_item(
        sku="SKU-BAD-CONDITION-TEXT",
        ebay_category_id="14056",
        condition_id="1000=New, New without tags",
        image_paths=[str(local_photo)],
    )
    _seed_item(
        sku="SKU-BAD-CONDITION-BLANK",
        ebay_category_id="14056",
        condition_id="",
        image_paths=[str(local_photo)],
    )
    _seed_item(
        sku="SKU-VALID-CONDITION",
        ebay_category_id="14056",
        condition_id="3000",
        image_paths=[str(local_photo)],
    )

    with _client() as client:
        resp = _preview_batch(
            client,
            skus=[
                "SKU-BAD-CONDITION-LIST",
                "SKU-BAD-CONDITION-TEXT",
                "SKU-BAD-CONDITION-BLANK",
                "SKU-VALID-CONDITION",
            ],
        )

    assert resp.status_code == 200
    body = resp.json()
    decisions = {entry["sku"]: entry for entry in body["decisions"]}
    for sku in ["SKU-BAD-CONDITION-LIST", "SKU-BAD-CONDITION-TEXT", "SKU-BAD-CONDITION-BLANK"]:
        decision = decisions[sku]
        assert decision["decision"] == "REVIEW"
        assert decision["reason_code"] == "invalid_condition_id_format"
        assert decision["primary_reason_code"] == "invalid_condition_id_format"
        assert decision["next_best_action_group"] == "Fix invalid condition ID"
        assert decision["condition_id_valid"] is False
        assert decision["inventory_condition_enum"] == ""
        assert decision["next_action"] == "Normalize condition_id to a valid eBay numeric condition ID before publishing."
        assert any(entry["code"] == "photo_hosting_required" for entry in decision["secondary_blockers"])

    valid = decisions["SKU-VALID-CONDITION"]
    assert valid["reason_code"] == "photo_hosting_required"
    assert valid["primary_reason_code"] == "photo_hosting_required"
    assert valid["next_best_action_group"] == "Host photos"
    assert valid["condition_id_valid"] is True
    assert valid["inventory_condition_enum"] == "USED_EXCELLENT"
    assert body["summary"]["invalid_condition_id_format_count"] == 3
    assert body["grouped_actionable_next_steps"]["Fix invalid condition ID"] == [
        "SKU-BAD-CONDITION-BLANK",
        "SKU-BAD-CONDITION-LIST",
        "SKU-BAD-CONDITION-TEXT",
    ]
    assert body["grouped_actionable_next_steps"]["Host photos"] == [
        "SKU-BAD-CONDITION-BLANK",
        "SKU-BAD-CONDITION-LIST",
        "SKU-BAD-CONDITION-TEXT",
        "SKU-VALID-CONDITION",
    ]
    assert body["grouped_next_best_actions"]["Fix invalid condition ID"] == [
        "SKU-BAD-CONDITION-BLANK",
        "SKU-BAD-CONDITION-LIST",
        "SKU-BAD-CONDITION-TEXT",
    ]
    assert "Manual high-value/authenticity review" not in body["grouped_actionable_next_steps"]


def test_bulk_publish_preview_groups_invalid_aspects_and_missing_condition_data(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    local_photo = tmp_path / "local-photo.jpg"
    local_photo.write_bytes(b"photo")
    _seed_item(
        sku="SKU-BAD-ASPECT",
        ebay_category_id="29223",
        condition_id="3000",
        status=ItemStatus.EXPORT_READY,
        color="X" * 70,
        image_paths=[str(local_photo)],
    )
    _seed_item(
        sku="SKU-MISSING-CONDITION-DATA",
        ebay_category_id="14056",
        condition_id="",
        status=ItemStatus.EXPORT_READY,
        image_paths=[str(local_photo)],
        missing_required_fields=["Condition"],
    )

    with _client() as client:
        resp = _preview_batch(
            client,
            skus=["SKU-BAD-ASPECT", "SKU-MISSING-CONDITION-DATA"],
            persist_report=False,
        )

    assert resp.status_code == 200
    body = resp.json()
    decisions = {entry["sku"]: entry for entry in body["decisions"]}

    assert decisions["SKU-BAD-ASPECT"]["decision"] == "REVIEW"
    assert decisions["SKU-BAD-ASPECT"]["primary_reason_code"] == "invalid_aspect_value"
    assert decisions["SKU-BAD-ASPECT"]["next_best_action_group"] == "Fix invalid aspects"
    assert any(entry["code"] == "photo_hosting_required" for entry in decisions["SKU-BAD-ASPECT"]["secondary_blockers"])

    assert decisions["SKU-MISSING-CONDITION-DATA"]["primary_reason_code"] == "invalid_condition_id_format"
    assert decisions["SKU-MISSING-CONDITION-DATA"]["next_best_action_group"] == "Fix invalid condition ID"
    assert "Missing required condition data" in body["grouped_actionable_next_steps"]
    assert body["grouped_actionable_next_steps"]["Missing required condition data"] == ["SKU-MISSING-CONDITION-DATA"]
    assert body["grouped_actionable_next_steps"]["Fix invalid aspects"] == ["SKU-BAD-ASPECT"]
    assert body["grouped_next_best_actions"]["Fix invalid aspects"] == ["SKU-BAD-ASPECT"]


def test_bulk_publish_preview_surfaces_existing_unpublished_offer_state(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    local_photo = tmp_path / "local-photo.jpg"
    local_photo.write_bytes(b"photo")
    _seed_item(
        sku="SKU-EXISTING-OFFER",
        ebay_category_id="14056",
        condition_id="3000",
        status=ItemStatus.EXPORT_READY,
        offer_id="offer-existing",
        listing_id="",
        image_paths=[str(local_photo)],
    )

    with _client() as client:
        resp = _preview_batch(client, skus=["SKU-EXISTING-OFFER"], persist_report=False)

    assert resp.status_code == 200
    body = resp.json()
    decision = body["decisions"][0]
    assert decision["planned_action"] == "publish_existing_offer"
    assert decision["primary_reason_code"] == "photo_hosting_required"
    assert any(entry["code"] == "existing_unpublished_offer" for entry in decision["secondary_blockers"])
    assert [entry["group"] for entry in decision["next_action_sequence"]] == [
        "Resolve existing-offer / repair queue blockers",
        "Host photos",
    ]
    assert "Resolve existing-offer / repair queue blockers" in body["grouped_actionable_next_steps"]
    assert body["grouped_next_best_actions"]["Resolve existing-offer / repair queue blockers"] == ["SKU-EXISTING-OFFER"]
