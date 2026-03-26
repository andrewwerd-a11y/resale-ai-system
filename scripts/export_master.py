import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session
from packages.data.src.db.sqlite import engine
from packages.data.src.repositories.item_repo import ItemRepository
from packages.spreadsheet.src.master_sheet import MasterSheetWriter

with Session(engine) as s:
    items = ItemRepository(s).get_all()
    path = MasterSheetWriter().write(items)
    print(f"Master sheet: {path}")
