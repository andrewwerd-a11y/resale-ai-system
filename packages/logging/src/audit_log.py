"""
AuditLog — writes a structured log entry for every major system action.
Stored as newline-delimited JSON in data/logs/audit.jsonl
Never raises — logging failure must never break the pipeline.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path

from packages.core.src.config import get_settings


class AuditLog:
    def __init__(self):
        try:
            settings = get_settings()
            self.log_path = settings.log_dir / "audit.jsonl"
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self.log_path = Path("data/logs/audit.jsonl")

    def _write(self, entry: dict) -> None:
        try:
            entry["ts"] = datetime.utcnow().isoformat()
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # Never let logging break the pipeline

    def item_created(self, sku: str, batch_id: str, folder: str) -> None:
        self._write({"event": "item_created", "sku": sku,
                     "batch_id": batch_id, "folder": folder})

    def item_analyzed(self, sku: str, confidence: float, model: str) -> None:
        self._write({"event": "item_analyzed", "sku": sku,
                     "confidence": confidence, "model": model})

    def item_triaged(self, sku: str, mode: str, reasons: list) -> None:
        self._write({"event": "item_triaged", "sku": sku,
                     "mode": mode, "reasons": reasons})

    def item_approved(self, sku: str, by: str = "human") -> None:
        self._write({"event": "item_approved", "sku": sku, "by": by})

    def item_rejected(self, sku: str, by: str = "human") -> None:
        self._write({"event": "item_rejected", "sku": sku, "by": by})

    def item_exported(self, sku: str, export_file: str) -> None:
        self._write({"event": "item_exported", "sku": sku, "file": export_file})

    def item_sold(self, sku: str, sold_price: float, platform: str) -> None:
        self._write({"event": "item_sold", "sku": sku,
                     "sold_price": sold_price, "platform": platform})

    def manual_override(self, sku: str, fields: list) -> None:
        self._write({"event": "manual_override", "sku": sku, "fields": fields})

    def worker_started(self, batch_id: str, item_count: int) -> None:
        self._write({"event": "worker_started", "batch_id": batch_id,
                     "item_count": item_count})

    def worker_finished(self, batch_id: str, stats: dict) -> None:
        self._write({"event": "worker_finished", "batch_id": batch_id, **stats})

    def error(self, sku: str | None, stage: str, message: str) -> None:
        self._write({"event": "error", "sku": sku,
                     "stage": stage, "message": message})
