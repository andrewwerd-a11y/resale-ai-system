"""Integration tests for ItemRepository — all operations on in-memory SQLite."""
import pytest
from packages.data.src.repositories.item_repo import ItemRepository
from tests.fixtures.sample_items import make_clothing_item, make_book_item


# ── upsert / get ──────────────────────────────────────────────────────────────

def test_upsert_creates_new_record(test_session):
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000001")
    saved = repo.upsert(item)
    assert saved.sku == "CL-000001"


def test_upsert_updates_existing_record_by_sku(test_session):
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000001", title_final="Original")
    repo.upsert(item)

    updated = make_clothing_item(sku="CL-000001", title_final="Updated")
    repo.upsert(updated)

    fetched = repo.get_by_sku("CL-000001")
    assert fetched.title_final == "Updated"


def test_upsert_does_not_create_duplicate(test_session):
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000002")
    repo.upsert(item)
    repo.upsert(item)  # second upsert

    all_items = repo.get_all()
    cl_items = [i for i in all_items if i.sku == "CL-000002"]
    assert len(cl_items) == 1


def test_get_by_sku_returns_item(test_session):
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000003")
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000003")
    assert fetched is not None
    assert fetched.sku == "CL-000003"


def test_get_by_sku_returns_none_for_missing(test_session):
    repo = ItemRepository(test_session)
    assert repo.get_by_sku("CL-999999") is None


# ── manual_override preserves protected fields ────────────────────────────────

def test_upsert_with_manual_override_preserves_cost(test_session):
    repo = ItemRepository(test_session)
    # First upsert — set cost manually
    item = make_clothing_item(sku="CL-000004", cost=12.50, manual_override=True)
    repo.upsert(item)

    # Second upsert — cost is None (AI re-processed) but override=True
    re_processed = make_clothing_item(sku="CL-000004", cost=None, manual_override=True)
    repo.upsert(re_processed)

    fetched = repo.get_by_sku("CL-000004")
    assert fetched.cost == 12.50


def test_enrichment_done_flag_preserved(test_session):
    repo = ItemRepository(test_session)
    item = make_clothing_item(sku="CL-000005", enrichment_done=True, enrichment_notes="AI notes")
    repo.upsert(item)

    # Re-upsert without enrichment fields — they should not be cleared
    update = make_clothing_item(sku="CL-000005", enrichment_done=False, enrichment_notes=None)
    repo.upsert(update)

    fetched = repo.get_by_sku("CL-000005")
    assert fetched.enrichment_done is True
    assert fetched.enrichment_notes == "AI notes"


# ── list_by_status ────────────────────────────────────────────────────────────

def test_list_by_status_returns_correct_items(test_session):
    repo = ItemRepository(test_session)
    repo.upsert(make_clothing_item(sku="CL-000010", status="approved"))
    repo.upsert(make_clothing_item(sku="CL-000011", status="needs_review"))
    repo.upsert(make_clothing_item(sku="CL-000012", status="approved"))

    approved = repo.list_by_status("approved")
    assert len(approved) == 2
    assert all(i.status == "approved" for i in approved)


def test_list_by_status_returns_empty_for_missing_status(test_session):
    repo = ItemRepository(test_session)
    repo.upsert(make_clothing_item(sku="CL-000013", status="approved"))

    result = repo.list_by_status("sold")
    assert result == []


# ── list_needs_review ─────────────────────────────────────────────────────────

def test_list_needs_review_returns_flagged_items(test_session):
    repo = ItemRepository(test_session)
    repo.upsert(make_clothing_item(sku="CL-000020", needs_review=True, status="needs_review"))
    repo.upsert(make_clothing_item(sku="CL-000021", needs_review=False, status="approved"))

    flagged = repo.list_needs_review()
    assert len(flagged) == 1
    assert flagged[0].sku == "CL-000020"


# ── update_status ─────────────────────────────────────────────────────────────

def test_update_status_changes_status(test_session):
    repo = ItemRepository(test_session)
    repo.upsert(make_clothing_item(sku="CL-000030", status="approved"))

    changed = repo.update_status("CL-000030", "listed")
    assert changed is True

    fetched = repo.get_by_sku("CL-000030")
    assert fetched.status == "listed"


def test_update_status_returns_false_for_missing_sku(test_session):
    repo = ItemRepository(test_session)
    result = repo.update_status("CL-999999", "sold")
    assert result is False


# ── count_by_status ───────────────────────────────────────────────────────────

def test_count_by_status_correct_counts(test_session):
    repo = ItemRepository(test_session)
    for i in range(3):
        repo.upsert(make_clothing_item(sku=f"CL-0000{40+i}", status="approved"))
    for i in range(2):
        repo.upsert(make_clothing_item(sku=f"CL-0000{50+i}", status="needs_review"))

    counts = repo.count_by_status()
    assert counts.get("approved", 0) == 3
    assert counts.get("needs_review", 0) == 2


# ── round-trip serialisation ──────────────────────────────────────────────────

def test_image_paths_round_trip(test_session):
    repo = ItemRepository(test_session)
    paths = ["intake/CL-000060/01.jpg", "intake/CL-000060/02.jpg"]
    item = make_clothing_item(sku="CL-000060", image_paths=paths)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000060")
    assert fetched.image_paths == paths


def test_review_reasons_round_trip(test_session):
    repo = ItemRepository(test_session)
    reasons = ["low_confidence", "unclear_brand"]
    item = make_clothing_item(sku="CL-000061", review_reasons=reasons, needs_review=True)
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000061")
    assert fetched.review_reasons == reasons


def test_measurements_round_trip(test_session):
    from packages.domain.src.entities.item import Measurements
    repo = ItemRepository(test_session)
    item = make_clothing_item(
        sku="CL-000062",
        measurements=Measurements(chest_in=42.0, length_in=28.0),
    )
    repo.upsert(item)

    fetched = repo.get_by_sku("CL-000062")
    assert fetched.measurements.chest_in == 42.0
    assert fetched.measurements.length_in == 28.0
