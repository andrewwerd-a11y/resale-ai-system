from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord).where(ItemRecord.category_key == 'clothing')).all()
    for i in items[:5]:
        print(i.sku, '|', i.status)