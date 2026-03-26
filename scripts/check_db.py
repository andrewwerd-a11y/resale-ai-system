from sqlmodel import Session, select
from collections import Counter
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord)).all()
    print(f"Total items in DB: {len(items)}")
    print()
    cats = Counter(i.category_key for i in items)
    for cat, count in sorted(cats.items()):
        print(f"  {cat}: {count}")
    print()
    statuses = Counter(i.status for i in items)
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")
    print()
    for item in items[:3]:
        print(f"  {item.sku} | {item.category_key} | {item.status}")
        print(f"  images: {item.image_paths}")
        print()