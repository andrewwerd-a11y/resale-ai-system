"""
DB-backed settings helper.
Reads/writes the `settings` table created by sqlite.py migrations.

Uses sqlite3 directly so it works from scripts as well as FastAPI routes.
Never caches — always reads from DB to reflect live changes.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allowed keys — reject anything outside this set at write time
KNOWN_KEYS = frozenset({
    "photo_sort",
    "enrichment_mode",
    "default_promotion_pct",
    "listing_age_alert_days",
    "intake_default_condition",
})

# Hard-coded fallbacks used when DB is unreachable or key is missing
_DEFAULTS: dict[str, str] = {
    "photo_sort":               "auto",
    "enrichment_mode":          "hybrid",
    "default_promotion_pct":    "3",
    "listing_age_alert_days":   "30,60,90",
    "intake_default_condition": "USED_EXCELLENT",
}


def _db_path() -> Path:
    from packages.core.src.config import get_settings
    return get_settings().db_path


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Read a single setting from DB.
    Returns `default` (or the hard-coded fallback) if the key is missing.
    Never raises — DB errors return the fallback silently.
    """
    fallback = default if default is not None else _DEFAULTS.get(key)
    try:
        conn = sqlite3.connect(_db_path())
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return row[0] if row else fallback
    except Exception:
        return fallback


def set_setting(key: str, value: str) -> None:
    """
    Upsert a setting. Updates updated_at timestamp.
    Raises ValueError for unknown keys.
    """
    if key not in KNOWN_KEYS:
        raise ValueError(f"Unknown setting key: {key!r}")
    now = datetime.utcnow().isoformat()
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now),
    )
    conn.commit()
    conn.close()


def get_all_settings() -> dict[str, str]:
    """Return all settings as {key: value} dict, merged with hard-coded defaults."""
    result = dict(_DEFAULTS)
    try:
        conn = sqlite3.connect(_db_path())
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        for key, value in rows:
            result[key] = value
    except Exception:
        pass
    return result
