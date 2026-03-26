import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord)).all()
    for i in items:
        i.status = "pending_intake"
        i.item_mode = "single"
        i.needs_review = False
        i.review_reasons = None
        i.confidence_score = None
        i.title_raw = None
        i.title_final = None
        s.add(i)
    s.commit()
    print(f"Reset {len(items)} items to pending_intake")
