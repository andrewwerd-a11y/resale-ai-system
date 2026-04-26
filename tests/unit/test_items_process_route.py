from __future__ import annotations

import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.src.routes import items


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    return TestClient(app)


def test_process_rejects_when_route_guard_on_and_no_skus(monkeypatch):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")

    with _client() as client:
        resp = client.post("/api/items/process")

    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "Explicit SKU constraints are required" in detail


def test_process_rejects_non_approved_sku_when_route_guard_on(monkeypatch):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")

    with _client() as client:
        resp = client.post("/api/items/process", params={"skus": "BK-999999", "e2e_only": "true"})

    assert resp.status_code == 403
    detail = resp.json().get("detail", "")
    assert "Only approved E2E SKUs are allowed" in detail


def test_process_guard_on_approved_missing_folder_returns_missing_skus(monkeypatch):
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")

    calls: list[list[str]] = []

    def fake_run_worker_for_skus(skus: list[str]):
        calls.append(list(skus))
        return {
            "ok": True,
            "error": None,
            "message": "no_matching_pending_folders",
            "requested_skus": skus,
            "found_skus": [],
            "missing_skus": skus,
            "processed_count": 0,
            "approved_count": 0,
            "review_count": 0,
            "rejected_count": 0,
            "failed_count": 0,
        }

    def fail_subprocess_run(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Global subprocess worker path should not run in constrained mode")

    monkeypatch.setattr("apps.worker.src.main.run_worker_for_skus", fake_run_worker_for_skus)
    monkeypatch.setattr(subprocess, "run", fail_subprocess_run)

    with _client() as client:
        resp = client.post("/api/items/process", params={"skus": "BK-000005", "e2e_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["requested_skus"] == ["BK-000005"]
    assert body["found_skus"] == []
    assert body["missing_skus"] == ["BK-000005"]
    assert body["processed_count"] == 0
    assert calls == [["BK-000005"]]


def test_process_route_guard_off_uses_legacy_global_background_worker(monkeypatch):
    monkeypatch.delenv("E2E_ROUTE_GUARD_ENABLED", raising=False)
    monkeypatch.delenv("APPROVED_E2E_SKUS", raising=False)

    calls: list[list[str]] = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        calls.append(cmd)
        class Dummy:  # pragma: no cover
            returncode = 0
        return Dummy()

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    with _client() as client:
        resp = client.post("/api/items/process")

    assert resp.status_code == 200
    body = resp.json()
    assert "Worker started" in body.get("message", "")
    assert calls, "Expected legacy subprocess worker path to be scheduled/executed"
    joined = " ".join(calls[0])
    assert "apps/worker/src/main.py" in joined

