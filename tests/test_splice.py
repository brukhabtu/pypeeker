"""The shared byte-splice engine and its non-overlap invariant.

``refactor/splice.py`` is the one algorithm both the applier (bytes to disk)
and the batch overlay (bytes in memory) splice through; these tests pin the
engine itself, the overlap check that states its invariant, and the applier
pre-flight that refuses a transaction violating it.
"""

from __future__ import annotations

import pytest

from pypeeker.models import EditEntry, TransactionHeader, TransactionStatus
from pypeeker.refactor import ApplyError, TransactionApplier, spans_overlap
from pypeeker.refactor.splice import (
    OverlappingEdits,
    SpliceMismatch,
    assert_no_overlapping_edits,
    splice_edits,
)
from pypeeker.storage import IndexStore, TransactionStore


def _edit(start: int, end: int, old: str, new: str, file: str = "m.py") -> EditEntry:
    return EditEntry(file=file, start=start, end=end, old=old, new=new, file_hash="h")


class TestSpliceEdits:
    def test_applies_bottom_to_top_so_plan_time_offsets_stay_valid(self):
        content = b"aaa bbb ccc\n"
        edits = [_edit(0, 3, "aaa", "A"), _edit(4, 7, "bbb", "BBBBB"), _edit(8, 11, "ccc", "")]
        assert splice_edits(content, edits) == b"A BBBBB \n"

    def test_input_order_does_not_matter(self):
        content = b"aaa bbb ccc\n"
        edits = [_edit(8, 11, "ccc", ""), _edit(0, 3, "aaa", "A"), _edit(4, 7, "bbb", "BBBBB")]
        assert splice_edits(content, edits) == b"A BBBBB \n"

    def test_mismatched_old_text_raises_and_names_the_offset(self):
        with pytest.raises(SpliceMismatch, match="m.py at byte 4.*expected 'xxx'") as info:
            splice_edits(b"aaa bbb ccc\n", [_edit(4, 7, "xxx", "y")])
        assert info.value.actual == b"bbb"
        assert info.value.edit.start == 4

    def test_does_not_touch_the_input(self):
        content = b"aaa\n"
        splice_edits(content, [_edit(0, 3, "aaa", "b")])
        assert content == b"aaa\n"


class TestSpansOverlap:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ((0, 5), (5, 9)),  # adjacent
            ((5, 9), (0, 5)),  # adjacent, reversed
            ((0, 5), (5, 5)),  # zero-width insertion at the boundary
            ((5, 5), (5, 5)),  # two insertions at one offset
            ((0, 5), (7, 9)),  # disjoint
        ],
    )
    def test_touching_and_disjoint_spans_do_not_overlap(self, first, second):
        assert not spans_overlap(first, second)

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ((0, 5), (3, 9)),  # partial
            ((0, 9), (3, 5)),  # nested
            ((0, 5), (2, 2)),  # zero-width insertion strictly inside a replaced span
            ((0, 5), (0, 5)),  # identical
        ],
    )
    def test_intersecting_spans_overlap(self, first, second):
        assert spans_overlap(first, second)
        assert spans_overlap(second, first)


class TestOverlappingEdits:
    def test_disjoint_and_adjacent_edits_pass(self):
        edits = [_edit(0, 3, "aaa", "A"), _edit(3, 6, "bbb", "B"), _edit(9, 9, "", "!")]
        assert_no_overlapping_edits(edits)

    def test_same_span_in_different_files_is_not_an_overlap(self):
        edits = [_edit(0, 3, "aaa", "A", file="a.py"), _edit(0, 3, "aaa", "B", file="b.py")]
        assert_no_overlapping_edits(edits)

    def test_an_enclosing_edit_is_caught_against_a_later_nested_one(self):
        """Sorting by start alone would compare only neighbours; the sweep keeps
        the furthest-reaching span so ``[0, 100)`` still collides with ``[30, 40)``
        after the non-overlapping ``[10, 20)`` sat between them."""
        wide = _edit(0, 100, "w", "W")
        edits = [_edit(10, 20, "x", "X"), wide, _edit(30, 40, "y", "Y")]
        with pytest.raises(OverlappingEdits) as info:
            assert_no_overlapping_edits(edits)
        assert wide in (info.value.first, info.value.second)

    def test_assert_raises_with_both_spans_in_the_message(self):
        edits = [_edit(0, 5, "hello", "HELLO"), _edit(3, 8, "lo wo", "x")]
        with pytest.raises(OverlappingEdits, match="m.py: bytes 0-5 .* and bytes 3-8"):
            assert_no_overlapping_edits(edits)


class TestApplierRefusesOverlappingEdits:
    def test_overlapping_edits_are_refused_before_anything_is_touched(self, project_dir):
        store = IndexStore(project_dir)
        target = project_dir / "m.py"
        target.write_text("hello world\n")
        file_hash = IndexStore.compute_file_hash(target)
        header = TransactionHeader(
            tx_id="overlap_tx",
            symbol_id="m:x",
            old_name="hello",
            new_name="HELLO",
            created_at="2025-01-01T00:00:00+00:00",
        )
        edits = [
            EditEntry(file="m.py", start=0, end=5, old="hello", new="HELLO", file_hash=file_hash),
            EditEntry(file="m.py", start=3, end=8, old="lo wo", new="x", file_hash=file_hash),
        ]
        tx_store = TransactionStore(project_dir)
        tx_store.save(header, edits)

        with pytest.raises(ApplyError, match="overlapping edits in m.py"):
            TransactionApplier(store, tx_store).apply("overlap_tx")

        assert target.read_text() == "hello world\n"
        loaded = tx_store.load("overlap_tx")
        assert loaded is not None
        assert loaded.header.status == TransactionStatus.PENDING
        assert not list(project_dir.glob("*.tmp"))
