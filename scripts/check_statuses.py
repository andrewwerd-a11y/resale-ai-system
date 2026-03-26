import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import Counter
from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord)).all()
    counts = Counter(i.status for i in items)
    for status, count in sorted(counts.items()):
        print(f"  {count:3d}  {status!r}")
