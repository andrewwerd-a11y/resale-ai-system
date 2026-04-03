"""
Integration tests for the intake pipeline.
Uses real in-memory DB and real temp folders; mocks only external calls.
"""
import pytest
from pathlib import Path
from packages.data.src.repositories.item_repo import ItemRepository
from packages.sku.src.registry import SKURegistry
from packages.intake.src.folder_scanner import FolderScanner
from packages.domain.src.entities.item import Item
from packages.core.src.constants import ItemStatus


def _make_item_folder(base: Path, name: str, images: list[str] | None = None) -> Path:
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    for img_name in (["01.jpg"] if images is None else images):
        (folder / img_name).write_bytes(b"fake_image")
    return folder


# ── scan → DB record ──────────────────────────────────────────────────────────

def test_full_flow_scan_creates_db_record(test_session, tmp_path):
    """Scan a folder, register SKU, save to DB."""
    _make_item_folder(tmp_path, "CL-000001", ["01.jpg", "02.jpg"])

    scanner = FolderScanner()
    manifests = scanner.scan_existing(tmp_path)
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.detected_sku == "CL-000001"
    assert manifest.image_count == 2

    # Register SKU and save
    registry = SKURegistry(test_session)
    registry.preserve_existing_sku("CL-000001")

    repo = ItemRepository(test_session)
    item = Item(
        sku="CL-000001",
        status=ItemStatus.PENDING_INTAKE,
        category_key="clothing",
        image_paths=[str(p) for p in manifest.image_paths],
        photo_folder=str(manifest.folder_path),
    )
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000001")
    assert fetched is not None
    assert fetched.sku == "CL-000001"
    assert fetched.status == ItemStatus.PENDING_INTAKE
    assert len(fetched.image_paths) == 2


def test_idempotent_scan_creates_only_one_record(test_session, tmp_path):
    """Scanning the same folder twice must not create duplicate DB records."""
    _make_item_folder(tmp_path, "CL-000002", ["01.jpg"])

    scanner = FolderScanner()
    repo = ItemRepository(test_session)

    for _ in range(2):
        manifests = scanner.scan_existing(tmp_path)
        for m in manifests:
            if m.detected_sku:
                item = Item(
                    sku=m.detected_sku,
                    status=ItemStatus.PENDING_INTAKE,
                    image_paths=[str(p) for p in m.image_paths],
                )
                repo.upsert(item)

    all_items = [i for i in repo.get_all() if i.sku == "CL-000002"]
    assert len(all_items) == 1


def test_sku_preserved_from_folder_name(test_session, tmp_path):
    """A folder named CL-000007 must produce SKU=CL-000007, not CL-000001."""
    _make_item_folder(tmp_path, "CL-000007", ["01.jpg"])

    scanner = FolderScanner()
    manifests = scanner.scan_existing(tmp_path)
    m = manifests[0]
    assert m.detected_sku == "CL-000007"

    registry = SKURegistry(test_session)
    registry.preserve_existing_sku("CL-000007")

    repo = ItemRepository(test_session)
    item = Item(sku=m.detected_sku, status=ItemStatus.PENDING_INTAKE)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000007")
    assert fetched.sku == "CL-000007"


def test_image_paths_stored_correctly(test_session, tmp_path):
    """Image paths stored in DB should match the actual file paths from the scan."""
    folder = _make_item_folder(tmp_path, "CL-000003", ["01.jpg", "02.jpg", "03.jpg"])

    scanner = FolderScanner()
    manifests = scanner.scan_existing(tmp_path)
    m = manifests[0]

    repo = ItemRepository(test_session)
    item = Item(
        sku="CL-000003",
        status=ItemStatus.PENDING_INTAKE,
        image_paths=[str(p) for p in m.image_paths],
    )
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000003")
    # Pipe-separated storage and round-trip back to list
    assert len(fetched.image_paths) == 3
    for path in fetched.image_paths:
        assert Path(path).name.endswith(".jpg")


def test_folder_with_no_images_is_invalid(tmp_path):
    """A folder with no images should be flagged invalid and not processed."""
    # Create folder with no images (pass empty list explicitly)
    folder = tmp_path / "CL-000004"
    folder.mkdir()

    scanner = FolderScanner()
    # Use _build_manifest directly to inspect a specific folder
    m = scanner._build_manifest(folder)
    assert m.is_valid is False
    assert "no_images_found" in m.errors


def test_new_sku_reserved_for_unnumbered_folder(test_session, tmp_path):
    """An unnumbered temp folder must get the next available SKU from the registry."""
    folder = tmp_path / "TEMP_001"
    folder.mkdir()
    (folder / "01.jpg").write_bytes(b"x")

    scanner = FolderScanner()
    # _build_manifest inspects any folder regardless of name
    m = scanner._build_manifest(folder)
    # No detected SKU for temp-named folder
    assert m.detected_sku is None

    registry = SKURegistry(test_session)
    result = registry.reserve("CL")
    assert result.ok
    assert result.value == "CL-000001"


def test_multiple_categories_scanned_independently(test_session, tmp_path):
    """CL and BK items in the same folder are scanned and stored independently."""
    _make_item_folder(tmp_path, "CL-000010", ["01.jpg"])
    _make_item_folder(tmp_path, "BK-000010", ["01.jpg"])

    scanner = FolderScanner()
    manifests = scanner.scan_existing(tmp_path)
    assert len(manifests) == 2

    prefixes = {m.detected_prefix for m in manifests if m.detected_prefix}
    assert "CL" in prefixes
    assert "BK" in prefixes
