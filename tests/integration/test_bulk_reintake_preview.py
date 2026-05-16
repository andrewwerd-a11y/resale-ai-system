from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import items
from apps.api.src.services.bulk_reintake_preview import build_bulk_reintake_preview
from apps.api.src.services.photo_metadata import upsert_photo_labels
from packages.core.src import config as core_config
from packages.core.src.constants import ItemStatus
from packages.data.src.db import sqlite as sqlite_db
from packages.data.src.repositories.item_repo import ItemRepository
from packages.domain.src.entities.item import Item


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "bulk_reintake_preview.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("EBAY_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("EBAY_FULFILLMENT_POLICY_ID", "fulfillment-1")
    monkeypatch.setenv("EBAY_PAYMENT_POLICY_ID", "payment-1")
    monkeypatch.setenv("EBAY_RETURN_POLICY_ID", "return-1")
    monkeypatch.setenv("INTAKE_EXTERNAL_PROVIDER_ENABLED", "false")
    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    return TestClient(app)


def _seed_item(**overrides) -> None:
    base = dict(
        sku="BK-REINTAKE",
        status=ItemStatus.EXPORT_READY,
        title_raw="Preview raw title",
        title_final="Preview title",
        description_final="Preview description",
        list_price=20.0,
        category_key="books",
        ebay_category_id="14056",
        ebay_category_name="Atlases",
        condition_id="3000",
        image_paths=["https://res.cloudinary.com/demo/image/upload/v1/BK-REINTAKE-01.jpg"],
        item_specifics={},
    )
    base.update(overrides)
    with Session(sqlite_db.engine) as session:
        ItemRepository(session).upsert(Item(**base))


def _block_mutations(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("bulk reintake preview must not call mutation methods")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_inventory_item", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_offer", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload", fail)
    monkeypatch.setattr("packages.ebay.src.inventory_client.PhotoUploader.upload_all", fail)


def test_bulk_reintake_preview_enumerates_status_skus_safely(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    monkeypatch.setattr(
        "apps.api.src.services.intake_pipeline.run_deep_analysis_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not run by default")),
    )
    _seed_item()
    _seed_item(sku="BK-LISTED", status=ItemStatus.LISTED, listing_id="1234567890")
    _seed_item(sku="BK-SOLD", status=ItemStatus.SOLD)

    with Session(sqlite_db.engine) as session:
        preview = build_bulk_reintake_preview(
            session,
            report_dir=tmp_path / "reports",
        )

    assert preview["read_only"] is True
    assert preview["draft_only"] is True
    assert preview["no_publish_performed"] is True
    assert preview["no_ebay_mutation_performed"] is True
    assert preview["no_external_provider_called"] is True
    assert preview["summary"]["total_skus"] == 2
    assert preview["summary"]["by_local_status"] == {"listed": 1, "export_ready": 1}
    assert "by_intake_quality_status" in preview["summary"]
    assert "by_workflow_lane" in preview["summary"]
    assert "by_primary_blocker_family" in preview["summary"]
    skus = [result["sku"] for result in preview["per_sku_results"]]
    assert skus == ["BK-LISTED", "BK-REINTAKE"]
    first = next(result for result in preview["per_sku_results"] if result["sku"] == "BK-REINTAKE")
    assert first["intake_quality_status"]
    assert "needs_more_photos_for_analysis" in first
    assert "missing_photo_types" in first
    assert "missing_required_photo_types" in first
    assert "missing_recommended_photo_types" in first
    assert "can_generate_limited_evidence_draft" in first
    assert "limited_evidence_allowed_for_draft_only" in first
    assert "publish_still_blocked" in first
    assert first["operator_photo_evidence"]["deep_analysis_image_selection_available"] is False
    assert first["workflow_lane"]
    assert first["primary_blocker_family"]
    assert first["publish_readiness_summary"]
    assert "Generated local report artifact" in preview["generated_artifact_warning"]
    assert (tmp_path / "reports").exists()
    assert preview["json_report_path"].endswith(".json")
    assert preview["markdown_report_path"].endswith(".md")
    assert "## Executive Summary" in preview["report_markdown"]
    assert "## Lane Counts" in preview["report_markdown"]
    assert "## SKU Tables By Lane" in preview["report_markdown"]
    assert "Do not publish automatically" in preview["report_markdown"]
    assert "Optional: generate limited-evidence draft for review only; publish remains blocked." in preview["report_markdown"]


def test_bulk_reintake_preview_handles_empty_status_selection(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)

    with Session(sqlite_db.engine) as session:
        preview = build_bulk_reintake_preview(
            session,
            statuses=[],
            report_dir=tmp_path / "reports",
        )

    assert preview["summary"]["total_skus"] == 0
    assert preview["per_sku_results"] == []
    assert preview["no_publish_performed"] is True
    assert "Do not publish automatically" in preview["report_markdown"]


def test_bulk_reintake_preview_handles_missing_explicit_sku(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)

    with Session(sqlite_db.engine) as session:
        preview = build_bulk_reintake_preview(
            session,
            skus=["missing-sku"],
            statuses=[],
            report_dir=tmp_path / "reports",
        )

    result = preview["per_sku_results"][0]
    assert result["sku"] == "MISSING-SKU"
    assert result["found"] is False
    assert result["workflow_lane"] == "unknown_manual_review"
    assert result["primary_blocker_family"] == "missing_local_item"
    assert "missing_local_item" in result["blockers"]
    assert preview["summary"]["missing"] == 1


def test_bulk_reintake_preview_rollups_and_markdown_are_lane_safe(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(sku="BK-LISTED", status=ItemStatus.LISTED, listing_id="1234567890")
    _seed_item(sku="BK-LIVE", status=ItemStatus.EXPORT_READY)
    _seed_item(sku="BK-IMAGE", status=ItemStatus.EXPORT_READY)

    def fake_batch(_session, skus, *, allow_live_readonly=False):
        results = []
        for sku in skus:
            if sku == "BK-LISTED":
                results.append({
                    "sku": sku,
                    "found": True,
                    "ready_for_publish_preview": False,
                    "workflow_lane": "already_listed_or_sync_review",
                    "workflow_hint": "already listed / sync review",
                    "primary_blocker_family": "sync_review",
                    "blocker_codes": [],
                    "recommended_next_action": "Treat this as a listed/sync-review item.",
                })
            elif sku == "BK-LIVE":
                results.append({
                    "sku": sku,
                    "found": True,
                    "ready_for_publish_preview": False,
                    "workflow_lane": "live_state_remediation_required",
                    "workflow_hint": "live state remediation required",
                    "primary_blocker_family": "condition",
                    "blocker_codes": ["stale_live_inventory_condition_suspected", "local_image_path_only"],
                    "recommended_next_action": "Do not publish. Review live-state mismatch before image hosting.",
                })
            else:
                results.append({
                    "sku": sku,
                    "found": True,
                    "ready_for_publish_preview": False,
                    "workflow_lane": "image_hosting_candidate",
                    "workflow_hint": "image hosting needed before publish preview",
                    "primary_blocker_family": "images",
                    "blocker_codes": ["local_image_path_only"],
                    "recommended_next_action": "Host item images to public URLs before publish preview or publish.",
                })
        return {"per_sku_results": results}

    monkeypatch.setattr(
        "apps.api.src.services.bulk_reintake_preview.build_publish_debug_diagnostics_batch",
        fake_batch,
    )

    with Session(sqlite_db.engine) as session:
        preview = build_bulk_reintake_preview(
            session,
            skus=["BK-LISTED", "BK-LIVE", "BK-IMAGE"],
            statuses=[],
            report_dir=tmp_path / "reports",
        )

    summary = preview["summary"]
    assert summary["already_listed_or_sync_review_count"] == 1
    assert summary["live_state_remediation_required_count"] == 1
    assert summary["image_hosting_candidate_count"] == 1
    assert summary["top_blocker_codes"]["local_image_path_only"] == 2
    assert summary["top_blocker_codes"]["stale_live_inventory_condition_suspected"] == 1
    assert "BK-LIVE" in summary["highest_risk_skus_to_defer"]
    markdown = preview["report_markdown"]
    assert "Review sync/revise/listed-state consistency; do not treat as fresh publish candidates." in markdown
    assert "Do not use normal publish flow; investigate stale offer/live inventory/category/condition mismatch." in markdown
    assert "Do not publish. Review live-state mismatch before image hosting." in markdown
    assert "Do not publish automatically" in markdown
    assert "already_listed_or_sync_review" in markdown
    assert "live_state_remediation_required" in markdown


def test_bulk_reintake_preview_api_returns_read_only_summary_for_explicit_skus(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    monkeypatch.setattr(
        "apps.api.src.services.intake_pipeline.run_deep_analysis_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider should not run by default")),
    )
    _seed_item(sku="BK-API", status=ItemStatus.EXPORT_READY)

    with _client() as client:
        resp = client.post(
            "/api/items/bulk-reintake-preview",
            json={"skus": ["BK-API"], "statuses": [], "persist_report": False},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["read_only"] is True
    assert body["draft_only"] is True
    assert body["no_publish_performed"] is True
    assert body["no_ebay_mutation_performed"] is True
    assert body["manual_approval_required"] is True
    assert body["no_external_provider_called"] is True
    assert body["summary"]["total_skus"] == 1
    assert body["per_sku_results"][0]["sku"] == "BK-API"
    assert "json_report_path" not in body
    assert "markdown_report_path" not in body


def test_bulk_reintake_preview_api_accepts_status_filters(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(sku="BK-API-READY", status=ItemStatus.EXPORT_READY)
    _seed_item(sku="BK-API-LISTED", status=ItemStatus.LISTED, listing_id="1234567890")

    with _client() as client:
        resp = client.post(
            "/api/items/bulk-reintake-preview",
            json={"statuses": ["listed"], "persist_report": False},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_statuses"] == ["listed"]
    assert body["summary"]["total_skus"] == 1
    assert body["per_sku_results"][0]["sku"] == "BK-API-LISTED"
    assert body["per_sku_results"][0]["workflow_lane"] == "already_listed_or_sync_review"


def test_bulk_reintake_preview_includes_photo_metadata_rollups(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _block_mutations(monkeypatch)
    _seed_item(
        sku="BK-LABELS",
        image_paths=["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"],
    )
    _seed_item(
        sku="BK-NO-LABELS",
        image_paths=["front-cover.jpg", "spine.jpg"],
    )

    with Session(sqlite_db.engine) as session:
        labeled_item = ItemRepository(session).get_by_sku("BK-LABELS")
        assert labeled_item is not None
        upsert_photo_labels(
            session,
            labeled_item,
            [
                {"image_path": "a.jpg", "photo_type": "front"},
                {"image_path": "b.jpg", "photo_type": "back"},
                {"image_path": "c.jpg", "photo_type": "spine"},
            ],
        )
        preview = build_bulk_reintake_preview(
            session,
            skus=["BK-LABELS", "BK-NO-LABELS"],
            statuses=[],
            report_dir=tmp_path / "reports",
        )

    labeled = next(result for result in preview["per_sku_results"] if result["sku"] == "BK-LABELS")
    no_labels = next(result for result in preview["per_sku_results"] if result["sku"] == "BK-NO-LABELS")
    assert labeled["labeled_photo_count"] >= 3
    assert labeled["user_labeled_photo_types"] == ["back", "front", "spine"]
    assert labeled["photo_metadata_status"] == "partial_labels"
    assert no_labels["photo_metadata_status"] == "no_labels"
    summary = preview["summary"]
    assert summary["skus_with_no_labeled_photos_count"] == 1
    assert summary["skus_with_partial_labels_count"] == 1
    assert "BK-LABELS" in summary["skus_improved_by_labels"]
    assert "BK-NO-LABELS" in summary["skus_recommended_for_labeling_before_reanalysis"]
    markdown = preview["report_markdown"]
    assert "## Label Coverage" in markdown
    assert "## Label Before Reanalysis" in markdown
    assert "Label photos before reanalysis." in markdown
    assert "photo_metadata_status=partial_labels" in markdown
    assert "missing_required_photo_types=" in markdown
    assert "missing_recommended_photo_types=" in markdown
