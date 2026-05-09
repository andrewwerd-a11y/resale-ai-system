from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from apps.api.src.routes import diagnostics
from apps.api.src.services.operation_diagnostics import record_failure, record_success
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(diagnostics.router, prefix="/api/diagnostics", tags=["diagnostics"])
    return TestClient(app)


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "diagnostic_reports.db"
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


def _seed_report_events():
    with Session(sqlite_db.engine) as session:
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/BK-000008",
            sku="BK-000008",
            session_id="session-1",
            safe_message="eBay publish failed.",
            external_service="ebay",
            stage="publish_offer",
            error_family="invalid_category_condition",
            error_code="25021",
            ebay_mutation_attempted=True,
            ebay_mutation_succeeded=False,
            raw_error_payload={"errors": [{"errorId": 25021, "message": "invalid condition"}]},
            recommended_next_action="Refresh stale live inventory state before retrying publish.",
            related_files_services=["apps/api/src/routes/ebay.py"],
        )
        record_failure(
            session,
            operation_name="ebay_publish",
            route="/api/ebay/publish/BK-000009",
            sku="BK-000009",
            session_id="session-1",
            safe_message="eBay publish failed.",
            external_service="ebay",
            stage="publish_offer",
            error_family="invalid_category_condition",
            error_code="25021",
            ebay_mutation_attempted=True,
            ebay_mutation_succeeded=False,
            raw_error_payload={"errors": [{"errorId": 25021, "message": "invalid condition"}]},
            recommended_next_action="Refresh stale live inventory state before retrying publish.",
            related_files_services=["apps/api/src/routes/ebay.py"],
        )
        record_failure(
            session,
            operation_name="photo_hosting",
            route="/api/items/BK-000010/photos/host",
            sku="BK-000010",
            session_id="session-2",
            safe_message="Photo hosting failed.",
            external_service="cloudinary",
            error_family="photo_hosting",
            error_code="CLOUDINARY_ERROR",
            mutation_attempted=True,
            mutation_succeeded=False,
            raw_error_payload={
                "Authorization": "Bearer top-secret-token",
                "refresh_token": "refresh-secret",
                "message": "access_token=abc123 4111111111111111",
            },
            recommended_next_action="Verify local image paths and cloudinary configuration.",
            related_files_services=["apps/api/src/routes/items.py"],
        )
        record_success(
            session,
            operation_name="publish_preview",
            route="/api/listings/publish-preview/BK-000008",
            sku="BK-000008",
            session_id="session-1",
            safe_message="Publish preview generated.",
            external_service="local",
            result_context={"preview_ready": True},
        )


def test_weekly_report_groups_repeated_failures_and_marks_critical(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_report_events()
    monkeypatch.setattr(
        "apps.api.src.services.diagnostic_reports._run_git",
        lambda args: {
            "rev-parse HEAD": "abc123",
            "rev-parse --abbrev-ref HEAD": "master",
            "log -1 --pretty=%s": "Add operation diagnostics event ledger",
            "status --porcelain": " M apps/api/src/routes/diagnostics.py",
        }[args],
    )

    with _client() as client:
        resp = client.get("/api/diagnostics/reports/weekly")

    assert resp.status_code == 200
    body = resp.json()
    report = body["report"]
    assert body["read_only"] is True
    assert body["no_mutation_performed"] is True
    assert body["no_ebay_mutation_performed"] is True
    assert body["no_external_send"] is True
    assert report["report_type"] == "weekly_report"
    assert report["summary_counts"]["events_total"] == 4
    assert report["severity_breakdown"]["critical"] >= 2
    assert report["top_error_families"][0]["error_family"] == "invalid_category_condition"
    assert "BK-000008" in report["affected_skus"]
    assert "BK-000009" in report["affected_skus"]
    assert report["repeated_failures"]
    assert report["repeated_failures"][0]["group_key"] == "ebay_publish|invalid_category_condition|25021|publish_offer"
    assert report["repeated_failures"][0]["sku_count"] == 2
    assert report["repeated_failures"][0]["severity"] == "critical"
    assert report["git_context"]["git_available"] is True
    assert report["git_context"]["current_commit_hash"] == "abc123"
    assert report["git_context"]["branch"] == "master"
    assert report["git_context"]["dirty_working_tree"] is True


def test_report_falls_back_cleanly_when_git_unavailable(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_report_events()
    monkeypatch.setattr(
        "apps.api.src.services.diagnostic_reports._run_git",
        lambda _args: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )

    with _client() as client:
        resp = client.get("/api/diagnostics/reports/session/session-1")

    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["report_type"] == "session_report"
    assert report["git_context"]["git_available"] is False
    assert report["git_context"]["current_commit_hash"] == ""
    assert report["git_context"]["branch"] == ""


def test_report_does_not_expose_secrets(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_report_events()

    with _client() as client:
        resp = client.get("/api/diagnostics/reports/sku/BK-000010")

    assert resp.status_code == 200
    report = resp.json()["report"]
    serialized = str(report).lower()
    assert "top-secret-token" not in serialized
    assert "refresh-secret" not in serialized
    assert "abc123" not in serialized
    assert "4111111111111111" not in serialized
    assert "[redacted]" in serialized
    assert report["no_external_send"] is True
    assert report["redaction_notice"]


def test_generate_report_persists_locally_and_recent_lists_it(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_report_events()
    monkeypatch.setattr(
        "apps.api.src.services.diagnostic_reports._run_git",
        lambda args: {
            "rev-parse HEAD": "be817af",
            "rev-parse --abbrev-ref HEAD": "master",
            "log -1 --pretty=%s": "Add operation diagnostics event ledger",
            "status --porcelain": "",
        }[args],
    )

    with _client() as client:
        generated = client.post(
            "/api/diagnostics/reports/generate",
            json={"report_type": "root_cause_analysis_package", "days": 7, "persist": True},
        )
        recent = client.get("/api/diagnostics/reports/recent")

    assert generated.status_code == 200
    generated_body = generated.json()
    report = generated_body["report"]
    assert generated_body["read_only"] is False
    assert generated_body["local_persistence_only"] is True
    assert generated_body["no_mutation_performed"] is True
    assert generated_body["no_ebay_mutation_performed"] is True
    assert generated_body["no_external_send"] is True
    assert report["report_type"] == "root_cause_analysis_package"
    assert report["persisted_files"]
    assert any(path.endswith(".json") for path in report["persisted_files"])
    assert any(path.endswith(".md") for path in report["persisted_files"])
    assert report["copyable_codex_prompt"]
    assert report["report_markdown"]

    assert recent.status_code == 200
    recent_body = recent.json()
    assert recent_body["read_only"] is True
    assert recent_body["reports"]
    assert recent_body["reports"][0]["report_type"] == "root_cause_analysis_package"


def test_report_routes_are_non_mutating(monkeypatch, tmp_path):
    _configure_temp_db(monkeypatch, tmp_path)
    _seed_report_events()

    def fail_mutation(*_args, **_kwargs):
        raise AssertionError("diagnostic report routes must not mutate external services")

    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.publish_item", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_inventory_item", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient.put_offer", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._put", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.inventory_client.EbayInventoryClient._post", fail_mutation)
    monkeypatch.setattr("packages.ebay.src.photo_uploader.PhotoUploader.upload", fail_mutation)

    with _client() as client:
        weekly = client.get("/api/diagnostics/reports/weekly")
        recent = client.get("/api/diagnostics/reports/recent")
        generated = client.post(
            "/api/diagnostics/reports/generate",
            json={"report_type": "critical_error_report", "persist": False},
        )

    assert weekly.status_code == 200
    assert recent.status_code == 200
    assert generated.status_code == 200
    assert weekly.json()["no_mutation_performed"] is True
    assert recent.json()["no_ebay_mutation_performed"] is True
    assert generated.json()["local_persistence_only"] is True
