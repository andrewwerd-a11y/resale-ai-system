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
    ]
    for col_name, col_def in new_columns:
        try:
            cursor.execute(f"ALTER TABLE items ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()
    conn.close()
