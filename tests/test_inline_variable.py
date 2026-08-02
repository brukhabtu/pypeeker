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


# ---------------------------------------------------------------------------
# TASK-141: spans this planner records as edit text must decode as UTF-8
# ---------------------------------------------------------------------------


def _rebind_bytes(project, store, name, raw):
    """Plant non-UTF-8 ``raw`` bytes for ``name`` and re-bind them.

    ``indexed_project`` writes text, so a non-UTF-8 fixture has to be planted
    separately and re-bound the way ``index`` would — otherwise the hash
    check refuses before the decode site is ever reached. Mirrors the helper
    of the same name in ``tests/test_extract_variable.py``.
    """
    from pypeeker.adapters.python_adapter import PythonAdapter
    from pypeeker.binder.binder import bind
    from pypeeker.paths import module_path_from

    (project / name).write_bytes(raw)
    ad = PythonAdapter()
    store.save(
        bind(ad, name, raw, ad.parse(raw).root_node, module_path=module_path_from(name))
    )


def test_refuses_non_utf8_assignment_line(indexed_project):
    """The deleted statement span covers its trailing comment, so it is guarded."""
    project, store = indexed_project(
        {"m.py": "def f(a):\n    x = a + 1  # note\n    return x\n"}
    )
    raw = b"def f(a):\n    x = a + 1  # caf\xe9\n    return x\n"
    _rebind_bytes(project, store, "m.py", raw)
    ts = TransactionStore(store.project_root)

    with pytest.raises(InlineVariableError) as exc:
        InlineVariablePlanner(store, ts).plan("m:f:x")

    assert exc.value.precondition == "source-is-utf8"
    assert str(exc.value) == (
        "File is not valid UTF-8: m.py (byte 30: invalid continuation byte)"
    )
    assert ts.list() == []
    assert (project / "m.py").read_bytes() == raw


def test_refuses_non_utf8_rhs_expression(indexed_project):
    """The RHS is spliced into every use, so it too must decode."""
    project, store = indexed_project({"m.py": 'def f():\n    x = "cafe"\n    return x\n'})
    raw = b'def f():\n    x = "caf\xe9"\n    return x\n'
    _rebind_bytes(project, store, "m.py", raw)
    ts = TransactionStore(store.project_root)

    with pytest.raises(InlineVariableError) as exc:
        InlineVariablePlanner(store, ts).plan("m:f:x")

    assert exc.value.precondition == "source-is-utf8"
    assert str(exc.value) == (
        "File is not valid UTF-8: m.py (byte 21: invalid continuation byte)"
    )
    assert ts.list() == []


def test_ascii_inline_in_a_non_utf8_file_still_applies(indexed_project):
    """Span-scoping: undecodable bytes on an unrelated line don't block the inline."""
    project, store = indexed_project(
        {"m.py": "# note\ndef f(a):\n    x = a + 1\n    return x\n"}
    )
    _rebind_bytes(
        project, store, "m.py", b"# caf\xe9\ndef f(a):\n    x = a + 1\n    return x\n"
    )
    ts = TransactionStore(store.project_root)

    summary = InlineVariablePlanner(store, ts).plan("m:f:x")
    _apply(store, summary)

    assert (project / "m.py").read_bytes() == (
        b"# caf\xe9\ndef f(a):\n    return (a + 1)\n"
    )
