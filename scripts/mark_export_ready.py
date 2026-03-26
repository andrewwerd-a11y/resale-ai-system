import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord).where(ItemRecord.status == 'exported')).all()
    for item in items:
        item.status = 'export_ready'
        s.add(item)
    s.commit()
    print(f"Reset {len(items)} items to export_ready")
