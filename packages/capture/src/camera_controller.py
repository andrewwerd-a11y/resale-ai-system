"""
CameraController — hardware stub for auto-capture integration.
Implement trigger() with gphoto2 (Linux/Mac) or DigiCamControl (Windows)
when hardware is connected. Until then all methods return failure gracefully.
"""
from __future__ import annotations

from dataclasses import dataclass

from packages.core.src.result import Result


@dataclass
class PositioningResult:
    ok: bool
    message: str


class CameraController:
    """
    Hardware stub for auto-capture integration.

    To activate with gphoto2 (Linux/Mac):
        pip install gphoto2
        Implement trigger() to call gp.Camera().capture(...)

    To activate with DigiCamControl (Windows):
        Use subprocess to call dccmd.exe
        Implement trigger() accordingly
    """

    def is_connected(self) -> bool:
        return False  # stub — no hardware

    def trigger(self) -> Result[str]:
        """Trigger camera capture. Returns path to saved photo."""
        return Result.failure("camera_not_connected")

    def check_positioning(self, live_view_frame=None) -> PositioningResult:
        """Check if subject is correctly positioned in live view."""
        return PositioningResult(ok=False, message="camera_not_connected")

    def get_live_view(self) -> Result[bytes]:
        """Return JPEG bytes from camera live view."""
        return Result.failure("camera_not_connected")
