from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.models.publish_attempt_record import PublishAttemptRecord
from packages.data.src.models.publish_repair_plan_record import PublishRepairPlanRecord
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "publish_all_script.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_export_ready_item(engine, sku: str = "BK-000008") -> None:
    with Session(engine) as session:
        ItemRepository(session).upsert(
            Item(
                sku=sku,
                status=ItemStatus.EXPORT_READY,
                title_raw="Repair raw title",
                title_final="Repair title",
                description_final="Repair description",
                list_price=20.0,
                category_key="books",
                ebay_category_id="14056",
                condition_id="3000",
                image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-000008-01.jpg"],
                item_specifics={},
            )
        )


def _seed_blocking_plan(engine, sku: str = "BK-000008") -> str:
    with Session(engine) as session:
        attempt = PublishAttemptRecord(
            id="attempt-blocked",
            sku=sku,
            stage="publish_offer",
            status="failed",
            ebay_error_id="25021",
            classified_error_code="invalid_category_condition",
            repair_layer="category_compatibility",
            requires_review=True,
            retry_allowed=False,
        )
        plan = PublishRepairPlanRecord(
            sku=sku,
            publish_attempt_id=attempt.id,
            status="needs_manual_review",
            affected_field="condition_id",
            current_value_json=json.dumps({"category_id": "14056", "condition_id": "3000"}),
            expected_value_json=json.dumps({"allowed_condition_ids": ["1000", "1500", "3000", "4000"]}),
            suggested_actions_json=json.dumps(["Review category/condition compatibility before retrying publish."]),
            risk_level="high",
            safe_to_auto_apply=False,
            requires_review=True,
            retry_allowed=False,
            source="ebay_error",
            repair_layer="category_compatibility",
            classified_error_code="invalid_category_condition",
            updated_at=datetime.utcnow(),
        )
        session.add(attempt)
        session.add(plan)
        session.commit()
        return plan.id


def test_publish_all_skips_repair_blocked_sku_before_publish_call(monkeypatch, tmp_path):
    engine = _configure_temp_db(monkeypatch, tmp_path)
    _seed_export_ready_item(engine)
    plan_id = _seed_blocking_plan(engine)

    import scripts.publish_all as publish_all_script

    monkeypatch.setattr(publish_all_script, "engine", engine)
    monkeypatch.setattr(publish_all_script, "init_db", lambda: SQLModel.metadata.create_all(engine))

    class FakeAuth:
        settings = type("Settings", (), {"ebay_environment": "sandbox"})()

        def is_configured(self):
            return True

    class FakeClient:
        auth = FakeAuth()

        def publish_item(self, _item):
            raise AssertionError("publish_all should not publish repair-blocked SKUs")

    monkeypatch.setattr(publish_all_script, "EbayInventoryClient", FakeClient)

    publish_all_script.publish_all(sku_filter="BK-000008")

    with Session(engine) as session:
        item = ItemRepository(session).get_by_sku("BK-000008")

    assert item is not None
    assert item.status == ItemStatus.EXPORT_READY
    message = publish_all_script.format_repair_blocker_for_console(
        {
            "repair_plan_id": plan_id,
            "retry_allowed": False,
            "repair_status": {"status": "needs_manual_review"},
            "classified_error_code": "invalid_category_condition",
            "reason": "Latest repair plan requires manual review before publish can be retried.",
        }
    )
    assert "blocked_by_repair_queue" in message
    assert plan_id in message
