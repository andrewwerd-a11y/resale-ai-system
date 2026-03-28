from sqlmodel import SQLModel, create_engine, Session
from packages.core.src.config import get_settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = f"sqlite:///{settings.db_path}"
        _engine = create_engine(db_url, echo=False, connect_args={"check_same_thread": False})
    return _engine


def init_db() -> None:
    """Create all tables if they don't exist."""
    # Import models so SQLModel registers them before create_all
    from packages.data.src.models import item_record, sku_record, batch_record, review_record  # noqa: F401
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    print("Database initialized.")


def get_session() -> Session:
    """
    Return a new Session as a context manager.
    Usage: with get_session() as session: ...
    """
    return Session(get_engine())
