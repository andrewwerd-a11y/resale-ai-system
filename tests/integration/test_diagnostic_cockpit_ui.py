from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.src.routes import ui


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ui.router, tags=["ui"])
    return TestClient(app)


def test_diagnostic_cockpit_page_renders_expected_sections() -> None:
    with _client() as client:
        resp = client.get("/diagnostics")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "Diagnostic Cockpit v1" in html
    assert "Run Read-Only Publish Diagnostics" in html
    assert "Preview Bulk Publish" in html
    assert "Recent Operation Events" in html
    assert "Per-SKU Diagnostic History" in html
    assert "Diagnostic Reports" in html
    assert "/api/listings/publish-diagnostics/batch" in html
    assert "/api/ebay/publish/batch-preview" in html
    assert "/api/diagnostics/events/recent" in html
    assert "/api/diagnostics/reports/weekly" in html
    assert "No live eBay mutation" in html
    assert "No external report sending" in html


def test_nav_includes_diagnostics_link() -> None:
    with _client() as client:
        resp = client.get("/reports")

    assert resp.status_code == 200
    assert 'href="/diagnostics"' in resp.text
