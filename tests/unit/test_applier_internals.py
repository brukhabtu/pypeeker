"""Unit tests for TransactionApplier internal methods."""

import pytest

from pypeeker.models.transaction import EditEntry
from pypeeker.refactor.applier import ApplyError, TransactionApplier

pytestmark = pytest.mark.unit


class TestApplyEditsToContent:
    def test_single_edit(self):
        content = b"def foo():\n    pass\n"
        edits = [
            EditEntry(file="test.py", start=4, end=7, old="foo", new="bar", file_hash="h"),
        ]
        result = TransactionApplier._apply_edits_to_content(content, edits)
        assert result == b"def bar():\n    pass\n"

    def test_multiple_edits_bottom_to_top(self):
        content = b"foo foo foo\n"
        edits = [
            EditEntry(file="test.py", start=0, end=3, old="foo", new="bar", file_hash="h"),
            EditEntry(file="test.py", start=4, end=7, old="foo", new="bar", file_hash="h"),
            EditEntry(file="test.py", start=8, end=11, old="foo", new="bar", file_hash="h"),
        ]
        result = TransactionApplier._apply_edits_to_content(content, edits)
        assert result == b"bar bar bar\n"

    def test_edits_with_different_lengths(self):
        content = b"x = 1\n"
        edits = [
            EditEntry(file="test.py", start=0, end=1, old="x", new="longer_name", file_hash="h"),
        ]
        result = TransactionApplier._apply_edits_to_content(content, edits)
        assert result == b"longer_name = 1\n"

    def test_empty_edits(self):
        content = b"unchanged\n"
        result = TransactionApplier._apply_edits_to_content(content, [])
        assert result == b"unchanged\n"

    def test_content_mismatch_raises(self):
        content = b"hello world\n"
        edits = [
            EditEntry(file="test.py", start=0, end=5, old="wrong", new="bar", file_hash="h"),
        ]
        with pytest.raises(ApplyError, match="Content mismatch"):
            TransactionApplier._apply_edits_to_content(content, edits)

    def test_edits_applied_in_reverse_order(self):
        """Verify edits are sorted bottom-to-top to preserve offsets."""
        content = b"ab\n"
        # Provide edits in forward order — method should sort them reversed
        edits = [
            EditEntry(file="test.py", start=0, end=1, old="a", new="xx", file_hash="h"),
            EditEntry(file="test.py", start=1, end=2, old="b", new="yy", file_hash="h"),
        ]
        result = TransactionApplier._apply_edits_to_content(content, edits)
        assert result == b"xxyy\n"

    def test_utf8_content(self):
        content = "def café():\n    pass\n".encode("utf-8")
        # "café" starts at byte 4, é is 2 bytes in UTF-8
        # So "café" is bytes 4..9 (5 bytes: c=1, a=1, f=1, é=2)
        edits = [
            EditEntry(file="test.py", start=4, end=9, old="café", new="bar", file_hash="h"),
        ]
        result = TransactionApplier._apply_edits_to_content(content, edits)
        assert result == b"def bar():\n    pass\n"
