from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.src.routes import capture


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(capture.router, prefix="/api/capture", tags=["capture"])
    return TestClient(app)


def test_capture_intake_readiness_defaults_to_manual_folder_mode(monkeypatch):
    def _unexpected(*_args, **_kwargs):
        raise AssertionError("hardware should not be touched for intake readiness")

    monkeypatch.setattr("packages.capture.src.camera_controller.CameraController.__init__", _unexpected)
    monkeypatch.setattr("packages.capture.src.label_printer.LabelPrinter.__init__", _unexpected)

    with _client() as client:
        resp = client.get("/api/capture/intake-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["hardware_intake_enabled"] is False
    assert body["current_mode"] == "manual_folder_intake"
    assert body["supported_future_inputs"] == [
        "camera",
        "barcode_scanner",
        "scale",
        "lightbox",
        "workstation_folder",
    ]
    assert "Hardware intake integration is not configured." in body["blockers"]
