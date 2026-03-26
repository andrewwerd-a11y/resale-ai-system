import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord

VALID_TRIGGERS = {
    "low_confidence", "missing_required_fields", "conflicting_extracted_values",
    "high_value_estimate", "unusual_defects", "antique", "signed", "inscribed",
    "first_edition", "rare_binding", "collectible_edition", "luxury_brand",
    "unclear_brand", "unclear_authenticity", "possible_counterfeit",
    "price_ambiguity", "rare", "vintage",
}

with Session(engine) as s:
    items = s.exec(select(ItemRecord).where(ItemRecord.needs_review == True)).all()
    approved = 0
    stayed = 0
    for item in items:
        try:
            reasons = json.loads(item.review_reasons) if item.review_reasons else []
        except:
            reasons = []

        # Keep only recognized trigger codes
        real_reasons = [r for r in reasons if r in VALID_TRIGGERS]

        if real_reasons:
            item.review_reasons = json.dumps(real_reasons)
            stayed += 1
        else:
            # No real triggers — approve it
            item.review_reasons = json.dumps([])
            item.needs_review = False
            item.status = "approved"
            approved += 1
        s.add(item)
    s.commit()
    print(f"Approved (noise cleared): {approved}")
    print(f"Stayed in review:         {stayed}")
