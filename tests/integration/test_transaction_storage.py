"""Tests for transaction storage in IndexStore."""

import json

import pytest

from pypeeker.models.transaction import EditEntry, TransactionHeader, TransactionStatus
from pypeeker.storage.store import IndexStore

pytestmark = pytest.mark.integration


def test_save_and_load_transaction(project_dir):
    store = IndexStore(project_dir)
    header = TransactionHeader(
        tx_id="test123",
        symbol_id="test.py:foo",
        old_name="foo",
        new_name="bar",
        created_at="2025-01-01T00:00:00+00:00",
    )
    edits = [
        EditEntry(file="test.py", start=0, end=3, old="foo", new="bar", file_hash="h1"),
        EditEntry(file="test.py", start=20, end=23, old="foo", new="bar", file_hash="h1"),
    ]

    store.save_transaction(header, edits)
    result = store.load_transaction("test123")

    assert result is not None
    loaded_header, loaded_edits, loaded_file_rename = result
    assert loaded_header.tx_id == "test123"
    assert loaded_header.symbol_id == "test.py:foo"
    assert loaded_header.status == TransactionStatus.PENDING
    assert len(loaded_edits) == 2
    assert loaded_edits[0].start == 0
    assert loaded_edits[1].start == 20
    assert loaded_file_rename is None


def test_load_nonexistent_transaction(project_dir):
    store = IndexStore(project_dir)
    result = store.load_transaction("nonexistent")
    assert result is None


def test_list_transactions(project_dir):
    store = IndexStore(project_dir)

    # Initially empty
    assert store.list_transactions() == []

    # Add some transactions
    for tx_id in ["tx_c", "tx_a", "tx_b"]:
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id="test.py:foo",
            old_name="foo",
            new_name="bar",
            created_at="2025-01-01T00:00:00+00:00",
        )
        store.save_transaction(header, [])

    # Should be sorted
    assert store.list_transactions() == ["tx_a", "tx_b", "tx_c"]


def test_remove_transaction(project_dir):
    store = IndexStore(project_dir)
    header = TransactionHeader(
        tx_id="to_remove",
        symbol_id="test.py:foo",
        old_name="foo",
        new_name="bar",
        created_at="2025-01-01T00:00:00+00:00",
    )
    store.save_transaction(header, [])
    assert store.load_transaction("to_remove") is not None

    store.remove_transaction("to_remove")
    assert store.load_transaction("to_remove") is None


def test_remove_nonexistent_transaction(project_dir):
    store = IndexStore(project_dir)
    # Should not raise
    store.remove_transaction("does_not_exist")


def test_jsonl_format(project_dir):
    store = IndexStore(project_dir)
    header = TransactionHeader(
        tx_id="jsonl_test",
        symbol_id="test.py:foo",
        old_name="foo",
        new_name="bar",
        created_at="2025-01-01T00:00:00+00:00",
    )
    edits = [
        EditEntry(file="test.py", start=0, end=3, old="foo", new="bar", file_hash="h"),
    ]
    store.save_transaction(header, edits)

    # Read raw file and verify JSONL format
    tx_path = store.transactions_root / "jsonl_test.jsonl"
    lines = tx_path.read_text().strip().split("\n")
    assert len(lines) == 2

    # Each line should be valid JSON
    header_data = json.loads(lines[0])
    assert header_data["tx_id"] == "jsonl_test"

    edit_data = json.loads(lines[1])
    assert edit_data["file"] == "test.py"
    assert edit_data["start"] == 0


def test_transactions_root_property(project_dir):
    store = IndexStore(project_dir)
    assert store.transactions_root == project_dir / ".semantic-tool" / "transactions"
