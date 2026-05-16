from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from apps.api.src.routes import ebay, export, items, listings, sync
from packages.core.src import config as core_config
from packages.data.src.db import sqlite as sqlite_db
from apps.worker.src import main as worker_main


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(items.router, prefix="/api/items", tags=["items"])
    app.include_router(export.router, prefix="/api/export", tags=["export"])
    app.include_router(ebay.router, prefix="/api/ebay", tags=["ebay"])
    app.include_router(listings.router, prefix="/api/listings", tags=["listings"])
    app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
    return TestClient(app)


def _configure_runtime(monkeypatch, tmp_path):
    db_path = tmp_path / "route_guard_matrix.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("E2E_ROUTE_GUARD_ENABLED", "true")
    monkeypatch.setenv("APPROVED_E2E_SKUS", "BK-000005,BK-000008,BK-000009")

    core_config.get_settings.cache_clear()
    sqlite_db.get_settings.cache_clear()

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(sqlite_db, "engine", engine)
    sqlite_db.init_db()

    # Keep route tests local and deterministic.
    monkeypatch.setattr(
        worker_main,
        "run_worker_for_skus",
        lambda skus: {
            "ok": True,
            "requested_skus": list(skus),
            "found_skus": [],
            "missing_skus": list(skus),
            "processed_count": 0,
        },
    )


def _request(client: TestClient, method: str, path: str, *, params=None, json=None, files=None):
    kwargs = {}
    if params is not None:
        kwargs["params"] = params
    if json is not None:
        kwargs["json"] = json
    if files is not None:
        kwargs["files"] = files
    return client.request(method, path, **kwargs)


def test_single_sku_guarded_mutation_routes_allow_approved_and_block_non_approved(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path)

    routes = [
        ("PATCH", "/api/items/{sku}", {"json": {"title_final": "x"}}),
        ("PATCH", "/api/items/{sku}/cost", {"json": {"cost": 9.5}}),
        ("POST", "/api/items/{sku}/analyze", {}),
        ("POST", "/api/items/{sku}/enrich", {}),
        ("POST", "/api/items/{sku}/category-intelligence", {}),
        (
            "POST",
            "/api/items/{sku}/photos",
            {"files": [("files", ("p.jpg", b"fake", "image/jpeg"))]},
        ),
        ("POST", "/api/items/{sku}/photos/host", {}),
        ("PATCH", "/api/items/{sku}/photos/metadata", {"json": {"updates": [{"image_path": "https://example.test/p.jpg", "photo_type": "front"}]}}),
        ("DELETE", "/api/items/{sku}/photos", {"json": {"url": "https://example.test/p.jpg"}}),
        ("POST", "/api/items/{sku}/photos/set-cover", {"json": {"url": "https://example.test/p.jpg"}}),
        ("POST", "/api/ebay/repair-queue/{sku}/recheck-readiness", {}),
        ("POST", "/api/ebay/repair-queue/{sku}/draft-fix", {}),
        (
            "POST",
            "/api/ebay/repair-queue/{sku}/apply-draft-fix",
            {"json": {"sku": "{sku}", "repair_plan_id": "plan-1", "approved": False}},
        ),
        (
            "POST",
            "/api/ebay/mark-sold/{sku}",
            {"params": {"sold_price": 10.0, "fees": 1.0, "platform": "ebay"}},
        ),
        ("PATCH", "/api/ebay/listing/{sku}", {"json": {"list_price": 10.0}}),
        ("POST", "/api/listings/push/{sku}", {"json": {"promotion_enabled": False}}),
        ("DELETE", "/api/listings/end/{sku}", {}),
        ("POST", "/api/sync/relist/{sku}", {}),
    ]

    with _client() as client:
        for method, route, payload in routes:
            resolved_payload = dict(payload)
            if "json" in resolved_payload and isinstance(resolved_payload["json"], dict):
                resolved_payload["json"] = {
                    key: (value.format(sku="BK-999999") if isinstance(value, str) and "{sku}" in value else value)
                    for key, value in resolved_payload["json"].items()
                }
            blocked = _request(client, method, route.format(sku="BK-999999"), **resolved_payload)
            assert blocked.status_code == 403
            assert "Only approved E2E SKUs are allowed" in blocked.json().get("detail", "")

            if "json" in resolved_payload and isinstance(resolved_payload["json"], dict):
                resolved_allowed = dict(resolved_payload)
                resolved_allowed["json"] = {
                    key: (value.format(sku="BK-000005") if isinstance(value, str) and "{sku}" in value else value)
                    for key, value in resolved_payload["json"].items()
                }
            else:
                resolved_allowed = resolved_payload
            allowed = _request(client, method, route.format(sku="BK-000005"), **resolved_allowed)
            assert allowed.status_code != 403


def test_global_guarded_mutation_routes_refuse_without_explicit_skus(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path)

    routes = [
        ("POST", "/api/export/ebay-csv", {}),
        ("POST", "/api/export/master-sheet", {}),
        ("POST", "/api/ebay/publish/batch", {}),
        ("POST", "/api/ebay/sync-sold", {}),
        ("GET", "/api/listings/sync", {}),
        ("POST", "/api/items/apply-stale-drops", {}),
        ("POST", "/api/items/process", {}),
        ("POST", "/api/sync/relist-all", {}),
        ("POST", "/api/listings/bulk/price", {"json": {"skus": [], "price": 9.99}}),
        ("POST", "/api/listings/bulk/promo", {"json": {"skus": [], "promotion_pct": 3.0}}),
        ("POST", "/api/items/bulk-approve", {"json": {"skus": []}}),
        ("POST", "/api/items/bulk-review", {"json": {"skus": []}}),
        ("POST", "/api/items/bulk-reject", {"json": {"skus": []}}),
    ]

    with _client() as client:
        for method, path, payload in routes:
            resp = _request(client, method, path, **payload)
            assert resp.status_code == 403
            assert "Explicit SKU constraints are required" in resp.json().get("detail", "")


def test_global_guarded_mutation_routes_block_non_approved_and_allow_approved(monkeypatch, tmp_path):
    _configure_runtime(monkeypatch, tmp_path)

    routes = [
        ("POST", "/api/export/ebay-csv", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/export/master-sheet", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/ebay/publish/batch", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/ebay/sync-sold", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/ebay/repair-queue/bulk-draft-fixes", {"json": {"skus": ["BK-999999"]}}, {"json": {"skus": ["BK-000005"]}}),
        ("POST", "/api/ebay/repair-queue/bulk-apply-approved-fixes", {"json": {"approvals": [{"sku": "BK-999999", "repair_plan_id": "plan-1", "approved": False}]}}, {"json": {"approvals": [{"sku": "BK-000005", "repair_plan_id": "plan-1", "approved": False}]}}),
        ("GET", "/api/listings/sync", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/items/apply-stale-drops", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/items/process", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/sync/relist-all", {"params": {"skus": "BK-999999", "e2e_only": "true"}}, {"params": {"skus": "BK-000005", "e2e_only": "true"}}),
        ("POST", "/api/listings/bulk/price", {"json": {"skus": ["BK-999999"], "price": 9.99}}, {"json": {"skus": ["BK-000005"], "price": 9.99}}),
        ("POST", "/api/listings/bulk/promo", {"json": {"skus": ["BK-999999"], "promotion_pct": 3.0}}, {"json": {"skus": ["BK-000005"], "promotion_pct": 3.0}}),
        ("POST", "/api/items/bulk-approve", {"json": {"skus": ["BK-999999"]}}, {"json": {"skus": ["BK-000005"]}}),
        ("POST", "/api/items/bulk-review", {"json": {"skus": ["BK-999999"]}}, {"json": {"skus": ["BK-000005"]}}),
        ("POST", "/api/items/bulk-reject", {"json": {"skus": ["BK-999999"]}}, {"json": {"skus": ["BK-000005"]}}),
    ]

    with _client() as client:
        for method, path, blocked_payload, allowed_payload in routes:
            blocked = _request(client, method, path, **blocked_payload)
            assert blocked.status_code == 403
            assert "Only approved E2E SKUs are allowed" in blocked.json().get("detail", "")

            allowed = _request(client, method, path, **allowed_payload)
            assert allowed.status_code != 403
