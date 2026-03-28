from __future__ import annotations
import logging
import json
from datetime import datetime
from pathlib import Path
from packages.core.src.config import get_settings

_logger: logging.Logger | None = None


def get_logger(name: str = "resale-ai") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    settings = get_settings()
    log_dir = Path(settings.db_path).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(level)

    if not logger.handlers:
        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(ch)

        # File handler
        fh = logging.FileHandler(log_dir / "app.log")
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)

    _logger = logger
    return logger


def log_action(action: str, sku: str, detail: str = "", extra: dict | None = None) -> None:
    logger = get_logger()
    payload = {"action": action, "sku": sku, "detail": detail, "ts": datetime.utcnow().isoformat()}
    if extra:
        payload.update(extra)
    logger.info(json.dumps(payload))
