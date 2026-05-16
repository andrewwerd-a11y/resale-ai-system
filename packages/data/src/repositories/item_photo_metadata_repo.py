from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from packages.data.src.models.item_photo_metadata_record import ItemPhotoMetadataRecord


class ItemPhotoMetadataRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_for_sku(self, sku: str) -> list[ItemPhotoMetadataRecord]:
        stmt = (
            select(ItemPhotoMetadataRecord)
            .where(ItemPhotoMetadataRecord.sku == sku)
            .order_by(ItemPhotoMetadataRecord.sort_order, ItemPhotoMetadataRecord.created_at)
        )
        return list(self.session.exec(stmt).all())

    def get_by_sku_and_path(self, sku: str, image_path: str) -> ItemPhotoMetadataRecord | None:
        stmt = select(ItemPhotoMetadataRecord).where(
            ItemPhotoMetadataRecord.sku == sku,
            ItemPhotoMetadataRecord.image_path == image_path,
        )
        return self.session.exec(stmt).first()

    def upsert(
        self,
        *,
        sku: str,
        image_path: str,
        photo_type: str,
        label_source: str,
        confidence: float | None,
        is_cover: bool,
        sort_order: int | None,
        notes: str | None,
    ) -> ItemPhotoMetadataRecord:
        record = self.get_by_sku_and_path(sku, image_path)
        now = datetime.utcnow()
        if record is None:
            record = ItemPhotoMetadataRecord(
                sku=sku,
                image_path=image_path,
                photo_type=photo_type,
                label_source=label_source,
                confidence=confidence,
                is_cover=is_cover,
                sort_order=sort_order,
                notes=notes,
                created_at=now,
                updated_at=now,
            )
        else:
            record.photo_type = photo_type
            record.label_source = label_source
            record.confidence = confidence
            record.is_cover = is_cover
            record.sort_order = sort_order
            record.notes = notes
            record.updated_at = now
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def delete_for_sku_and_path(self, sku: str, image_path: str) -> bool:
        record = self.get_by_sku_and_path(sku, image_path)
        if record is None:
            return False
        self.session.delete(record)
        self.session.commit()
        return True

    def sync_cover_and_sort_order(self, sku: str, ordered_paths: list[str]) -> None:
        if not ordered_paths:
            return
        records = self.list_for_sku(sku)
        by_path = {record.image_path: record for record in records}
        changed = False
        for idx, path in enumerate(ordered_paths):
            record = by_path.get(path)
            if record is None:
                continue
            is_cover = idx == 0
            if record.sort_order != idx or record.is_cover != is_cover:
                record.sort_order = idx
                record.is_cover = is_cover
                record.updated_at = datetime.utcnow()
                self.session.add(record)
                changed = True
        if changed:
            self.session.commit()
