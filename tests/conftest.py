"""
Shared pytest fixtures — in-memory DB, sessions, sample objects.
ALL tests use in-memory SQLite. The production data/app.db is never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

# Ensure repo root is on sys.path so all packages resolve.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def test_engine():
    """Fresh in-memory SQLite engine per test — never touches data/app.db."""
    # Import all models so SQLModel registers their table schemas
    import packages.data.src.models.item_record  # noqa: F401
    import packages.data.src.models.sku_record  # noqa: F401
    import packages.data.src.models.sale_record  # noqa: F401
    import packages.data.src.models.sourcing_batch  # noqa: F401
    import packages.data.src.models.publish_attempt_record  # noqa: F401
    import packages.data.src.models.publish_repair_plan_record  # noqa: F401
    import packages.data.src.models.publish_repair_decision_record  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    # In-memory DB is destroyed automatically; drop_all is belt-and-suspenders.
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def test_session(test_engine):
    """Yield a live Session backed by the in-memory engine."""
    with Session(test_engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_item():
    from tests.fixtures.sample_items import make_clothing_item
    return make_clothing_item(sku="CL-000001", status="approved")


@pytest.fixture
def sample_book():
    from tests.fixtures.sample_items import make_book_item
    return make_book_item(sku="BK-000001", status="approved")
