"""Tests for TASK-125: PreconditionResult as the single atom of "why not".

Each of the five phase-4 remedy planners (delete-symbol, remove-import,
rewrite-star-import, tuplify, replace-text — ``tests/test_planner_ports.py``'s
subjects) now refuses via a named
:class:`~pypeeker.refactor.preconditions.Precondition` evaluated through
:func:`~pypeeker.refactor.preconditions.evaluate_in_order`, exactly like the
classic planners (rename/inline/extract/visibility). This module asserts the
new, additive plumbing on top of that: the failing precondition's ``name``
surfaces on :class:`~pypeeker.app.submit.SubmitError` and
:class:`~pypeeker.refactor.batch.DroppedIntent`, the derived
:attr:`~pypeeker.refactor.preconditions.Precondition.slug` equals the legacy
``check --fix`` reason ``test_planner_ports.py`` already pins, and the detail
message is unchanged from the phase-4 wording.

``test_planner_ports.py`` and ``test_preconditions.py`` remain the oracles for
behavior and message identity; nothing here duplicates or loosens either.
"""

from __future__ import annotations

import pytest

from pypeeker.app import SubmitError, submit_intent
from pypeeker.intents import (
    DeleteSymbolIntent,
    RemoveImportIntent,
    RenameIntent,
    ReplaceTextIntent,
    RewriteStarImportIntent,
    TuplifyIntent,
)
from pypeeker.refactor.batch import DropReason, run_batch


class TestDeleteSymbolRefusalVocabulary:
    def test_non_definition_anchor_refuses_via_submit(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        intent = DeleteSymbolIntent("i1", "mod:f:x")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "symbol-match-found"
        assert excinfo.value.code == "text-mismatch"
        assert excinfo.value.detail == "symbol 'mod:f:x' is no longer in the index"

    def test_non_definition_anchor_drops_with_precondition(
        self, indexed_project, tmp_path
    ):
        _, store = indexed_project({"mod.py": "def f():\n    x = 1\n    return x\n"})
        intent = DeleteSymbolIntent("i1", "mod:f:x")

        result = run_batch([intent], store, work_dir=tmp_path / "mirror")

        assert result.executed == ()
        [dropped] = result.dropped
        assert dropped.reason is DropReason.PRECONDITION_FAILED
        assert dropped.precondition == "symbol-match-found"
        assert dropped.detail == "symbol 'mod:f:x' is no longer in the index"


class TestRemoveImportRefusalVocabulary:
    def test_parenthesized_import_list_refuses_via_submit(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({
            "mod.py": (
                "from typing import (Any, Optional)\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        intent = RemoveImportIntent("i1", "mod:Optional", "Optional")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "import-line-surgery-safe"
        assert excinfo.value.code == "ambiguous"
        assert (
            excinfo.value.detail
            == "parenthesized or continued import lists are not edited"
        )

    def test_parenthesized_import_list_drops_with_precondition(
        self, indexed_project, tmp_path
    ):
        _, store = indexed_project({
            "mod.py": (
                "from typing import (Any, Optional)\n"
                "\n"
                "def f(x: Any):\n"
                "    return x\n"
            )
        })
        intent = RemoveImportIntent("i1", "mod:Optional", "Optional")

        result = run_batch([intent], store, work_dir=tmp_path / "mirror")

        [dropped] = result.dropped
        assert dropped.precondition == "import-line-surgery-safe"
        assert (
            dropped.detail == "parenthesized or continued import lists are not edited"
        )


class TestRewriteStarImportRefusalVocabulary:
    LIB = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n\n\n_hidden = 3\n"

    def test_zero_used_names_refuses_via_submit(self, indexed_project, transaction_store):
        _, store = indexed_project({
            "lib.py": self.LIB,
            "app.py": "from lib import *\n\nx = 1\n",
        })
        intent = RewriteStarImportIntent("i1", "app:*", "lib")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "star-supply-nonempty"
        assert excinfo.value.code == "ambiguous"
        assert excinfo.value.detail == (
            "no names from 'lib' are used; delete the star import instead of "
            "rewriting it"
        )


class TestTuplifyRefusalVocabulary:
    def test_fstring_in_literal_refuses_via_submit(self, indexed_project, transaction_store):
        _, store = indexed_project(
            {"mod.py": 'def f(x):\n    xs = [f"{x}", 1]\n    return xs[0]\n'}
        )
        intent = TuplifyIntent("i1", "mod:f:xs", "xs")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "scannable-literal"
        assert excinfo.value.code == "ambiguous"
        assert excinfo.value.detail == "the literal contains an f-string"

    def test_fstring_in_literal_drops_with_precondition(self, indexed_project, tmp_path):
        _, store = indexed_project(
            {"mod.py": 'def f(x):\n    xs = [f"{x}", 1]\n    return xs[0]\n'}
        )
        intent = TuplifyIntent("i1", "mod:f:xs", "xs")

        result = run_batch([intent], store, work_dir=tmp_path / "mirror")

        [dropped] = result.dropped
        assert dropped.precondition == "scannable-literal"
        assert dropped.detail == "the literal contains an f-string"


class TestReplaceTextRefusalVocabulary:
    SOURCE = "def foo():\n    return 1\n"

    def test_text_mismatch_refuses_via_submit(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        (project_dir / "mod.py").write_text("def baz():\n    return 1\n")
        intent = ReplaceTextIntent("i1", "mod.py", 0, 4, "foo", "bar")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "occurrence-exists"
        assert excinfo.value.code == "text-mismatch"
        assert excinfo.value.detail == "expected text 'foo' not found in mod.py"

    def test_ambiguous_reanchor_refuses_via_submit(self, indexed_project, transaction_store):
        project_dir, store = indexed_project({"mod.py": self.SOURCE})
        (project_dir / "mod.py").write_text("# foo\n\ndef foo():\n    return 1\n")
        intent = ReplaceTextIntent("i1", "mod.py", 0, 4, "foo", "bar")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "unique-occurrence"
        assert excinfo.value.code == "ambiguous"
        assert excinfo.value.detail == "expected text 'foo' occurs more than once in mod.py"


class TestRenameRefusalVocabulary:
    """The classic planners already flow named preconditions (TASK-85); this
    confirms the field now also surfaces through SubmitError/DroppedIntent."""

    def test_invalid_identifier_refuses_via_submit(self, indexed_project, transaction_store):
        _, store = indexed_project({"test.py": "def foo(): pass\n"})
        intent = RenameIntent("i1", "test:foo", "123invalid")

        with pytest.raises(SubmitError) as excinfo:
            submit_intent(intent, store, transaction_store)

        assert excinfo.value.precondition == "valid-identifier"
        assert excinfo.value.code == "plan-refused"
        assert excinfo.value.detail == "Invalid Python identifier: 123invalid"

    def test_invalid_identifier_drops_with_precondition(self, indexed_project, tmp_path):
        _, store = indexed_project({"test.py": "def foo(): pass\n"})
        intent = RenameIntent("i1", "test:foo", "123invalid")

        result = run_batch([intent], store, work_dir=tmp_path / "mirror")

        [dropped] = result.dropped
        assert dropped.reason is DropReason.PRECONDITION_FAILED
        assert dropped.precondition == "valid-identifier"
        assert dropped.detail == "Invalid Python identifier: 123invalid"
