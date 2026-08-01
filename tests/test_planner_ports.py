"""Tests for the TASK-124 intent-anchored planners.

Each fix of the superseded TASK-82 fix protocol
(``ReplaceTextFix``, ``PreferTupleFix``, ``RemoveUnusedImportFix``,
``DeleteUnusedSymbolFix``, ``_RewriteStarImportFix``,
``_DocstringParamRenameFix``) became a planner in ``refactor/`` registered
under a real :class:`~pypeeker.intents.Intent` kind.
These tests port the old fix-level unit tests' scenarios (success
byte-rewrite, decline paths, re-anchoring) against the new planner API —
``Planner(store, tx_store).plan(anchor, ...)`` raising a planner-specific
``*Error`` carrying the legacy refusal slug as its ``.code`` — plus one
end-to-end registry-dispatch test per kind, driven through
``submit_intent``/``run_batch`` exactly like
``tests/test_planner_registry.py``'s ``TestDispatchParity``.

The complementary axis — that each *rule* attaches the right remedy and that
repairing through it fixes the code — is ``tests/test_check_fix.py``'s and
``tests/test_rule_*.py``'s; the field contract of ``Violation.remedy`` itself
is ``tests/test_violation_remedy.py``'s.
"""

from __future__ import annotations

import pytest

from pypeeker.app import SubmitError, submit_intent
from pypeeker.intents import (
    DeleteSymbolIntent,
    RangeAnchor,
    RemoveImportIntent,
    RenameDocstringParamIntent,
    ReplaceTextIntent,
    RewriteStarImportIntent,
    SymbolAnchor,
    TuplifyIntent,
)
from pypeeker.refactor import TransactionApplier
from pypeeker.refactor.batch import run_batch
from pypeeker.refactor.delete import DeleteSymbolError, DeleteSymbolPlanner
from pypeeker.refactor.docstring_ops import (
    DocstringParamRenameError,
    DocstringParamRenamePlanner,
)
from pypeeker.refactor.imports_ops import (
    RemoveImportError,
    RemoveImportPlanner,
    RewriteStarImportError,
    RewriteStarImportPlanner,
)
from pypeeker.refactor.literals import TuplifyError, TuplifyPlanner
from pypeeker.refactor.text_ops import ReplaceTextError, ReplaceTextPlanner
from pypeeker.storage import IndexStore, TransactionStore


def _apply(store, transaction_store, tx_id: str) -> None:
    """Apply a persisted transaction and assert it succeeded."""
    result = TransactionApplier(store, transaction_store).apply(tx_id)
    assert result["status"] == "applied"


# ---------------------------------------------------------------------------
# ReplaceTextPlanner (ports the fix protocol's ReplaceTextFix)
# ---------------------------------------------------------------------------


class TestReplaceTextPlanner:
    SOURCE = "def foo():\n    return 1\n"

    def test_plan_yields_a_fresh_hash_edit(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        planner = ReplaceTextPlanner(store, transaction_store)

        summary = planner.plan(RangeAnchor("mod.py", 0, 4), "foo", "bar")

        assert summary.operation == "replace-text"
        edits = transaction_store.load(summary.tx_id).edits
        [edit] = edits
        assert (edit.old, edit.new) == ("foo", "bar")
        content = (project_dir / "mod.py").read_bytes()
        assert content[edit.start : edit.end] == b"foo"
        assert edit.file_hash == IndexStore.compute_file_hash(project_dir / "mod.py")

    def test_plan_applies_cleanly(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        summary = ReplaceTextPlanner(store, transaction_store).plan(
            RangeAnchor("mod.py", 0, 4), "foo", "bar"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "mod.py").read_text() == "def bar():\n    return 1\n"

    def test_benign_unrelated_edit_reanchors_uniquely(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        planner = ReplaceTextPlanner(store, transaction_store)
        # Unrelated edit above the anchor shifts every byte offset, but "foo"
        # still occurs exactly once: a benign re-anchor, not a decline.
        (project_dir / "mod.py").write_text("import os\n\n" + self.SOURCE)

        summary = planner.plan(RangeAnchor("mod.py", 0, 4), "foo", "bar")
        edits = transaction_store.load(summary.tx_id).edits
        [edit] = edits
        content = (project_dir / "mod.py").read_bytes()
        assert content[edit.start : edit.end] == b"foo"

    def test_anchor_match_wins_even_when_text_repeats(
        self, indexed_project, transaction_store
    ):
        # "foo" occurs twice, but the recorded anchor still verifiably holds
        # the text, so the plan lands there with no ambiguity.
        _, store = indexed_project({"mod.py": "def foo():\n    return 1\n\nfoo()\n"})

        summary = ReplaceTextPlanner(store, transaction_store).plan(
            RangeAnchor("mod.py", 0, 4), "foo", "bar"
        )
        edits = transaction_store.load(summary.tx_id).edits
        [edit] = edits
        assert (edit.start, edit.end) == (4, 7)  # the def site, not the call

    def test_reanchored_edits_apply_cleanly(self, indexed_project, transaction_store):
        # The re-anchored plan is hash-anchored to the CURRENT bytes, so it
        # still applies after the shift that invalidated the original offset.
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        planner = ReplaceTextPlanner(store, transaction_store)
        stale = planner.plan(RangeAnchor("mod.py", 0, 4), "foo", "bar")
        (project_dir / "mod.py").write_text("import os\n\n" + self.SOURCE)

        summary = planner.plan(RangeAnchor("mod.py", 0, 4), "foo", "bar")
        fresh_edits = transaction_store.load(summary.tx_id).edits
        stale_edits = transaction_store.load(stale.tx_id).edits
        assert fresh_edits[0].start != stale_edits[0].start
        assert fresh_edits[0].file_hash != stale_edits[0].file_hash

        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "mod.py").read_text() == (
            "import os\n\ndef bar():\n    return 1\n"
        )

    def test_text_mismatch_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        planner = ReplaceTextPlanner(store, transaction_store)
        (project_dir / "mod.py").write_text("def baz():\n    return 1\n")

        with pytest.raises(ReplaceTextError) as excinfo:
            planner.plan(RangeAnchor("mod.py", 0, 4), "foo", "bar")
        assert excinfo.value.code == "text-mismatch"

    def test_ambiguous_reanchor_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        planner = ReplaceTextPlanner(store, transaction_store)
        (project_dir / "mod.py").write_text("# foo\n\ndef foo():\n    return 1\n")

        with pytest.raises(ReplaceTextError) as excinfo:
            planner.plan(RangeAnchor("mod.py", 0, 4), "foo", "bar")
        assert excinfo.value.code == "ambiguous"

    def test_missing_file_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        (project_dir / "mod.py").unlink()

        with pytest.raises(ReplaceTextError) as excinfo:
            ReplaceTextPlanner(store, transaction_store).plan(
                RangeAnchor("mod.py", 0, 4), "foo", "bar"
            )
        assert excinfo.value.code == "file-missing"


# ---------------------------------------------------------------------------
# TuplifyPlanner (ports the prefer-tuple autofix)
# ---------------------------------------------------------------------------


class TestTuplifyPlanner:
    def test_multi_element_rewrite_end_to_end(self, indexed_project, transaction_store):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2, 3]\n    return xs[0]\n"}
        )
        summary = TuplifyPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:f:xs"), "xs"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f():\n    xs = (1, 2, 3)\n    return xs[0]\n"

    def test_single_element_list_gets_trailing_comma(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project(
            {"mod.py": "def f(x):\n    xs = [x]\n    return xs[0]\n"}
        )
        summary = TuplifyPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:f:xs"), "xs"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f(x):\n    xs = (x,)\n    return xs[0]\n"

    def test_comprehension_rewritten_as_tuple_call(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project(
            {"mod.py": "def f(xs):\n    ys = [a for a in xs]\n    return ys[0]\n"}
        )
        summary = TuplifyPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:f:ys"), "ys"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "mod.py").read_text() == (
            "def f(xs):\n    ys = tuple(a for a in xs)\n    return ys[0]\n"
        )

    def test_fstring_in_literal_declines_ambiguous(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project(
            {"mod.py": 'def f(x):\n    xs = [f"{x}", 1]\n    return xs[0]\n'}
        )
        with pytest.raises(TuplifyError) as excinfo:
            TuplifyPlanner(store, transaction_store).plan(SymbolAnchor("mod:f:xs"), "xs")
        assert excinfo.value.code == "ambiguous"
        assert "f-string" in str(excinfo.value)

    def test_mutated_file_between_detect_and_plan_declines_stale(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"}
        )
        (project_dir / "mod.py").write_text(
            "# moved\ndef f():\n    xs = [1, 2]\n    return xs[0]\n"
        )
        with pytest.raises(TuplifyError) as excinfo:
            TuplifyPlanner(store, transaction_store).plan(SymbolAnchor("mod:f:xs"), "xs")
        assert excinfo.value.code == "stale-index"

    def test_missing_file_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1]\n    return xs[0]\n"}
        )
        (project_dir / "mod.py").unlink()
        with pytest.raises(TuplifyError) as excinfo:
            TuplifyPlanner(store, transaction_store).plan(SymbolAnchor("mod:f:xs"), "xs")
        assert excinfo.value.code == "file-missing"

    def test_unresolvable_anchor_declines_text_mismatch(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(TuplifyError) as excinfo:
            TuplifyPlanner(store, transaction_store).plan(SymbolAnchor("mod:ghost"), "ghost")
        assert excinfo.value.code == "text-mismatch"

    def test_non_list_binding_declines_text_mismatch(
        self, indexed_project, transaction_store
    ):
        # TASK-128 oracle: a fresh-index VARIABLE that resolves but was never
        # bound to an inferred list literal — pins InferredListBinding's
        # refusal wording/slug before the type-annotation trait migration.
        _, store = indexed_project({"mod.py": "def f():\n    xs = 5\n    return xs\n"})
        with pytest.raises(TuplifyError) as excinfo:
            TuplifyPlanner(store, transaction_store).plan(SymbolAnchor("mod:f:xs"), "xs")
        assert excinfo.value.code == "text-mismatch"
        assert str(excinfo.value) == "'xs' is no longer bound to an inferred list literal"


# ---------------------------------------------------------------------------
# RemoveImportPlanner (ports the unused-imports autofix)
# ---------------------------------------------------------------------------


class TestRemoveImportPlanner:
    def test_single_name_line_deleted_whole(self, indexed_project, transaction_store):
        project_dir, store = indexed_project(
            {"mod.py": "import os\n\ndef f():\n    return 1\n"}
        )
        summary = RemoveImportPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:os"), "os"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "mod.py").read_text() == "\ndef f():\n    return 1\n"

    def test_multi_name_last_entry_removed(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({
            "mod.py": (
                "from typing import Any, Optional\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        summary = RemoveImportPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:Optional"), "Optional"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (
            project_dir / "mod.py"
        ).read_text() == "from typing import Any\n\ndef f(x: Any):\n    return x\n"

    def test_parenthesized_import_list_declines(self, indexed_project, transaction_store):
        _, store = indexed_project({
            "mod.py": (
                "from typing import (Any, Optional)\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        with pytest.raises(RemoveImportError) as excinfo:
            RemoveImportPlanner(store, transaction_store).plan(
                SymbolAnchor("mod:Optional"), "Optional"
            )
        assert excinfo.value.code == "ambiguous"

    def test_mutated_file_declines_stale(self, indexed_project, transaction_store):
        project_dir, store = indexed_project(
            {"mod.py": "import os\n\ndef f():\n    return 1\n"}
        )
        (project_dir / "mod.py").write_text("# shifted\nimport os\n\ndef f():\n    return 1\n")
        with pytest.raises(RemoveImportError) as excinfo:
            RemoveImportPlanner(store, transaction_store).plan(SymbolAnchor("mod:os"), "os")
        assert excinfo.value.code == "stale-index"

    def test_unresolvable_anchor_declines_text_mismatch(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "x = 1\n"})
        with pytest.raises(RemoveImportError) as excinfo:
            RemoveImportPlanner(store, transaction_store).plan(
                SymbolAnchor("mod:ghost"), "ghost"
            )
        assert excinfo.value.code == "text-mismatch"


# ---------------------------------------------------------------------------
# DeleteSymbolPlanner (ports the unused-public-symbol autofix)
# ---------------------------------------------------------------------------


class TestDeleteSymbolPlanner:
    def test_deletion_end_to_end_eats_trailing_blank_lines(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({
            "mod.py": (
                "def _dead():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def keep():\n"
                "    return keep\n"
            )
        })
        summary = DeleteSymbolPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:_dead")
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def keep():\n    return keep\n"

    def test_class_deletion(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({
            "mod.py": ("class _Dead:\n" "    x = 1\n" "\n" "\n" "VALUE = 2\n")
        })
        summary = DeleteSymbolPlanner(store, transaction_store).plan(
            SymbolAnchor("mod:_Dead")
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "mod.py").read_text() == "VALUE = 2\n"

    def test_decorated_symbol_declines(self, indexed_project, transaction_store):
        _, store = indexed_project({
            "mod.py": (
                "import functools\n"
                "\n"
                "\n"
                "@functools.cache\n"
                "def _dead():\n"
                "    return 1\n"
            )
        })
        with pytest.raises(DeleteSymbolError) as excinfo:
            DeleteSymbolPlanner(store, transaction_store).plan(SymbolAnchor("mod:_dead"))
        assert excinfo.value.code == "ambiguous"
        assert "decorated" in str(excinfo.value)

    def test_mutated_file_declines_stale(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": "def _dead():\n    return 1\n"})
        (project_dir / "mod.py").write_text("# shifted\ndef _dead():\n    return 1\n")
        with pytest.raises(DeleteSymbolError) as excinfo:
            DeleteSymbolPlanner(store, transaction_store).plan(SymbolAnchor("mod:_dead"))
        assert excinfo.value.code == "stale-index"

    def test_missing_file_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": "def _dead():\n    return 1\n"})
        (project_dir / "mod.py").unlink()
        with pytest.raises(DeleteSymbolError) as excinfo:
            DeleteSymbolPlanner(store, transaction_store).plan(SymbolAnchor("mod:_dead"))
        assert excinfo.value.code == "file-missing"

    def test_non_function_class_anchor_declines_text_mismatch(
        self, indexed_project, transaction_store
    ):
        # "mod:f:x" is a VARIABLE, not a FUNCTION/CLASS: the planner's
        # project-wide resolution filters to definition kinds only.
        _, store = indexed_project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        with pytest.raises(DeleteSymbolError) as excinfo:
            DeleteSymbolPlanner(store, transaction_store).plan(SymbolAnchor("mod:f:x"))
        assert excinfo.value.code == "text-mismatch"


# ---------------------------------------------------------------------------
# RewriteStarImportPlanner (ports the star-imports autofix)
# ---------------------------------------------------------------------------


class TestRewriteStarImportPlanner:
    LIB = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n\n\n_hidden = 3\n"

    def test_rewrite_preserves_module_text_and_comment(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({
            "pkg/__init__.py": "",
            "pkg/sib.py": "def gamma():\n    return 1\n",
            "pkg/mod.py": "from .sib import *  # keep\n\nx = gamma()\n",
        })
        summary = RewriteStarImportPlanner(store, transaction_store).plan(
            SymbolAnchor("pkg.mod:*"), "pkg.sib"
        )
        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "pkg" / "mod.py").read_text() == (
            "from .sib import gamma  # keep\n\nx = gamma()\n"
        )

    def test_zero_used_names_declines_with_deletion_advice(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({
            "lib.py": self.LIB,
            "app.py": "from lib import *\n\nx = 1\n",
        })
        with pytest.raises(RewriteStarImportError) as excinfo:
            RewriteStarImportPlanner(store, transaction_store).plan(
                SymbolAnchor("app:*"), "lib"
            )
        assert excinfo.value.code == "ambiguous"
        assert "delete the star import" in str(excinfo.value)

    def test_unattributable_name_declines(self, indexed_project, transaction_store):
        _, store = indexed_project({
            "lib.py": self.LIB,
            "app.py": "from lib import *\n\nx = alpha() + ghost()\n",
        })
        with pytest.raises(RewriteStarImportError) as excinfo:
            RewriteStarImportPlanner(store, transaction_store).plan(
                SymbolAnchor("app:*"), "lib"
            )
        assert excinfo.value.code == "ambiguous"
        assert "'ghost'" in str(excinfo.value)

    def test_multi_star_file_declines(self, indexed_project, transaction_store):
        _, store = indexed_project({
            "liba.py": "def only_a():\n    return 1\n",
            "libb.py": "def only_b():\n    return 2\n",
            "app.py": "from liba import *\nfrom libb import *\n\nx = only_a()\n",
        })
        with pytest.raises(RewriteStarImportError) as excinfo:
            RewriteStarImportPlanner(store, transaction_store).plan(
                SymbolAnchor("app:*"), "liba"
            )
        assert excinfo.value.code == "ambiguous"
        assert "star imports" in str(excinfo.value)

    def test_unindexed_target_declines(self, indexed_project, transaction_store):
        _, store = indexed_project({"app.py": "from os.path import *\n\nx = join('a')\n"})
        with pytest.raises(RewriteStarImportError) as excinfo:
            RewriteStarImportPlanner(store, transaction_store).plan(
                SymbolAnchor("app:*"), "os.path"
            )
        assert excinfo.value.code == "ambiguous"
        assert "not indexed" in str(excinfo.value)

    def test_missing_file_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({
            "lib.py": self.LIB,
            "app.py": "from lib import *\n\ndef use():\n    return beta() + alpha()\n",
        })
        (project_dir / "app.py").unlink()
        with pytest.raises(RewriteStarImportError) as excinfo:
            RewriteStarImportPlanner(store, transaction_store).plan(
                SymbolAnchor("app:*"), "lib"
            )
        assert excinfo.value.code == "file-missing"


# ---------------------------------------------------------------------------
# DocstringParamRenamePlanner (ports the fix protocol's _DocstringParamRenameFix)
# ---------------------------------------------------------------------------


DRIFTED = (
    "def scale(value, factor):\n"
    '    """Scale.\n\n    Args:\n        value: The value.\n        amount: The multiplier.\n    """\n'
    "    return value * factor\n"
)


class TestDocstringParamRenamePlanner:
    """The one index-anchored *prose* rewrite: only the name token moves."""

    def test_renames_only_the_token(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": DRIFTED})
        summary = DocstringParamRenamePlanner(store, transaction_store).plan(
            SymbolAnchor("mod:scale"), "amount", "factor", "google"
        )
        assert summary.operation == "rename-docstring-param"
        _apply(store, transaction_store, summary.tx_id)
        assert (project_dir / "mod.py").read_text() == DRIFTED.replace(
            "amount:", "factor:"
        )

    def test_stale_index_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": DRIFTED})
        (project_dir / "mod.py").write_text("# shifted\n" + DRIFTED)
        with pytest.raises(DocstringParamRenameError) as excinfo:
            DocstringParamRenamePlanner(store, transaction_store).plan(
                SymbolAnchor("mod:scale"), "amount", "factor", "google"
            )
        assert excinfo.value.code == "stale-index"

    def test_missing_file_declines(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": DRIFTED})
        (project_dir / "mod.py").unlink()
        with pytest.raises(DocstringParamRenameError) as excinfo:
            DocstringParamRenamePlanner(store, transaction_store).plan(
                SymbolAnchor("mod:scale"), "amount", "factor", "google"
            )
        assert excinfo.value.code == "file-missing"

    def test_drift_that_no_longer_holds_declines(
        self, indexed_project, transaction_store
    ):
        # The signature grew a second undocumented parameter after detection:
        # "which parameter was renamed" is no longer answerable.
        _, store = indexed_project({
            "mod.py": DRIFTED.replace(
                "def scale(value, factor):", "def scale(value, factor, gamma):"
            )
        })
        with pytest.raises(DocstringParamRenameError) as excinfo:
            DocstringParamRenamePlanner(store, transaction_store).plan(
                SymbolAnchor("mod:scale"), "amount", "factor", "google"
            )
        assert excinfo.value.code == "ambiguous"
        assert "single-parameter rename" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Registry dispatch: each new kind, end-to-end through submit_intent/run_batch
# ---------------------------------------------------------------------------


class TestRegistryDispatch:
    def test_replace_text_dispatches_through_submit_intent(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({"mod.py": "def foo():\n    return 1\n"})
        intent = ReplaceTextIntent("i1", "mod.py", 0, 4, "foo", "bar")
        materialized = submit_intent(intent, store, transaction_store)
        assert materialized.summary is not None
        assert materialized.summary.operation == "replace-text"

    def test_tuplify_dispatches_through_submit_intent(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project(
            {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"}
        )
        intent = TuplifyIntent("i1", "mod:f:xs", "xs")
        materialized = submit_intent(intent, store, transaction_store)
        assert materialized.summary is not None
        _apply(store, transaction_store, materialized.summary.tx_id)
        assert (
            project_dir / "mod.py"
        ).read_text() == "def f():\n    xs = (1, 2)\n    return xs[0]\n"

    def test_rename_docstring_param_dispatches_through_submit_intent(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({"mod.py": DRIFTED})
        intent = RenameDocstringParamIntent(
            "i1", "mod:scale", "amount", "factor", "google"
        )
        materialized = submit_intent(intent, store, transaction_store)
        assert materialized.summary is not None
        _apply(store, transaction_store, materialized.summary.tx_id)
        assert (project_dir / "mod.py").read_text() == DRIFTED.replace(
            "amount:", "factor:"
        )

    def test_remove_import_dispatches_through_submit_intent(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project(
            {"mod.py": "import os\n\ndef f():\n    return 1\n"}
        )
        intent = RemoveImportIntent("i1", "mod:os", "os")
        materialized = submit_intent(intent, store, transaction_store)
        assert materialized.summary is not None
        _apply(store, transaction_store, materialized.summary.tx_id)
        assert (project_dir / "mod.py").read_text() == "\ndef f():\n    return 1\n"

    def test_rewrite_star_import_dispatches_through_submit_intent(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({
            "lib.py": "def alpha():\n    return 1\n",
            "app.py": "from lib import *\n\nx = alpha()\n",
        })
        intent = RewriteStarImportIntent("i1", "app:*", "lib")
        materialized = submit_intent(intent, store, transaction_store)
        assert materialized.summary is not None
        _apply(store, transaction_store, materialized.summary.tx_id)
        assert (project_dir / "app.py").read_text() == "from lib import alpha\n\nx = alpha()\n"

    def test_delete_symbol_dispatches_through_submit_intent(
        self, indexed_project, transaction_store
    ):
        project_dir, store = indexed_project({"mod.py": "def _dead():\n    return 1\n"})
        intent = DeleteSymbolIntent("i1", "mod:_dead")
        materialized = submit_intent(intent, store, transaction_store)
        assert materialized.summary is not None
        _apply(store, transaction_store, materialized.summary.tx_id)
        assert (project_dir / "mod.py").read_text() == ""

    def test_delete_symbol_decline_surfaces_as_submit_error_with_code(
        self, indexed_project, transaction_store
    ):
        # Same non-FUNCTION/CLASS anchor as TestDeleteSymbolPlanner above,
        # driven through submit_intent so the MaterializeError -> SubmitError
        # code mapping is covered end-to-end, not just at the planner level.
        _, store = indexed_project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        intent = DeleteSymbolIntent("i1", "mod:f:x")
        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)
        assert excinfo.value.code == "text-mismatch"

    def test_delete_symbol_dispatches_through_run_batch(
        self, indexed_project, tmp_path
    ):
        # The batch-of-many path (run_batch), not just submit_intent's
        # batch-of-one fast path — mirrors
        # test_planner_registry.py::TestDispatchParity's shape for the
        # other builtin kinds.
        project_dir, store = indexed_project({"mod.py": "def _dead():\n    return 1\n"})
        intent = DeleteSymbolIntent("i1", "mod:_dead")
        result = run_batch([intent], store, tx_store=TransactionStore(tmp_path / "tx"))
        assert [i.intent.intent_id for i in result.executed] == ["i1"]
        assert result.dropped == ()
        assert result.store.read_file("mod.py").decode() == ""
