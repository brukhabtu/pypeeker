"""Tests for the planner plumbing shared through ``refactor.plan_support``.

Covers the line-start arithmetic now sourced from ``text_anchor`` alone (the
newline-terminated-file case the deleted per-planner copies differed on),
the ``persist`` counting rule, and the store-side unindexed-file walk that
replaced the planner layer's only direct filesystem access.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from typing import ClassVar

import pytest

from pypeeker.intents import EMPTY_EFFECT, EMPTY_FOOTPRINT, Effect, Footprint, Intent
from pypeeker.models import EditEntry, EditOp
from pypeeker.refactor import (
    DeleteSymbolError,
    DocstringParamRenameError,
    ExtractMethodError,
    ExtractVariableError,
    InlineVariableError,
    MoveSymbolError,
    RemoveImportError,
    RenamePlanError,
    ReplaceTextError,
    RewriteStarImportError,
    TuplifyError,
    get_materializer,
    registry,
)
from pypeeker.refactor.extract import _physical_lines
from pypeeker.refactor.plan_support import PlanRefused, persist, simple_materializer
from pypeeker.refactor.planner import _position_to_byte_offset
from pypeeker.refactor.text_anchor import (
    line_end,
    line_start_offsets,
    line_stop,
    position_to_byte_offset,
)
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore


def _sentinel_line_starts(source: bytes) -> list[int]:
    """The variant ``inline._line_start_bytes`` used: a trailing offset past the end."""
    offsets = [0]
    for i, byte in enumerate(source):
        if byte == 0x0A:
            offsets.append(i + 1)
    return offsets


class TestLineStartOffsets:
    @pytest.mark.parametrize(
        "content",
        [b"a = 1\nb = 2\nc = 3\n", b"a = 1\nb = 2\nc = 3", b"x\n", b"x", b"", b"\n\n"],
    )
    def test_span_end_matches_sentinel_variant_on_every_line(self, content):
        canonical = line_start_offsets(content)
        sentinel = _sentinel_line_starts(content)
        for end_line in range(len(canonical)):
            assert line_stop(canonical, content, end_line) == line_stop(
                sentinel, content, end_line
            )

    def test_newline_terminated_file_has_one_offset_per_line(self):
        assert line_start_offsets(b"a\nb\n") == [0, 2]
        assert line_start_offsets(b"a\nb") == [0, 2]

    def test_physical_lines_match_offsets(self):
        content = b"def f():\n    return 1\n"
        lines = _physical_lines(content.decode("utf-8"))
        starts = line_start_offsets(content)
        assert len(lines) == len(starts)
        for start, line in zip(starts, lines):
            assert content[start : start + len(line.encode("utf-8"))] == line.encode("utf-8")

    def test_physical_lines_ignores_exotic_separators(self):
        # str.splitlines would split on the form feed; the byte offsets never do.
        assert _physical_lines("a\x0cb\nc") == ["a\x0cb\n", "c"]
        assert _physical_lines("") == [""]


class TestLineStopAndLineEnd:
    """``line_stop`` includes the newline, ``line_end`` excludes it."""

    def test_interior_line_differs_by_the_newline(self):
        content = b"ab\ncd\n"
        starts = line_start_offsets(content)
        assert line_stop(starts, content, 0) == 3
        assert line_end(starts, content, 0) == 2
        assert content[starts[0] : line_stop(starts, content, 0)] == b"ab\n"
        assert content[starts[0] : line_end(starts, content, 0)] == b"ab"

    def test_last_line_with_trailing_newline(self):
        content = b"ab\ncd\n"
        starts = line_start_offsets(content)
        assert line_stop(starts, content, 1) == len(content)
        assert line_end(starts, content, 1) == len(content) - 1
        assert content[starts[1] : line_end(starts, content, 1)] == b"cd"

    def test_last_line_without_trailing_newline_agree(self):
        content = b"ab\ncd"
        starts = line_start_offsets(content)
        assert line_stop(starts, content, 1) == len(content)
        assert line_end(starts, content, 1) == len(content)

    def test_empty_content(self):
        assert line_stop([0], b"", 0) == 0
        assert line_end([0], b"", 0) == 0


class TestPlanRefused:
    """Every planner refusal is a ``PlanRefused`` carrying ``code``/``precondition``."""

    @pytest.mark.parametrize(
        "error_cls",
        [
            DeleteSymbolError,
            DocstringParamRenameError,
            RemoveImportError,
            ReplaceTextError,
            RewriteStarImportError,
            TuplifyError,
        ],
    )
    def test_coded_refusals_keep_their_constructor(self, error_cls):
        error = error_cls("stale-index", "index is stale", precondition="anchor-fresh")
        assert isinstance(error, PlanRefused)
        assert str(error) == "index is stale"
        assert (error.code, error.precondition) == ("stale-index", "anchor-fresh")
        assert error_cls("x", "msg").precondition is None

    @pytest.mark.parametrize(
        "error_cls",
        [
            ExtractMethodError,
            ExtractVariableError,
            InlineVariableError,
            MoveSymbolError,
            RenamePlanError,
        ],
    )
    def test_uncoded_refusals_carry_no_code(self, error_cls):
        error = error_cls("refused", precondition="name-differs")
        assert isinstance(error, PlanRefused)
        assert str(error) == "refused"
        assert (error.code, error.precondition) == (None, "name-differs")
        assert error_cls("refused").precondition is None


class TestSimpleMaterializer:
    def test_registers_under_the_intent_kind_and_maps_refusals(self, tmp_path):
        kind = "test-simple-materializer-kind"

        @dataclasses.dataclass(frozen=True)
        class _ProbeIntent(Intent):
            kind: ClassVar[str] = "test-simple-materializer-kind"

            def footprint(self, store) -> Footprint:
                return EMPTY_FOOTPRINT

            def predicted_effect(self, store) -> Effect:
                return EMPTY_EFFECT

            def remap(self, effect) -> Intent:
                return self

        class _Refuses(PlanRefused):
            pass

        class _Planner:
            def __init__(self, store, tx_store) -> None:
                pass

            def plan(self, intent_id: str) -> None:
                raise _Refuses(f"no {intent_id}", code="c", precondition="p")

        try:
            materializer = simple_materializer(
                _ProbeIntent, _Planner, lambda intent: (intent.intent_id,)
            )
            assert get_materializer(kind) is materializer
            outcome = materializer(_ProbeIntent("probe"), None, None)
            assert isinstance(outcome, str)
            assert (str(outcome), outcome.code, outcome.precondition) == ("no probe", "c", "p")
        finally:
            registry._REGISTRY.pop(kind, None)


class TestPositionToByteOffset:
    def test_anchor_form_returns_none_out_of_range(self):
        assert position_to_byte_offset(b"hello\n", 5, 0) is None
        assert position_to_byte_offset(b"hello\n", 0, 9) is None

    def test_planner_form_raises_out_of_range(self):
        with pytest.raises(ValueError, match="out of range"):
            _position_to_byte_offset(b"hello\n", 5, 0)
        assert _position_to_byte_offset(b"hello\nworld\n", 1, 2) == 8


class TestPersist:
    def test_edit_count_and_files_derived_from_edits(self, tmp_path):
        tx_store = TransactionStore(tmp_path)
        edits = [
            EditEntry(op=EditOp.REPLACE, file="b.py", start=0, end=1, old="x", new="y", file_hash="h"),
            EditEntry(op=EditOp.REPLACE, file="a.py", start=0, end=1, old="x", new="y", file_hash="h"),
            EditEntry(op=EditOp.DELETE, file="a.py", start=2, end=3, old="z", new="", file_hash="h"),
        ]
        summary = persist(tx_store, "op", "a:x", "x", "y", edits)
        assert summary.edit_count == 3
        assert summary.files_affected == ["a.py", "b.py"]
        assert summary.operation == "op"
        loaded = tx_store.load(summary.tx_id)
        assert loaded is not None
        assert loaded.header.operation == "op"
        assert len(loaded.edits) == 3


class TestIterUnindexedSourceFiles:
    def test_store_walk_skips_indexed_and_pruned(self, indexed_project):
        project_dir, store = indexed_project({"pkg/mod.py": "x = 1\n"})
        (project_dir / "tests").mkdir()
        (project_dir / "tests" / "test_mod.py").write_text("from pkg.mod import x\n")
        (project_dir / "notes.txt").write_text("x\n")
        (project_dir / ".venv").mkdir()
        (project_dir / ".venv" / "hidden.py").write_text("x\n")
        (project_dir / "build").mkdir()
        (project_dir / "build" / "gen.py").write_text("x\n")

        found = dict(store.iter_unindexed_source_files())
        assert found == {"tests/test_mod.py": b"from pkg.mod import x\n"}

    def test_overlay_filters_its_own_indexes_and_serves_overlaid_bytes(self, indexed_project):
        project_dir, store = indexed_project({"pkg/mod.py": "x = 1\n"})
        (project_dir / "loose.py").write_text("x\n")
        (project_dir / "other.py").write_text("y\n")
        overlay = OverlayIndexStore(store)
        base_index = store.load("pkg/mod.py")
        assert base_index is not None
        # Indexing "loose.py" in the overlay hides it; re-writing "other.py"
        # serves the simulated bytes; removing the base index for pkg/mod.py
        # brings it back into the unindexed set — all without touching disk.
        overlay.save(replace(base_index, file_path="loose.py"))
        overlay.write_file("other.py", b"y = 2\n")
        overlay.remove("pkg/mod.py")

        assert dict(overlay.iter_unindexed_source_files()) == {
            "other.py": b"y = 2\n",
            "pkg/mod.py": b"x = 1\n",
        }
        assert dict(store.iter_unindexed_source_files()) == {
            "loose.py": b"x\n",
            "other.py": b"y\n",
        }

    def test_plain_store_without_index_dir(self, tmp_path):
        (tmp_path / "a.py").write_text("a\n")
        assert dict(IndexStore(tmp_path).iter_unindexed_source_files()) == {"a.py": b"a\n"}


def test_overlay_yields_a_removed_unindexed_path_once(tmp_path):
    """``remove()`` records a path even if the base never indexed it; the
    survey must not serve that path from both the base walk and the
    removed-index pass."""
    from pypeeker.storage import IndexStore, OverlayIndexStore

    (tmp_path / "loose.py").write_bytes(b"loose = 1\n")
    base = IndexStore(tmp_path)
    overlay = OverlayIndexStore(base)
    overlay.remove("loose.py")
    assert [p for p, _ in overlay.iter_unindexed_source_files()] == ["loose.py"]
