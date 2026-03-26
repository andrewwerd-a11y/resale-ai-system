import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord)).all()
    fixed = 0
    for item in items:
        if item.image_paths and '.jpeg' in item.image_paths:
            item.image_paths = item.image_paths.replace('.jpeg', '.jpg')
            s.add(item)
            fixed += 1
    s.commit()
    print(f"Fixed {fixed} items")
