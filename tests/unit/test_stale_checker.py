from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from packages.data.src.models.item_record import ItemRecord
from packages.sync.src.stale_checker import StaleChecker


def _make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed_item(
    session: Session,
    *,
    sku: str,
    status: str = "listed",
    date_listed: datetime | None = None,
    days_listed: int | None = None,
    list_price: float | None = None,
    minimum_price: float | None = None,
) -> ItemRecord:
    item = ItemRecord(
        sku=sku,
        status=status,
        date_listed=date_listed,
        days_listed=days_listed,
        list_price=list_price,
        minimum_price=minimum_price,
    )
    session.add(item)
    session.commit()
    return item


def test_refresh_days_listed_recalculates_from_date_listed(monkeypatch):
    monkeypatch.setattr(
        "packages.sync.src.stale_checker.get_rules",
        lambda: {"pricing": {"stale_listing_days": 60, "stale_price_drop_percent": 10}},
    )
    with _make_session() as session:
        listed_at = datetime.utcnow() - timedelta(days=12, hours=3)
        _seed_item(
            session,
            sku="BK-000005",
            date_listed=listed_at,
            days_listed=0,
            list_price=25.0,
        )

        checker = StaleChecker()
        changed = checker.refresh_days_listed(session)

        refreshed = session.exec(
            select(ItemRecord).where(ItemRecord.sku == "BK-000005")
        ).first()
        assert changed == 1
        assert refreshed is not None
        expected_days = (datetime.utcnow().date() - listed_at.date()).days
        assert refreshed.days_listed == expected_days


def test_refresh_days_listed_handles_missing_date_listed(monkeypatch):
    monkeypatch.setattr(
        "packages.sync.src.stale_checker.get_rules",
        lambda: {"pricing": {"stale_listing_days": 60, "stale_price_drop_percent": 10}},
    )
    with _make_session() as session:
        _seed_item(
            session,
            sku="BK-000008",
            date_listed=None,
            days_listed=999,
            list_price=30.0,
        )

        checker = StaleChecker()
        changed = checker.refresh_days_listed(session)

        refreshed = session.exec(
            select(ItemRecord).where(ItemRecord.sku == "BK-000008")
        ).first()
        assert changed == 1
        assert refreshed is not None
        assert refreshed.days_listed is None


def test_apply_price_drops_uses_recalculated_days_listed(monkeypatch):
    monkeypatch.setattr(
        "packages.sync.src.stale_checker.get_rules",
        lambda: {"pricing": {"stale_listing_days": 60, "stale_price_drop_percent": 10}},
    )
    with _make_session() as session:
        _seed_item(
            session,
            sku="BK-000009",
            date_listed=datetime.utcnow() - timedelta(days=70),
            days_listed=0,
            list_price=100.0,
            minimum_price=70.0,
        )

        checker = StaleChecker()
        updated = checker.apply_price_drops(session)

        refreshed = session.exec(
            select(ItemRecord).where(ItemRecord.sku == "BK-000009")
        ).first()
        assert updated == 1
        assert refreshed is not None
        assert refreshed.days_listed is not None and refreshed.days_listed >= 60
        assert refreshed.list_price == 90.0


def test_apply_price_drops_leaves_non_stale_items_unchanged(monkeypatch):
    monkeypatch.setattr(
        "packages.sync.src.stale_checker.get_rules",
        lambda: {"pricing": {"stale_listing_days": 60, "stale_price_drop_percent": 10}},
    )
    with _make_session() as session:
        _seed_item(
            session,
            sku="BK-000010",
            date_listed=datetime.utcnow() - timedelta(days=10),
            days_listed=999,
            list_price=80.0,
            minimum_price=60.0,
        )

        checker = StaleChecker()
        updated = checker.apply_price_drops(session)

        refreshed = session.exec(
            select(ItemRecord).where(ItemRecord.sku == "BK-000010")
        ).first()
        assert updated == 0
        assert refreshed is not None
        assert refreshed.days_listed is not None and refreshed.days_listed < 60
        assert refreshed.list_price == 80.0
