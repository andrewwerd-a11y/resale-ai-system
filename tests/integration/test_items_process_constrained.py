from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import create_engine

from apps.api.src.routes import items
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from apps.worker.src import main as worker_main


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    return TestClient(app)


def _write_jpg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color=(220, 30, 30)).save(path, format="JPEG")


def _item_count(db_path: Path, sku: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM items WHERE sku = ?", (sku,)).fetchone()[0]
    finally:
        conn.close()


def _all_item_skus(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT sku FROM items ORDER BY sku").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _configure_temp_runtime(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    intake_root = tmp_path / "intake"
    db_path = tmp_path / "test_app.db"

    monkeypatch.setenv("INTAKE_ROOT", str(intake_root))
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")

    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    temp_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", temp_engine)
    monkeypatch.setattr(worker_main, "engine", temp_engine)

    return intake_root, db_path


def test_constrained_intake_processes_only_requested_approved_sku(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    intake_root, db_path = _configure_temp_runtime(monkeypatch, tmp_path)

    _write_jpg(intake_root / "pending" / "BK-000005" / "01.jpg")

    with _make_client() as client:
        resp = client.post(
            "/api/items/process",
            params={"skus": "BK-000005", "e2e_only": "true"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "BK-000005" in body["requested_skus"]
    assert "BK-000005" in body["found_skus"]
    assert "BK-000005" not in body["missing_skus"]
    assert body["processed_count"] >= 1
    assert _all_item_skus(db_path) == ["BK-000005"]


def test_constrained_intake_rejects_non_approved_sku_under_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    _configure_temp_runtime(monkeypatch, tmp_path)

    called = {"value": False}

    def fail_if_called(_skus):  # pragma: no cover
        called["value"] = True
        raise AssertionError("Worker must not run for non-approved SKU")

    monkeypatch.setattr(worker_main, "run_worker_for_skus", fail_if_called)

    with _make_client() as client:
        resp = client.post(
            "/api/items/process",
            params={"skus": "BK-999999", "e2e_only": "true"},
        )

    assert resp.status_code == 403
    assert "Only approved E2E SKUs are allowed" in resp.json().get("detail", "")
    assert called["value"] is False


def test_constrained_intake_does_not_process_non_target_pending_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    intake_root, db_path = _configure_temp_runtime(monkeypatch, tmp_path)

    _write_jpg(intake_root / "pending" / "BK-000005" / "01.jpg")
    _write_jpg(intake_root / "pending" / "BK-000008" / "01.jpg")
    _write_jpg(intake_root / "pending" / "CL-999999" / "01.jpg")

    with _make_client() as client:
        resp = client.post(
            "/api/items/process",
            params={"skus": "BK-000005", "e2e_only": "true"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["found_skus"] == ["BK-000005"]
    assert "BK-000008" not in body["found_skus"]
    assert "CL-999999" not in body["found_skus"]
    assert _all_item_skus(db_path) == ["BK-000005"]


def test_constrained_intake_is_idempotent_for_repeat_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")
    intake_root, db_path = _configure_temp_runtime(monkeypatch, tmp_path)

    _write_jpg(intake_root / "pending" / "BK-000005" / "01.jpg")

    with _make_client() as client:
        first = client.post(
            "/api/items/process",
            params={"skus": "BK-000005", "e2e_only": "true"},
        )
        second = client.post(
            "/api/items/process",
            params={"skus": "BK-000005", "e2e_only": "true"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert _item_count(db_path, "BK-000005") == 1


def test_process_guard_off_preserves_legacy_global_trigger_safely(monkeypatch, tmp_path):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)
    _configure_temp_runtime(monkeypatch, tmp_path)

    calls: list[list[str]] = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        calls.append(cmd)

        class Dummy:
            returncode = 0

        return Dummy()

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    with _make_client() as client:
        resp = client.post("/api/items/process")

    assert resp.status_code == 200
    assert "Worker started" in resp.json().get("message", "")
    assert calls
    assert "apps/worker/src/main.py" in " ".join(calls[0])

