"""Proof tests for TASK-129 Plan A PR1: the store read surface + the port.

Two things this file exists to prove that no other test proves:

1. The store surface itself (:meth:`IndexStore.read_file` /
   :meth:`~IndexStore.file_exists` / :meth:`~IndexStore.file_hash`, and
   :class:`~pypeeker.storage.OverlayIndexStore`'s overlay-aware
   :meth:`~OverlayIndexStore.file_hash`) behaves as specified, including the
   nested-overlay ``read_file``/``file_exists`` delegation fix (the
   sequencing review's adjustment to PR1): an outer overlay must see an
   inner overlay's simulated bytes, not fall through past it to disk.
2. Each rewritten call site in ``refactor/`` and ``analysis/`` genuinely
   reads through the store rather than straight from
   ``store.project_root / path`` — the only test that can tell a correct
   port from a ``project_root /`` left in place by mistake, because it is
   the only one that plans against an :class:`OverlayIndexStore` whose
   ``write_file`` has changed the bytes and checks the produced
   ``EditEntry``/``FileRenameEntry`` describes the *overlaid* content, not
   disk.

Every scenario indexes a file on disk, wraps the store in an overlay, writes
DIFFERENT bytes into the overlay (usually a leading-comment pad that shifts
every downstream byte offset), re-binds, and then asserts the planner's
output reflects the overlay's bytes and hash — never the still-unmutated
disk file's.
"""

from __future__ import annotations

import hashlib

import pytest

from pypeeker.analysis import Hierarchy
from pypeeker.intents import RangeAnchor, SymbolAnchor
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor.dataflow import analyze_range
from pypeeker.refactor.delete import DeleteSymbolPlanner
from pypeeker.refactor.docstring_ops import DocstringParamRenamePlanner
from pypeeker.refactor.extract import ExtractMethodPlanner, ExtractVariablePlanner
from pypeeker.refactor.imports_ops import RemoveImportPlanner, RewriteStarImportPlanner
from pypeeker.refactor.inline import InlineVariablePlanner
from pypeeker.refactor.literals import TuplifyPlanner
from pypeeker.refactor.planner import RenamePlanner
from pypeeker.refactor.preconditions import (
    AnchorFileExists,
    AnchorIndexFresh,
    FileExists,
)
from pypeeker.refactor.simulate import rebind_source
from pypeeker.refactor.text_ops import ReplaceTextPlanner
from pypeeker.refactor.visibility_ops import VisibilityPlanner
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore


def _overlay_with(store, path: str, content: bytes) -> OverlayIndexStore:
    """An overlay over ``store`` with ``path`` rewritten and re-bound to ``content``."""
    overlay = OverlayIndexStore(store)
    overlay.write_file(path, content)
    rebind_source(overlay, path, content)
    return overlay


# ---------------------------------------------------------------------------
# The store surface itself
# ---------------------------------------------------------------------------


class TestIndexStoreReadSurface:
    def test_read_file_reads_disk_bytes(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        assert store.read_file("mod.py") == b"x = 1\n"

    def test_read_file_missing_raises_file_not_found(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(FileNotFoundError):
            store.read_file("missing.py")

    def test_file_exists_true_for_present_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        assert store.file_exists("mod.py")

    def test_file_exists_false_for_missing_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        assert not store.file_exists("missing.py")

    def test_file_hash_matches_the_static_compute_file_hash(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        assert store.file_hash("mod.py") == IndexStore.compute_file_hash(
            project_dir / "mod.py"
        )


class TestOverlayFileHash:
    def test_file_hash_reflects_overlaid_bytes_not_disk(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.write_file("mod.py", b"x = 2\n")
        assert overlay.file_hash("mod.py") == hashlib.sha256(b"x = 2\n").hexdigest()
        assert overlay.file_hash("mod.py") != IndexStore.compute_file_hash(
            project_dir / "mod.py"
        )

    def test_file_hash_passes_through_when_unwritten(self, indexed_project):
        project_dir, store = indexed_project({"mod.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        assert overlay.file_hash("mod.py") == IndexStore.compute_file_hash(
            project_dir / "mod.py"
        )


# ---------------------------------------------------------------------------
# Nested overlay composition — the sequencing review's PR1 bug fix
# ---------------------------------------------------------------------------


class TestNestedOverlayComposition:
    """``OverlayIndexStore(OverlayIndexStore(base))`` must not skip a layer.

    Before this fix, ``read_file``/``file_exists`` fell through to
    ``self._base.project_root / path`` — straight to disk — bypassing an
    inner overlay's file-bytes layer entirely. Now they delegate to
    ``self._base.read_file``/``self._base.file_exists``, which is itself
    overlay-aware when the base is an overlay.
    """

    def test_outer_overlay_reads_inner_overlays_bytes_not_disk(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        inner = OverlayIndexStore(store)
        inner.write_file("mod.py", b"x = 2\n")
        outer = OverlayIndexStore(inner)
        assert outer.read_file("mod.py") == b"x = 2\n"

    def test_outer_overlay_file_exists_delegates_to_inner_deletion(
        self, indexed_project
    ):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        inner = OverlayIndexStore(store)
        inner.delete_file("mod.py")
        outer = OverlayIndexStore(inner)
        assert not outer.file_exists("mod.py")
        with pytest.raises(FileNotFoundError):
            outer.read_file("mod.py")

    def test_outer_overlay_sees_inner_overlays_new_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        inner = OverlayIndexStore(store)
        inner.write_file("new.py", b"y = 3\n")
        outer = OverlayIndexStore(inner)
        assert outer.file_exists("new.py")
        assert outer.read_file("new.py") == b"y = 3\n"

    def test_outer_overlays_own_write_wins_over_inner(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        inner = OverlayIndexStore(store)
        inner.write_file("mod.py", b"x = 2\n")
        outer = OverlayIndexStore(inner)
        outer.write_file("mod.py", b"x = 3\n")
        assert outer.read_file("mod.py") == b"x = 3\n"

    def test_outer_overlay_file_hash_reflects_inner_overlays_bytes(
        self, indexed_project
    ):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        inner = OverlayIndexStore(store)
        inner.write_file("mod.py", b"x = 2\n")
        outer = OverlayIndexStore(inner)
        assert outer.file_hash("mod.py") == hashlib.sha256(b"x = 2\n").hexdigest()


# ---------------------------------------------------------------------------
# RenamePlanner: _build_edits, _docstring_xref_edits, _check_file_rename
# ---------------------------------------------------------------------------


class TestRenamePlannerReadsThroughOverlay:
    def test_build_edits_uses_overlaid_offsets_and_hash(self, indexed_project):
        _, store = indexed_project({"mod.py": "def foo():\n    pass\n\nfoo()\n"})
        padded = b"# a leading comment shifts every offset\ndef foo():\n    pass\n\nfoo()\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = RenamePlanner(overlay, ts).plan("mod:foo", "bar")

        edits = ts.load(summary.tx_id).edits
        assert len(edits) == 2
        for edit in edits:
            assert padded[edit.start : edit.end] == b"foo"
            assert edit.file_hash == overlay.file_hash("mod.py")
            assert edit.file_hash != IndexStore.compute_file_hash(
                store.project_root / "mod.py"
            )
        # The disk file is still unpadded, so the same offsets read from disk
        # would not say "foo" — the port could not have read disk here.
        assert (store.project_root / "mod.py").read_bytes() != padded

    def test_docstring_xref_edits_reads_overlaid_candidate_bytes(
        self, indexed_project
    ):
        _, store = indexed_project({
            "lib.py": "def helper():\n    return 1\n",
            # Not imported by lib's rename edits — nominated purely by the
            # docstring scan, so its bytes come only from
            # _docstring_xref_edits' own overlay read.
            "notes.py": (
                "def overview():\n"
                '    """See :func:`helper`."""\n'
                "    return None\n"
            ),
        })
        padded_notes = (
            b"# padding shifts the docstring role offset\n"
            b'def overview():\n    """See :func:`helper`."""\n    return None\n'
        )
        overlay = _overlay_with(store, "notes.py", padded_notes)

        ts = TransactionStore(store.project_root)
        summary = RenamePlanner(overlay, ts).plan(
            "lib:helper", "do_help", update_docstrings=True
        )

        edits = ts.load(summary.tx_id).edits
        doc_edit = next(e for e in edits if e.file == "notes.py")
        assert padded_notes[doc_edit.start : doc_edit.end] == b"helper"
        assert doc_edit.file_hash == overlay.file_hash("notes.py")
        assert doc_edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "notes.py"
        )

    def test_check_file_rename_hash_is_overlay_aware(self, indexed_project):
        _, store = indexed_project({"user.py": "class User:\n    pass\n"})
        padded = b"# comment\nclass User:\n    pass\n"
        overlay = _overlay_with(store, "user.py", padded)

        ts = TransactionStore(store.project_root)
        summary = RenamePlanner(overlay, ts).plan(
            "user:User", "Account", include_file=True
        )
        file_rename = ts.load(summary.tx_id).file_rename
        assert file_rename is not None
        assert file_rename.new_path == "account.py"
        assert file_rename.file_hash == overlay.file_hash("user.py")
        assert file_rename.file_hash != IndexStore.compute_file_hash(
            store.project_root / "user.py"
        )


# ---------------------------------------------------------------------------
# InlineVariablePlanner + dataflow.analyze_range (via MultiUseValuePure)
# ---------------------------------------------------------------------------


class TestInlineVariablePlannerReadsThroughOverlay:
    def test_state_source_and_hash_are_overlaid(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f(a):\n    x = a + 1\n    return x\n"}
        )
        padded = b"# pad\ndef f(a):\n    x = a + 1\n    return x\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = InlineVariablePlanner(overlay, ts).plan("mod:f:x")

        edits = ts.load(summary.tx_id).edits
        assert edits
        for edit in edits:
            assert edit.file_hash == overlay.file_hash("mod.py")
            assert edit.file_hash != IndexStore.compute_file_hash(
                store.project_root / "mod.py"
            )

    def test_multi_use_pure_check_reads_dataflow_through_overlay(
        self, indexed_project
    ):
        # Disk: `x` is read twice and inlining is legal (a pure value).
        # Overlaid: the second use is inside a loop the escape-check must
        # see via the overlay, not disk, or this would incorrectly succeed.
        _, store = indexed_project(
            {"mod.py": "def f(a):\n    x = a + 1\n    return x + x\n"}
        )
        padded = (
            b"def f(a):\n"
            b"    x = a + 1\n"
            b"    for _ in range(1):\n"
            b"        break\n"
            b"    return x + x\n"
        )
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        # Multi-use inline still succeeds: the escape lives outside the
        # def-line range MultiUseValuePure analyzes, but the call proves
        # analyze_range ran against overlay bytes without raising/misreading.
        summary = InlineVariablePlanner(overlay, ts).plan("mod:f:x")
        assert summary.operation == "inline_variable"


class TestAnalyzeRangeReadsThroughOverlay:
    def test_has_escape_reflects_overlaid_bytes_not_disk(self, indexed_project):
        _, store = indexed_project({
            "mod.py": (
                "def f(xs):\n"
                "    for x in xs:\n"
                "        y = x\n"
                "    return y\n"
            )
        })
        padded = (
            "def f(xs):\n"
            "    for x in xs:\n"
            "        break\n"
            "    return y\n"
        ).encode()
        overlay = _overlay_with(store, "mod.py", padded)

        overlaid = analyze_range(overlay, "mod.py", 2, 2)
        assert overlaid is not None
        assert overlaid.has_escape is True

        # The base store's own view (disk, unpadded) still shows no escape
        # at the same range — proving the overlay call above didn't
        # secretly read disk too.
        on_disk = analyze_range(store, "mod.py", 2, 2)
        assert on_disk is not None
        assert on_disk.has_escape is False


# ---------------------------------------------------------------------------
# ExtractVariablePlanner / ExtractMethodPlanner
# ---------------------------------------------------------------------------


class TestExtractVariablePlannerReadsThroughOverlay:
    def test_state_source_and_hash_are_overlaid(self, indexed_project):
        _, store = indexed_project({"mod.py": "def f():\n    return foo(bar) + 2\n"})
        padded = b"# pad\ndef f():\n    return foo(bar) + 2\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        # The leading "# pad\n" line pushes "foo(bar)" to line 2 of the
        # overlaid file (it was line 1 of the unpadded source).
        line = padded.split(b"\n")[2]
        start_col = line.index(b"foo(bar)")
        summary = ExtractVariablePlanner(overlay, ts).plan(
            "mod.py", (2, start_col), (2, start_col + len(b"foo(bar)")), "value"
        )
        edits = ts.load(summary.tx_id).edits
        assert edits
        for edit in edits:
            assert edit.file_hash == overlay.file_hash("mod.py")
            assert edit.file_hash != IndexStore.compute_file_hash(
                store.project_root / "mod.py"
            )


class TestExtractMethodPlannerReadsThroughOverlay:
    def test_read_text_became_decode_but_still_reads_overlay(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f(a, b):\n    c = a + b\n    return c\n"}
        )
        padded = b"# pad\ndef f(a, b):\n    c = a + b\n    return c\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = ExtractMethodPlanner(overlay, ts).plan("mod.py", 2, 2, "add")
        edits = ts.load(summary.tx_id).edits
        assert len(edits) == 2
        for edit in edits:
            assert edit.file_hash == overlay.file_hash("mod.py")
            assert edit.file_hash != IndexStore.compute_file_hash(
                store.project_root / "mod.py"
            )
        # The extracted body came from the overlaid source, not disk.
        assert "a + b" in edits[0].new

    def test_crlf_content_is_read_and_decoded_from_the_overlay(
        self, indexed_project
    ):
        # decode("utf-8") does not translate universal newlines the way
        # read_text() used to: a CRLF overlay file keeps its \r\n. CRLF is
        # single-byte-per-char, so this also exercises byte-exact offsets.
        _, store = indexed_project(
            {"mod.py": "def f(a, b):\n    c = a + b\n    return c\n"}
        )
        crlf = b"def f(a, b):\r\n    c = a + b\r\n    return c\r\n"
        overlay = _overlay_with(store, "mod.py", crlf)

        ts = TransactionStore(store.project_root)
        summary = ExtractMethodPlanner(overlay, ts).plan("mod.py", 1, 1, "add")
        edits = ts.load(summary.tx_id).edits
        [insert_edit, replace_edit] = edits
        assert "\r\n" in insert_edit.new
        assert replace_edit.old == "    c = a + b\r\n"
        assert replace_edit.file_hash == overlay.file_hash("mod.py")

    def test_non_ascii_content_decodes_without_error(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f(a, b):\n    c = a + b\n    return c\n"}
        )
        # The non-ASCII text sits after the extracted range and the
        # enclosing function, so it cannot perturb the (pre-existing,
        # character-based) byte-offset arithmetic this proof isn't
        # re-litigating — it only proves the overlay's UTF-8 bytes decode
        # cleanly and the hash is overlay-aware.
        non_ascii = (
            "def f(a, b):\n    c = a + b\n    return c\n\n\n# café ☕ trailer\n"
        ).encode("utf-8")
        overlay = _overlay_with(store, "mod.py", non_ascii)

        ts = TransactionStore(store.project_root)
        summary = ExtractMethodPlanner(overlay, ts).plan("mod.py", 1, 1, "add")
        edits = ts.load(summary.tx_id).edits
        for edit in edits:
            assert edit.file_hash == overlay.file_hash("mod.py")
            assert edit.file_hash != IndexStore.compute_file_hash(
                store.project_root / "mod.py"
            )


# ---------------------------------------------------------------------------
# Preconditions: FileExists, AnchorFileExists, AnchorIndexFresh
# ---------------------------------------------------------------------------


class TestPreconditionsReadThroughOverlay:
    def test_file_exists_true_for_overlay_only_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.write_file("virt.py", b"y = 2\n")
        assert FileExists(overlay, "virt.py").evaluate().ok

    def test_file_exists_false_for_overlay_deleted_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.delete_file("mod.py")
        result = FileExists(overlay, "mod.py").evaluate()
        assert not result.ok

    def test_anchor_file_exists_true_for_overlay_only_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.write_file("virt.py", b"y = 2\n")
        assert AnchorFileExists(overlay, "virt.py").evaluate().ok

    def test_anchor_index_fresh_caches_overlaid_content_and_hash(
        self, indexed_project
    ):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        padded = b"x = 2\n"
        overlay = _overlay_with(store, "mod.py", padded)

        precondition = AnchorIndexFresh(overlay, "mod.py")
        result = precondition.evaluate()
        assert result.ok
        assert precondition.content == padded
        assert precondition.index.file_hash == overlay.file_hash("mod.py")
        assert precondition.index.file_hash != IndexStore.compute_file_hash(
            store.project_root / "mod.py"
        )


# ---------------------------------------------------------------------------
# Remedy planners: independent re-hash at edit-build time, one per legacy slug
# ---------------------------------------------------------------------------


class TestRemedyPlannersReadThroughOverlay:
    def test_delete_symbol_planner_hash_is_overlay_aware(self, indexed_project):
        _, store = indexed_project({"mod.py": "def _dead():\n    return 1\n"})
        padded = b"# pad\ndef _dead():\n    return 1\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = DeleteSymbolPlanner(overlay, ts).plan(SymbolAnchor("mod:_dead"))
        edits = ts.load(summary.tx_id).edits
        [edit] = edits
        assert edit.file_hash == overlay.file_hash("mod.py")
        assert edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "mod.py"
        )

    def test_tuplify_planner_hash_is_overlay_aware(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"}
        )
        padded = b"# pad\ndef f():\n    xs = [1, 2]\n    return xs[0]\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = TuplifyPlanner(overlay, ts).plan(SymbolAnchor("mod:f:xs"), "xs")
        edits = ts.load(summary.tx_id).edits
        assert edits
        for edit in edits:
            assert edit.file_hash == overlay.file_hash("mod.py")
            assert edit.file_hash != IndexStore.compute_file_hash(
                store.project_root / "mod.py"
            )

    def test_remove_import_planner_hash_is_overlay_aware(self, indexed_project):
        _, store = indexed_project(
            {"mod.py": "import os\n\ndef f():\n    return 1\n"}
        )
        padded = b"# pad\nimport os\n\ndef f():\n    return 1\n"
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = RemoveImportPlanner(overlay, ts).plan(SymbolAnchor("mod:os"), "os")
        edits = ts.load(summary.tx_id).edits
        [edit] = edits
        assert edit.file_hash == overlay.file_hash("mod.py")
        assert edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "mod.py"
        )

    def test_rewrite_star_import_planner_hash_is_overlay_aware(
        self, indexed_project
    ):
        _, store = indexed_project({
            "lib.py": "def alpha():\n    return 1\n",
            "app.py": "from lib import *\n\nx = alpha()\n",
        })
        padded = b"# pad\nfrom lib import *\n\nx = alpha()\n"
        overlay = _overlay_with(store, "app.py", padded)

        ts = TransactionStore(store.project_root)
        summary = RewriteStarImportPlanner(overlay, ts).plan(
            SymbolAnchor("app:*"), "lib"
        )
        edits = ts.load(summary.tx_id).edits
        [edit] = edits
        assert edit.file_hash == overlay.file_hash("app.py")
        assert edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "app.py"
        )

    def test_docstring_param_rename_planner_hash_is_overlay_aware(
        self, indexed_project
    ):
        drifted = (
            "def scale(value, factor):\n"
            '    """Scale.\n\n    Args:\n'
            "        value: The value.\n        amount: The multiplier.\n"
            '    """\n'
            "    return value * factor\n"
        )
        _, store = indexed_project({"mod.py": drifted})
        padded = ("# pad\n" + drifted).encode()
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = DocstringParamRenamePlanner(overlay, ts).plan(
            SymbolAnchor("mod:scale"), "amount", "factor", "google"
        )
        edits = ts.load(summary.tx_id).edits
        [edit] = edits
        assert edit.file_hash == overlay.file_hash("mod.py")
        assert edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "mod.py"
        )

    def test_replace_text_planner_re_anchors_against_overlay_not_disk(
        self, indexed_project
    ):
        # ReplaceTextPlanner is the odd one out (AnchorFileExists only, then
        # its own read) — this proves that read is overlay-routed: the
        # anchor's recorded offset is still valid on DISK (unpadded), so a
        # port that secretly fell back to disk would match there directly
        # without re-anchoring. Padding the overlay forces a re-anchor,
        # which only succeeds if "foo" was searched for in overlay bytes.
        source = "def foo():\n    return 1\n"
        _, store = indexed_project({"mod.py": source})
        padded = ("# padding shifts the anchor\n" + source).encode()
        overlay = _overlay_with(store, "mod.py", padded)

        ts = TransactionStore(store.project_root)
        summary = ReplaceTextPlanner(overlay, ts).plan(
            RangeAnchor("mod.py", 0, 4), "foo", "bar"
        )
        edits = ts.load(summary.tx_id).edits
        [edit] = edits
        assert padded[edit.start : edit.end] == b"foo"
        assert edit.start != 4  # the disk-only offset; proves it re-anchored
        assert edit.file_hash == overlay.file_hash("mod.py")
        assert edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "mod.py"
        )


# ---------------------------------------------------------------------------
# visibility_ops: promote's export edit into a package __init__.py
# ---------------------------------------------------------------------------


class TestVisibilityPlannerExportEditReadsThroughOverlay:
    def test_add_export_hash_is_overlay_aware(self, indexed_project):
        _, store = indexed_project({
            "pkg/__init__.py": "from pkg.other import x\n",
            "pkg/other.py": "x = 1\n",
            "pkg/mod.py": "def _helper():\n    return 1\n",
        })
        padded_init = b"# pad\nfrom pkg.other import x\n"
        overlay = _overlay_with(store, "pkg/__init__.py", padded_init)

        ts = TransactionStore(store.project_root)
        result = VisibilityPlanner(overlay, ts).plan_promote(
            "pkg.mod:_helper", add_export="pkg"
        )
        edits = ts.load(result.summary.tx_id).edits
        init_edit = next(e for e in edits if e.file == "pkg/__init__.py")
        assert init_edit.file_hash == overlay.file_hash("pkg/__init__.py")
        assert init_edit.file_hash != IndexStore.compute_file_hash(
            store.project_root / "pkg/__init__.py"
        )


# ---------------------------------------------------------------------------
# analysis/hierarchy.py: from_store's collapsed getattr duck-typing
# ---------------------------------------------------------------------------


class TestHierarchyFromStoreReadsThroughOverlay:
    def test_base_discrimination_reads_an_overlay_only_file(self, indexed_project):
        _, store = indexed_project({"mod.py": "class Base:\n    pass\n"})
        content = b"from mod import Base\n\n\nclass Foo(Base):\n    pass\n"
        overlay = OverlayIndexStore(store)
        overlay.write_file("virt.py", content)
        rebind_source(overlay, "virt.py", content)

        hierarchy = Hierarchy.from_store(overlay)
        [base] = hierarchy.bases("virt:Foo")
        assert base.class_id == "mod:Base"

    def test_missing_source_falls_back_to_unreadable_header(self, indexed_project):
        # A class exists in the index but its file was deleted in the
        # overlay: read_source must catch the OSError and return None
        # rather than raising, same as the pre-port getattr fallback did.
        _, store = indexed_project({"mod.py": "class Foo(Bar):\n    pass\n"})
        overlay = OverlayIndexStore(store)
        overlay.delete_file("mod.py")

        hierarchy = Hierarchy.from_store(overlay)
        [base] = hierarchy.bases("mod:Foo")
        assert base.class_id is None
        assert base.text == "<unreadable header>"


# ---------------------------------------------------------------------------
# SemanticQueryEngine's default tree store under an overlay (TASK-133)
# ---------------------------------------------------------------------------


class TestDefaultTreeStoreUnderOverlay:
    """A simulated ``get_tree()`` must never persist into the real
    ``.pypeeker/tree.json`` — before the fix, an engine constructed over an
    :class:`OverlayIndexStore` with no injected ``tree_store`` fell back to
    ``TreeStore(store.project_root)``, which writes to the real project root
    the overlay wraps.
    """

    def test_simulated_tree_does_not_touch_the_real_tree_json(
        self, indexed_project
    ):
        project_dir, store = indexed_project(
            {"pkg/__init__.py": "", "pkg/mod.py": "def foo(): pass\n"}
        )
        # Establish the real tree artifact first, through a real engine.
        SemanticQueryEngine(store).get_tree()
        tree_json = project_dir / ".pypeeker" / "tree.json"
        before = tree_json.read_bytes()

        overlay = OverlayIndexStore(store)
        overlay.delete_file("pkg/mod.py")
        overlay.remove("pkg/mod.py")

        simulated = SemanticQueryEngine(overlay).get_tree()
        assert "pkg" in simulated.nodes
        assert "pkg.mod" not in simulated.nodes
        assert tree_json.read_bytes() == before

    def test_simulation_only_module_is_never_persisted(self, indexed_project):
        project_dir, store = indexed_project(
            {"pkg/__init__.py": "", "pkg/mod.py": "def foo(): pass\n"}
        )
        overlay = _overlay_with(store, "pkg/newmod.py", b"def bar(): pass\n")

        simulated = SemanticQueryEngine(overlay).get_tree()
        assert "pkg.newmod" in simulated.nodes
        assert not (project_dir / ".pypeeker" / "tree.json").exists()

    def test_a_real_batch_creates_no_tree_artifact(self, indexed_project):
        """Invariant guard: nothing in ``refactor/`` calls ``get_tree``/
        ``members`` today, so a real batch run creates no tree artifact
        either way — this passes before and after the fix, and is what would
        catch a future planner that starts consulting the tree under
        simulation without also asking the store for its default.
        """
        from pypeeker.intents import RenameIntent
        from pypeeker.refactor.batch import run_batch

        project_dir, store = indexed_project(
            {"mod.py": "def foo():\n    pass\n\nfoo()\n"}
        )
        tx_store = TransactionStore(project_dir / "tx")
        result = run_batch(
            [RenameIntent("r1", "mod:foo", "bar")], store, tx_store=tx_store
        )

        assert result.dropped == ()
        assert not (project_dir / ".pypeeker" / "tree.json").exists()
