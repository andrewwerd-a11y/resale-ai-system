"""Unit tests for FolderScanner."""
import pytest
from pathlib import Path
from packages.intake.src.folder_scanner import FolderScanner, FolderManifest


@pytest.fixture
def scanner():
    return FolderScanner()


def _make_item_folder(base: Path, name: str, images: list[str] = None) -> Path:
    """Helper: create a folder with optional image files."""
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    for img_name in (images or []):
        (folder / img_name).write_bytes(b"fake_image")
    return folder


# ── SKU detection ─────────────────────────────────────────────────────────────

def test_valid_sku_folder_detected(scanner, tmp_path):
    _make_item_folder(tmp_path, "BK-000001", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    assert len(manifests) == 1
    assert manifests[0].detected_sku == "BK-000001"


def test_invalid_folder_name_ignored(scanner, tmp_path):
    _make_item_folder(tmp_path, "random_folder", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    # No SKU detected for the random folder
    for m in manifests:
        assert m.detected_sku is None


def test_temp_id_folder_no_sku_assigned(scanner, tmp_path):
    _make_item_folder(tmp_path, "TEMP_20260101_001", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    # Either not found or detected_sku is None
    valid = [m for m in manifests if m.detected_sku is not None]
    assert len(valid) == 0


def test_unknown_prefix_folder_name_has_no_sku(scanner, tmp_path):
    _make_item_folder(tmp_path, "ZZ-000001", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    # ZZ is not a valid prefix
    assert all(m.detected_sku is None for m in manifests)


# ── image counting ────────────────────────────────────────────────────────────

def test_image_count_correct(scanner, tmp_path):
    _make_item_folder(tmp_path, "CL-000001", ["01.jpg", "02.jpg", "03.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    cl = next((m for m in manifests if m.detected_sku == "CL-000001"), None)
    assert cl is not None
    assert cl.image_count == 3


def test_non_image_files_not_counted(scanner, tmp_path):
    folder = _make_item_folder(tmp_path, "CL-000002", ["01.jpg"])
    (folder / "description.txt").write_text("desc")
    manifests = scanner.scan_existing(tmp_path)
    cl = next((m for m in manifests if m.detected_sku == "CL-000002"), None)
    assert cl.image_count == 1


# ── validity ──────────────────────────────────────────────────────────────────

def test_folder_with_no_images_marked_invalid(scanner, tmp_path):
    _make_item_folder(tmp_path, "CL-000003", [])
    manifests = scanner.scan_existing(tmp_path)
    cl = next((m for m in manifests if m.folder_name == "CL-000003"), None)
    assert cl is not None
    assert cl.is_valid is False
    assert "no_images_found" in cl.errors


def test_folder_with_images_is_valid(scanner, tmp_path):
    _make_item_folder(tmp_path, "CL-000004", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    cl = next((m for m in manifests if m.detected_sku == "CL-000004"), None)
    assert cl.is_valid is True


# ── category prefix detection ─────────────────────────────────────────────────

def test_cl_prefix_detected(scanner, tmp_path):
    _make_item_folder(tmp_path, "CL-000005", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    m = next(m for m in manifests if m.detected_sku == "CL-000005")
    assert m.detected_prefix == "CL"


def test_bk_prefix_detected(scanner, tmp_path):
    _make_item_folder(tmp_path, "BK-000002", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    m = next(m for m in manifests if m.detected_sku == "BK-000002")
    assert m.detected_prefix == "BK"


def test_detected_number_correct(scanner, tmp_path):
    _make_item_folder(tmp_path, "CL-000007", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    m = next(m for m in manifests if m.detected_sku == "CL-000007")
    assert m.detected_number == 7


# ── nested category subfolder scanning ───────────────────────────────────────

def test_nested_category_folder_scanned(scanner, tmp_path):
    # BK/BK-000001/ layout
    nested = tmp_path / "BK"
    _make_item_folder(nested, "BK-000001", ["01.jpg"])
    manifests = scanner.scan_existing(tmp_path)
    found = [m for m in manifests if m.detected_sku == "BK-000001"]
    assert len(found) == 1


# ── multiple formats ──────────────────────────────────────────────────────────

def test_multiple_image_extensions_counted(scanner, tmp_path):
    _make_item_folder(
        tmp_path, "CO-000001",
        ["01.jpg", "02.jpeg", "03.png", "04.webp"],
    )
    manifests = scanner.scan_existing(tmp_path)
    m = next(m for m in manifests if m.detected_sku == "CO-000001")
    assert m.image_count == 4
