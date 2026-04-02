"""
Capture station API endpoints.
Hardware stubs — safe to call when no camera/printer is connected.
"""
from __future__ import annotations

from fastapi import APIRouter

from packages.capture.src.camera_controller import CameraController
from packages.capture.src.label_printer import LabelPrinter

router = APIRouter()

# Module-level watcher state (single instance per server process)
_watcher = None
_watcher_running = False


@router.get("/status")
def capture_status():
    camera = CameraController()
    printer = LabelPrinter()
    return {
        "camera_connected": camera.is_connected(),
        "printer_connected": printer.is_connected(),
        "watcher_running": _watcher_running,
    }


@router.post("/watcher/start")
def start_watcher(watch_folder: str = ""):
    global _watcher, _watcher_running
    from pathlib import Path
    from packages.core.src.config import get_settings
    from packages.capture.src.file_watcher import IntakeWatcher

    settings = get_settings()
    folder = Path(watch_folder) if watch_folder else settings.intake_root / "pending"

    def _callback(photo_path: Path):
        import logging
        logging.getLogger(__name__).info("Watcher saw: %s", photo_path)

    if _watcher and _watcher.is_running:
        return {"ok": False, "message": "Watcher already running"}

    _watcher = IntakeWatcher(folder, _callback)
    _watcher.start()
    _watcher_running = _watcher.is_running
    return {"ok": True, "watching": str(folder), "running": _watcher_running}


@router.post("/watcher/stop")
def stop_watcher():
    global _watcher, _watcher_running
    if _watcher:
        _watcher.stop()
        _watcher_running = False
    return {"ok": True, "running": False}


@router.post("/print-label/{sku}")
def print_label(
    sku: str,
    title: str = "",
    category: str = "",
    storage_location: str = "",
):
    printer = LabelPrinter()
    result = printer.print_label(sku, title, category, storage_location)
    if not result.ok:
        return {"ok": False, "error": result.error}
    return {"ok": True}
