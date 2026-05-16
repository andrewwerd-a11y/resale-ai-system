"""
SQLite database setup using SQLModel.
Single connection file — everything imports get_session() from here.
"""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from packages.core.src.config import get_settings


def _make_engine():
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{settings.db_path}"
    return create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )


engine = _make_engine()


def init_db() -> None:
    """Create all tables. Safe to call on every startup — idempotent."""
    # Import models so SQLModel registers them before create_all
    import packages.data.src.models.item_record  # noqa: F401
    import packages.data.src.models.review_record  # noqa: F401
    import packages.data.src.models.sale_record  # noqa: F401
    import packages.data.src.models.sourcing_batch  # noqa: F401
    import packages.data.src.models.publish_attempt_record  # noqa: F401
    import packages.data.src.models.publish_repair_plan_record  # noqa: F401
    import packages.data.src.models.publish_repair_decision_record  # noqa: F401
    import packages.data.src.models.operation_diagnostic_event_record  # noqa: F401
    import packages.data.src.models.item_photo_metadata_record  # noqa: F401
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a database session."""
    with Session(engine) as session:
        yield session


def migrate_add_columns() -> None:
    """
    Safely add new columns to existing tables without Alembic.
    Safe to call on every startup — ALTER TABLE is a no-op if column exists.
    """
    import sqlite3

    settings = get_settings()
    conn = sqlite3.connect(settings.db_path)
    cursor = conn.cursor()
    new_columns = [
        ("enrichment_done", "INTEGER DEFAULT 0"),
        ("enrichment_notes", "TEXT"),
        ("cost_manual", "INTEGER DEFAULT 0"),
        ("sourcing_location", "TEXT"),
        ("sourcing_date", "TEXT"),
        ("sourcing_batch", "TEXT"),
        # Phase 3.6 — Category Intelligence
        ("ebay_category_name", "TEXT"),
        ("category_template_fetched", "INTEGER DEFAULT 0"),
        ("category_template_fetched_at", "TEXT"),
        ("item_specifics", "TEXT"),
        ("missing_required_fields", "TEXT"),
        ("missing_recommended_fields", "TEXT"),
        ("publish_ready", "INTEGER DEFAULT 0"),
        # Phase 3.7 — Review queue
        ("review_reason", "TEXT"),
        ("review_sub_queue", "TEXT"),
        ("reviewer_notes", "TEXT"),
        ("listing_quality_score", "INTEGER"),
        ("concern_flags", "TEXT"),
        # Phase 5A — eBay offer tracking + promotions + cost
        ("offer_id", "VARCHAR"),
        ("promotion_pct", "REAL"),
        ("cost_basis", "REAL"),
        ("last_synced_at", "TEXT"),
        # Intake quality gate
        ("intake_quality_status", "TEXT"),
        ("missing_photo_types", "TEXT"),
        ("needs_more_photos_for_analysis", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in new_columns:
        try:
            cursor.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()
    _migrate_settings_table()


def _migrate_settings_table() -> None:
    """
    Create the settings table and seed default values.
    Idempotent — INSERT OR IGNORE never overwrites existing values.
    """
    import sqlite3
    from datetime import datetime

    settings = get_settings()
    conn = sqlite3.connect(settings.db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    defaults = [
        ("photo_sort",               "auto"),
        ("enrichment_mode",          "hybrid"),
        ("default_promotion_pct",    "3"),
        ("listing_age_alert_days",   "30,60,90"),
        ("intake_default_condition", "USED_EXCELLENT"),
    ]
    now = datetime.utcnow().isoformat()
    for key, value in defaults:
        cursor.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )

    conn.commit()
    conn.close()
