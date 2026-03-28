from __future__ import annotations
import json
from typing import Optional
from datetime import datetime
from sqlmodel import Session, select

from packages.data.src.models.item_record import ItemRecord
from packages.domain.src.entities.item import Item


def _to_json(lst: list) -> Optional[str]:
    if not lst:
        return None
    return json.dumps(lst)


def _from_json(s: Optional[str]) -> list:
    if not s:
        return []
    try:
        return json.loads(s)
    except Exception:
        return []


def _paths_to_str(paths: list[str]) -> Optional[str]:
    if not paths:
        return None
    return "|".join(paths)


def _str_to_paths(s: Optional[str]) -> list[str]:
    if not s:
        return []
    return [p for p in s.split("|") if p]


def record_to_item(r: ItemRecord) -> Item:
    return Item(
        sku=r.sku,
        batch_id=r.batch_id,
        image_paths=_str_to_paths(r.image_paths),
        hosted_photo_urls=_str_to_paths(r.hosted_photo_urls),
        title=r.title,
        category=r.category,
        brand=r.brand,
        item_type=r.item_type,
        department=r.department,
        size=r.size,
        color=r.color,
        material=r.material,
        style=r.style,
        condition=r.condition,
        condition_id=r.condition_id,
        condition_notes=r.condition_notes,
        author=r.author,
        book_format=r.book_format,
        isbn=r.isbn,
        publisher=r.publisher,
        publication_year=r.publication_year,
        franchise=r.franchise,
        character=r.character,
        features=_from_json(r.features),
        defects=_from_json(r.defects),
        keywords=_from_json(r.keywords),
        review_reasons=_from_json(r.review_reasons),
        estimated_price=r.estimated_price,
        list_price=r.list_price,
        sold_price=r.sold_price,
        ebay_fees=r.ebay_fees,
        net_profit=r.net_profit,
        ai_confidence=r.ai_confidence,
        ai_model=r.ai_model,
        raw_ai_response=r.raw_ai_response,
        status=r.status,
        notes=r.notes,
        manual_override=r.manual_override,
        ebay_listing_id=r.ebay_listing_id,
        ebay_listing_url=r.ebay_listing_url,
        ebay_offer_id=r.ebay_offer_id,
        date_listed=r.date_listed,
        date_sold=r.date_sold,
        date_created=r.date_created,
        date_updated=r.date_updated,
    )


def item_to_record(item: Item) -> ItemRecord:
    return ItemRecord(
        sku=item.sku,
        batch_id=item.batch_id,
        image_paths=_paths_to_str(item.image_paths),
        hosted_photo_urls=_paths_to_str(item.hosted_photo_urls),
        title=item.title,
        category=item.category,
        brand=item.brand,
        item_type=item.item_type,
        department=item.department,
        size=item.size,
        color=item.color,
        material=item.material,
        style=item.style,
        condition=item.condition,
        condition_id=item.condition_id,
        condition_notes=item.condition_notes,
        author=item.author,
        book_format=item.book_format,
        isbn=item.isbn,
        publisher=item.publisher,
        publication_year=item.publication_year,
        franchise=item.franchise,
        character=item.character,
        features=_to_json(item.features),
        defects=_to_json(item.defects),
        keywords=_to_json(item.keywords),
        review_reasons=_to_json(item.review_reasons),
        estimated_price=item.estimated_price,
        list_price=item.list_price,
        sold_price=item.sold_price,
        ebay_fees=item.ebay_fees,
        net_profit=item.net_profit,
        ai_confidence=item.ai_confidence,
        ai_model=item.ai_model,
        raw_ai_response=item.raw_ai_response,
        status=item.status,
        notes=item.notes,
        manual_override=item.manual_override,
        ebay_listing_id=item.ebay_listing_id,
        ebay_listing_url=item.ebay_listing_url,
        ebay_offer_id=item.ebay_offer_id,
        date_listed=item.date_listed,
        date_sold=item.date_sold,
        date_created=item.date_created,
        date_updated=item.date_updated,
    )


class ItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_sku(self, sku: str) -> Optional[Item]:
        record = self._session.get(ItemRecord, sku)
        if record is None:
            return None
        return record_to_item(record)

    def list_all(self) -> list[Item]:
        records = self._session.exec(select(ItemRecord)).all()
        return [record_to_item(r) for r in records]

    def list_by_status(self, status: str) -> list[Item]:
        stmt = select(ItemRecord).where(ItemRecord.status == status)
        records = self._session.exec(stmt).all()
        return [record_to_item(r) for r in records]

    def list_by_statuses(self, statuses: list[str]) -> list[Item]:
        stmt = select(ItemRecord).where(ItemRecord.status.in_(statuses))  # type: ignore
        records = self._session.exec(stmt).all()
        return [record_to_item(r) for r in records]

    def upsert(self, item: Item) -> Item:
        existing = self._session.get(ItemRecord, item.sku)
        record = item_to_record(item)
        record.date_updated = datetime.utcnow()
        if existing is None:
            record.date_created = record.date_created or datetime.utcnow()
            self._session.add(record)
        else:
            # Preserve manual_override flag
            if existing.manual_override and not item.manual_override:
                record.manual_override = True
            for key, val in record.model_dump().items():
                setattr(existing, key, val)
            self._session.add(existing)
        self._session.commit()
        return self.get_by_sku(item.sku)  # type: ignore

    def update_status(self, sku: str, status: str) -> bool:
        record = self._session.get(ItemRecord, sku)
        if record is None:
            return False
        record.status = status
        record.date_updated = datetime.utcnow()
        self._session.add(record)
        self._session.commit()
        return True

    def update_ebay(
        self,
        sku: str,
        listing_id: str,
        offer_id: str,
        listing_url: str,
        status: str = "listed",
    ) -> bool:
        record = self._session.get(ItemRecord, sku)
        if record is None:
            return False
        record.ebay_listing_id = listing_id
        record.ebay_offer_id = offer_id
        record.ebay_listing_url = listing_url
        record.status = status
        record.date_listed = datetime.utcnow()
        record.date_updated = datetime.utcnow()
        self._session.add(record)
        self._session.commit()
        return True

    def count_by_status(self) -> dict[str, int]:
        records = self._session.exec(select(ItemRecord)).all()
        counts: dict[str, int] = {}
        for r in records:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    def search(self, query: str) -> list[Item]:
        q = f"%{query.lower()}%"
        stmt = select(ItemRecord).where(
            (ItemRecord.sku.ilike(q))  # type: ignore
            | (ItemRecord.title.ilike(q))  # type: ignore
            | (ItemRecord.brand.ilike(q))  # type: ignore
        )
        return [record_to_item(r) for r in self._session.exec(stmt).all()]
