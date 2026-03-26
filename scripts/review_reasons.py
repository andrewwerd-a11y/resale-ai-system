import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from collections import Counter
from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

with Session(engine) as s:
    items = s.exec(select(ItemRecord).where(ItemRecord.needs_review == True)).all()
    reasons = []
    for item in items:
        if item.review_reasons:
            try:
                r = json.loads(item.review_reasons)
                reasons.extend(r)
            except:
                reasons.append(item.review_reasons)
    counts = Counter(reasons)
    print(f"Total in review: {len(items)}")
    print()
    for reason, count in counts.most_common():
        print(f"  {count:3d}  {reason}")
