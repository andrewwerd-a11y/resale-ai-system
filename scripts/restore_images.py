import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select
from packages.data.src.db.sqlite import engine
from packages.data.src.models.item_record import ItemRecord
from packages.intake.src.folder_scanner import FolderScanner

source = Path(r"C:\Users\Andrew\Desktop\reselling_system_template\Inventory_Photos")
scanner = FolderScanner()
manifests = scanner.scan_existing(source)

with Session(engine) as s:
    updated = 0
    for m in manifests:
        if not m.detected_sku or not m.image_paths:
            continue
        item = s.exec(select(ItemRecord).where(ItemRecord.sku == m.detected_sku)).first()
        if item:
            item.image_paths = "|".join(str(p) for p in m.image_paths)
            item.photo_folder = str(m.folder_path)
            s.add(item)
            updated += 1
    s.commit()
    print(f"Restored image paths for {updated} items")
