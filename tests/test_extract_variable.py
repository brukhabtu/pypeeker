"""Tests for extract-variable refactoring."""

from __future__ import annotations

import pytest

from pypeeker.refactor.applier import TransactionApplier
from pypeeker.refactor.extract import ExtractVariableError, ExtractVariablePlanner
from pypeeker.storage import TransactionStore


def _project(tmp_path, files):
    (tmp_path / ".semantic-tool" / "index").mkdir(parents=True, exist_ok=True)
    from pypeeker.storage import IndexStore

    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path, IndexStore(tmp_path)


def test_extract_emits_insert_and_replace(tmp_path):
    project, store = _project(tmp_path, {"m.py": "def f():\n    return foo(bar) + 2\n"})
    ts = TransactionStore(store.project_root)
    planner = ExtractVariablePlanner(store, ts)
    # select "foo(bar)" on line 1: cols 11..19
    summary = planner.plan("m.py", (1, 11), (1, 19), "value")
    assert summary.edit_count == 2
    assert summary.files_affected == ["m.py"]


def test_extract_end_to_end_runnable(tmp_path):
    src = "def f():\n    return foo(bar) + 2\n"
    project, store = _project(tmp_path, {"m.py": src})
    ts = TransactionStore(store.project_root)
    summary = ExtractVariablePlanner(store, ts).plan("m.py", (1, 11), (1, 19), "value")
    result = TransactionApplier(store, ts).apply(summary.tx_id)
    assert result["status"] == "applied"
    assert (project / "m.py").read_text() == (
        "def f():\n    value = foo(bar)\n    return value + 2\n"
    )


def test_extract_preserves_indent(tmp_path):
    src = "class C:\n    def m(self):\n        x = compute(a) * 3\n"
    project, store = _project(tmp_path, {"m.py": src})
    ts = TransactionStore(store.project_root)
    # select "compute(a)" on line 2
    col = src.split("\n")[2].index("compute")
    summary = ExtractVariablePlanner(store, ts).plan(
        "m.py", (2, col), (2, col + len("compute(a)")), "c"
    )
    TransactionApplier(store, ts).apply(summary.tx_id)
    out = (project / "m.py").read_text()
    assert "        c = compute(a)\n" in out
    assert "        x = c * 3\n" in out


def test_invalid_name_rejected(tmp_path):
    project, store = _project(tmp_path, {"m.py": "x = 1 + 2\n"})
    ts = TransactionStore(store.project_root)
    with pytest.raises(ExtractVariableError, match="identifier"):
        ExtractVariablePlanner(store, ts).plan("m.py", (0, 4), (0, 5), "1bad")


def test_missing_file_rejected(tmp_path):
    project, store = _project(tmp_path, {"m.py": "x = 1\n"})
    ts = TransactionStore(store.project_root)
    with pytest.raises(ExtractVariableError, match="not found"):
        ExtractVariablePlanner(store, ts).plan("nope.py", (0, 0), (0, 1), "y")
