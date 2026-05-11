"""Tests for transaction models."""

from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    TransactionHeader,
    TransactionStatus,
    TransactionSummary,
)
from pypeeker.models.serialize import from_json, to_dict, to_json


def test_edit_entry_roundtrip():
    entry = EditEntry(
        file="src/auth/service.py",
        start=145,
        end=152,
        old="old_name",
        new="new_name",
        file_hash="abc123",
    )
    json_str = to_json(entry)
    loaded = from_json(EditEntry, json_str)
    assert loaded == entry
    assert loaded.op == EditOp.REPLACE


def test_transaction_header_roundtrip():
    header = TransactionHeader(
        tx_id="abc123def456",
        symbol_id="src/service.py:MyClass",
        old_name="MyClass",
        new_name="NewClass",
        created_at="2025-01-01T00:00:00+00:00",
    )
    json_str = to_json(header)
    loaded = from_json(TransactionHeader, json_str)
    assert loaded == header
    assert loaded.status == TransactionStatus.PENDING
    assert loaded.operation == "rename"


def test_transaction_summary_roundtrip():
    summary = TransactionSummary(
        tx_id="abc123def456",
        operation="rename",
        symbol_id="src/service.py:MyClass",
        old_name="MyClass",
        new_name="NewClass",
        files_affected=["src/service.py", "src/main.py"],
        edit_count=5,
        created_at="2025-01-01T00:00:00+00:00",
    )
    json_str = to_json(summary)
    loaded = from_json(TransactionSummary, json_str)
    assert loaded == summary


def test_edit_op_serialization():
    entry = EditEntry(
        file="test.py", start=0, end=3, old="foo", new="bar", file_hash="h"
    )
    data = to_dict(entry)
    assert data["op"] == "replace"


def test_transaction_status_values():
    assert TransactionStatus.PENDING.value == "pending"
    assert TransactionStatus.APPLIED.value == "applied"
    assert TransactionStatus.FAILED.value == "failed"
    assert TransactionStatus.ROLLED_BACK.value == "rolled_back"


def test_header_with_flags():
    header = TransactionHeader(
        tx_id="test",
        symbol_id="test.py:foo",
        old_name="foo",
        new_name="bar",
        created_at="2025-01-01T00:00:00+00:00",
        include_file=True,
        include_exports=True,
    )
    assert header.include_file is True
    assert header.include_exports is True
