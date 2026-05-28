"""Tests for inline-variable refactoring."""

from __future__ import annotations

import ast
import pytest

from pypeeker.refactor.applier import TransactionApplier
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.storage import TransactionStore


def _apply(store, summary):
    ts = TransactionStore(store.project_root)
    return TransactionApplier(store, ts).apply(summary.tx_id)


def test_inline_single_use(indexed_project):
    project, store = indexed_project({"m.py": "def f(a):\n    x = a + 1\n    return x\n"})
    ts = TransactionStore(store.project_root)
    summary = InlineVariablePlanner(store, ts).plan("m:f:x")
    _apply(store, summary)
    out = (project / "m.py").read_text()
    assert out == "def f(a):\n    return (a + 1)\n"
    ast.parse(out)


def test_inline_multiple_uses_pure(indexed_project):
    project, store = indexed_project({"m.py": "def f(a):\n    x = a + 1\n    return x + x\n"})
    ts = TransactionStore(store.project_root)
    summary = InlineVariablePlanner(store, ts).plan("m:f:x")
    _apply(store, summary)
    out = (project / "m.py").read_text()
    assert out == "def f(a):\n    return (a + 1) + (a + 1)\n"


def test_inline_atom_no_parens(indexed_project):
    project, store = indexed_project({"m.py": "def f(a):\n    x = a\n    return x\n"})
    ts = TransactionStore(store.project_root)
    summary = InlineVariablePlanner(store, ts).plan("m:f:x")
    _apply(store, summary)
    assert (project / "m.py").read_text() == "def f(a):\n    return a\n"


def test_inline_dead_variable_just_deletes(indexed_project):
    project, store = indexed_project({"m.py": "def f(a):\n    x = a + 1\n    return a\n"})
    ts = TransactionStore(store.project_root)
    summary = InlineVariablePlanner(store, ts).plan("m:f:x")
    assert summary.edit_count == 1  # only the delete
    _apply(store, summary)
    assert (project / "m.py").read_text() == "def f(a):\n    return a\n"


def test_inline_impure_single_use_allowed(indexed_project):
    project, store = indexed_project({
        "m.py": "def f(items):\n    x = items.pop()\n    return x\n"
    })
    ts = TransactionStore(store.project_root)
    summary = InlineVariablePlanner(store, ts).plan("m:f:x")
    _apply(store, summary)
    assert (project / "m.py").read_text() == "def f(items):\n    return items.pop()\n"


def test_inline_impure_multiple_uses_refused(indexed_project):
    _, store = indexed_project({
        "m.py": "def f(items):\n    x = items.pop()\n    return x + x\n"
    })
    ts = TransactionStore(store.project_root)
    with pytest.raises(InlineVariableError, match="side effects"):
        InlineVariablePlanner(store, ts).plan("m:f:x")


def test_inline_reassigned_refused(indexed_project):
    _, store = indexed_project({
        "m.py": "def f(a):\n    x = 1\n    x = 2\n    return x\n"
    })
    ts = TransactionStore(store.project_root)
    with pytest.raises(InlineVariableError, match="reassigned"):
        InlineVariablePlanner(store, ts).plan("m:f:x")


def test_inline_non_local_refused(indexed_project):
    _, store = indexed_project({"m.py": "x = 1\ny = x\n"})
    ts = TransactionStore(store.project_root)
    with pytest.raises(InlineVariableError, match="function-local"):
        InlineVariablePlanner(store, ts).plan("m:x")
