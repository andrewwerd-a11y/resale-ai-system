from __future__ import annotations

from sqlmodel import Session, create_engine

from apps.api.src.services.bulk_reintake_preview import build_bulk_reintake_preview
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
    skus = [result["sku"] for result in preview["per_sku_results"]]
    assert skus == ["BK-LISTED", "BK-REINTAKE"]
    first = next(result for result in preview["per_sku_results"] if result["sku"] == "BK-REINTAKE")
    assert first["intake_quality_status"]
    assert "needs_more_photos_for_analysis" in first
    assert "missing_photo_types" in first
    assert first["operator_photo_evidence"]["deep_analysis_image_selection_available"] is False
    assert first["workflow_lane"]
    assert first["primary_blocker_family"]
    assert first["publish_readiness_summary"]
    assert "Generated local report artifact" in preview["generated_artifact_warning"]
    assert (tmp_path / "reports").exists()
    assert preview["json_report_path"].endswith(".json")
    assert preview["markdown_report_path"].endswith(".md")


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
