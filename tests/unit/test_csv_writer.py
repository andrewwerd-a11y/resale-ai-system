"""Unit tests for EbayCSVWriter."""
import csv
import pytest
from pathlib import Path
from packages.ebay.src.csv_writer import EbayCSVWriter, CLOTHING_ONLY_FIELDS
from tests.fixtures.sample_items import make_clothing_item, make_book_item


@pytest.fixture
def writer():
    return EbayCSVWriter()


# ── clothing fields ───────────────────────────────────────────────────────────

def test_clothing_fields_present_for_clothing_item(writer):
    item = make_clothing_item(size="L", department="Men", material="Cotton", style="Casual")
    rows = writer.preview([item])
    row = rows[0]
    assert row.get("Size") == "L"
    assert row.get("Department") == "Men"


def test_clothing_fields_stripped_for_book_item(writer):
    item = make_book_item()
    rows = writer.preview([item])
    row = rows[0]
    for field in CLOTHING_ONLY_FIELDS:
        assert row.get(field) in (None, ""), f"Field {field} should be absent for books"


def test_clothing_fields_stripped_for_toy_item(writer):
    from tests.fixtures.sample_items import make_toy_item
    item = make_toy_item()
    rows = writer.preview([item])
    row = rows[0]
    assert row.get("Size") in (None, "")
    assert row.get("Department") in (None, "")


# ── photo URL validation ──────────────────────────────────────────────────────

def test_photo_url_excludes_directory_paths(writer, tmp_path):
    # Create a real directory — the writer must skip it
    dir_path = tmp_path / "photos"
    dir_path.mkdir()
    item = make_clothing_item(image_paths=[str(dir_path)])
    rows = writer.preview([item])
    row = rows[0]
    assert row.get("Photo URL 1") in (None, "")


def test_photo_url_includes_existing_file(writer, tmp_path):
    img = tmp_path / "01.jpg"
    img.write_bytes(b"fake_image_data")
    item = make_clothing_item(image_paths=[str(img)])
    rows = writer.preview([item])
    row = rows[0]
    assert row.get("Photo URL 1") == str(img)


def test_photo_url_skips_nonexistent_file(writer, tmp_path):
    nonexistent = str(tmp_path / "ghost.jpg")
    item = make_clothing_item(image_paths=[nonexistent])
    rows = writer.preview([item])
    row = rows[0]
    assert row.get("Photo URL 1") in (None, "")


def test_photo_url_max_six_images(writer, tmp_path):
    imgs = []
    for i in range(8):
        p = tmp_path / f"{i:02d}.jpg"
        p.write_bytes(b"x")
        imgs.append(str(p))
    item = make_clothing_item(image_paths=imgs)
    rows = writer.preview([item])
    row = rows[0]
    # Photo URL 7 and 8 should not be present (only 1–6)
    assert row.get("Photo URL 7") in (None, "")


# ── title cleaning ────────────────────────────────────────────────────────────

def test_title_cleaned_of_ebay_suffix(writer):
    item = make_clothing_item(
        title_final="Nike Jacket Blue L for Resale on eBay"
    )
    rows = writer.preview([item])
    assert "for Resale on eBay" not in rows[0]["Title"]


def test_title_cleaned_of_for_sale_suffix(writer):
    item = make_clothing_item(title_final="Nike Jacket for Sale on eBay")
    rows = writer.preview([item])
    assert "for Sale on eBay" not in rows[0]["Title"]


def test_title_truncated_to_80_chars(writer):
    item = make_clothing_item(title_final="A" * 100)
    rows = writer.preview([item])
    assert len(rows[0]["Title"]) <= 80


# ── required eBay columns ─────────────────────────────────────────────────────

def test_all_required_columns_present(writer):
    item = make_clothing_item()
    rows = writer.preview([item])
    required = ["Custom label (SKU)", "Title", "Price", "Condition ID"]
    for col in required:
        assert col in rows[0], f"Missing required column: {col}"


def test_sku_in_custom_label_column(writer):
    item = make_clothing_item(sku="CL-000007")
    rows = writer.preview([item])
    assert rows[0]["Custom label (SKU)"] == "CL-000007"


# ── price formatting ──────────────────────────────────────────────────────────

def test_price_formatted_as_string(writer):
    item = make_clothing_item(list_price=24.99)
    rows = writer.preview([item])
    price = rows[0]["Price"]
    # Must be a string representation
    assert isinstance(price, str)
    assert "24.99" in price


def test_price_fallback_to_estimated(writer):
    item = make_clothing_item(list_price=None, estimated_price=18.00)
    rows = writer.preview([item])
    assert "18" in rows[0]["Price"]


# ── CSV file output ───────────────────────────────────────────────────────────

def test_write_creates_file(writer, tmp_path):
    item = make_clothing_item()
    out_path = tmp_path / "test_export.csv"
    writer.write([item], output_path=out_path)
    assert out_path.exists()


def test_write_csv_has_header_row(writer, tmp_path):
    item = make_clothing_item()
    out_path = tmp_path / "test_export.csv"
    writer.write([item], output_path=out_path)
    with open(out_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 1
    assert "Custom label (SKU)" in rows[0]
