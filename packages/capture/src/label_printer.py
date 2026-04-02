"""
LabelPrinter — hardware stub for thermal label printer.
Implement with brother_ql or dymoprint when hardware is connected.
Until then all methods return failure gracefully — never crashes.
"""
from __future__ import annotations

from packages.core.src.result import Result


class LabelPrinter:
    """
    Hardware stub for thermal label printer.

    To activate with Brother QL series:
        pip install brother_ql
        Implement print_label() to call brother_ql.BrotherQLRaster(...)

    To activate with DYMO:
        pip install dymoprint
        Implement print_label() accordingly
    """

    def is_connected(self) -> bool:
        return False  # stub — no hardware

    def print_label(
        self,
        sku: str,
        title: str,
        category: str,
        storage_location: str,
    ) -> Result[bool]:
        """
        Print an item label. Returns failure if printer not connected.
        Label format: SKU + title (truncated) + category + storage location.
        """
        if not self.is_connected():
            return Result.failure("printer_not_connected")
        # Implementation goes here when hardware is available
        return Result.failure("not_implemented")
