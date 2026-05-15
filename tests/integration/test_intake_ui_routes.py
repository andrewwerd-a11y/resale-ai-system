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
