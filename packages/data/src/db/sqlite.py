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
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a database session."""
    with Session(engine) as session:
        yield session
