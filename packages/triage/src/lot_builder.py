"""
LotBuilder — groups individual items into a single lot listing.
Member items get status 'lot_member'. The lot gets item_mode='lot'.
"""
from __future__ import annotations

import logging
from datetime import datetime

from packages.core.src.result import Result

logger = logging.getLogger(__name__)


class LotBuilder:
    def create_lot(
        self,
        skus: list[str],
        title: str,
        price: float,
        session,
    ) -> Result[str]:
        """
        Group items into a lot. Assigns lot SKU, marks members.
        Returns lot SKU on success.
        """
        from packages.data.src.repositories.item_repo import ItemRepository
        from packages.domain.src.entities.item import Item

        if len(skus) < 2:
            return Result.failure("A lot requires at least 2 items")

        repo = ItemRepository(session)

        items = []
        for sku in skus:
            item = repo.get_by_sku(sku)
            if not item:
                return Result.failure(f"Item {sku} not found")
            items.append(item)

        first = items[0]
        lot_sku = f"LOT-{first.sku}" if first.sku else f"LOT-{skus[0]}"

        # Check lot SKU doesn't already exist
        existing = repo.get_by_sku(lot_sku)
        if existing:
            import uuid
            lot_sku = f"LOT-{str(uuid.uuid4())[:8].upper()}"

        lot_item = Item(
            sku=lot_sku,
            status="approved",
            item_mode="lot",
            title_final=title,
            title_raw=title,
            list_price=price,
            estimated_price=price,
            category_key=first.category_key,
            category_label=first.category_label,
            ebay_category_id=first.ebay_category_id,
            lot_group_id=lot_sku,
            bundle_candidate=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        repo.upsert(lot_item)

        for item in items:
            item.status = "lot_member"
            item.lot_group_id = lot_sku
            item.item_mode = "lot"
            repo.upsert(item)

        logger.info("Created lot %s with %d items: %s", lot_sku, len(skus), skus)
        return Result.success(lot_sku)

    def dissolve_lot(self, lot_sku: str, session) -> Result[int]:
        """Remove a lot, returning member items to 'approved' status."""
        from packages.data.src.repositories.item_repo import ItemRepository
        from sqlmodel import select
        from packages.data.src.models.item_record import ItemRecord

        repo = ItemRepository(session)

        # Free member items
        stmt = select(ItemRecord).where(ItemRecord.lot_group_id == lot_sku)
        members = session.exec(stmt).all()
        for record in members:
            if record.sku != lot_sku:
                record.status = "approved"
                record.lot_group_id = None
                record.item_mode = "single"
                session.add(record)

        # Remove lot item
        lot_record = session.exec(
            select(ItemRecord).where(ItemRecord.sku == lot_sku)
        ).first()
        if lot_record:
            session.delete(lot_record)

        session.commit()
        logger.info("Dissolved lot %s, freed %d members", lot_sku, len(members) - 1)
        return Result.success(len(members) - 1)
