from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.src.routes import ui


def test_intake_queue_ui_uses_operator_evidence_sections():
    app = FastAPI()
    app.include_router(ui.router)

    with TestClient(app) as client:
        resp = client.get("/intake")

    assert resp.status_code == 200
    body = resp.text
    assert "Next photos needed" in body
    assert "Evidence needed" in body
    assert "/correction-report-v2" in body
    assert "operator_photo_evidence" in body
    assert "Photo labels" in body
    assert "/photos/metadata" in body
    assert "local-only labels; no publish or approval changes" in body
    assert "Title page" in body
    assert "Front cover" in body
    assert '"value": "title_page"' in body
    assert "Cover/display image" in body


def test_intake_pipeline_cockpit_ui_uses_operator_evidence_sections():
    app = FastAPI()
    app.include_router(ui.router)

    with TestClient(app) as client:
        resp = client.get("/intake-pipeline/BK-PIPE")

    assert resp.status_code == 200
    body = resp.text
    assert "Operator photo evidence" in body
    assert "Next photos needed" in body
    assert "Evidence needed" in body
    assert "/correction-report-v2" in body
    assert "operator_photo_evidence" in body
    assert "Photo labels" in body
    assert "/photos/metadata" in body
    assert "local-only labels; no publish or approval changes" in body
    assert "loadInitialEvidence()" in body
    assert "Loading intake evidence" in body
    assert "Title page" in body
    assert '"value": "title_page"' in body
    assert "Display image means the first image shown locally" in body
    assert "Save all labels" in body
    assert "collectAllPhotoMetadataUpdates()" in body
    assert "Unsaved changes" in body
    assert "Generate limited-evidence draft" in body
    assert "Use this only to create a rough draft." in body
    assert "This does not approve or publish the item." in body
    assert "Missing required evidence will still block publish." in body
    assert "Extraction confidence" in body
    assert "Category confidence" in body
    assert "Photo evidence confidence" in body
    assert "This is not category confidence, photo evidence confidence, pricing confidence, or publish readiness." in body
    assert "Confidence warnings" in body
    assert "generateLimitedEvidenceDraft()" in body
    assert "Save label" not in body
