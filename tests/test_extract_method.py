"""Tests for extract-method refactoring."""

from __future__ import annotations

import ast
import pytest

from pypeeker.refactor.applier import TransactionApplier
from pypeeker.refactor.extract import ExtractMethodError, ExtractMethodPlanner
from pypeeker.storage import IndexStore, TransactionStore


def _project(tmp_path, files):
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
    store = IndexStore(tmp_path)
    from pypeeker.adapters.python_adapter import PythonAdapter
    from pypeeker.binder.binder import bind
    from pypeeker.paths import module_path_from
    ad = PythonAdapter()
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        b = content.encode()
        store.save(bind(ad, name, b, ad.parse(b).root_node,
                        module_path=module_path_from(name)))
    return tmp_path, store


def test_extract_with_params_and_return(tmp_path):
    src = "def f(a, b):\n    c = a + b\n    return c\n"
    project, store = _project(tmp_path, {"m.py": src})
    ts = TransactionStore(store.project_root)
    summary = ExtractMethodPlanner(store, ts).plan("m.py", 1, 1, "add")
    TransactionApplier(store, ts).apply(summary.tx_id)
    out = (project / "m.py").read_text()
    assert out == (
        "def add(a, b):\n"
        "    c = a + b\n"
        "    return c\n"
        "\n\n"
        "def f(a, b):\n"
        "    c = add(a, b)\n"
        "    return c\n"
    )
    ast.parse(out)  # valid Python


def test_extract_multi_statement(tmp_path):
    src = "def f(a, b):\n    d = 10\n    c = a + b\n    e = c + d\n    return e\n"
    project, store = _project(tmp_path, {"m.py": src})
    ts = TransactionStore(store.project_root)
    summary = ExtractMethodPlanner(store, ts).plan("m.py", 2, 3, "compute")
    TransactionApplier(store, ts).apply(summary.tx_id)
    out = (project / "m.py").read_text()
    ast.parse(out)
    assert "def compute(a, b, d):" in out
    assert "    return e\n" in out
    assert "    e = compute(a, b, d)\n" in out


def test_refuses_escape(tmp_path):
    src = "def f(a):\n    if a:\n        return 1\n    return 2\n"
    project, store = _project(tmp_path, {"m.py": src})
    ts = TransactionStore(store.project_root)
    with pytest.raises(ExtractMethodError, match="return/break/continue"):
        ExtractMethodPlanner(store, ts).plan("m.py", 1, 2, "g")


def test_refuses_method(tmp_path):
    src = "class C:\n    def m(self, a):\n        c = a + 1\n        return c\n"
    project, store = _project(tmp_path, {"m.py": src})
    ts = TransactionStore(store.project_root)
    with pytest.raises(ExtractMethodError, match="top-level"):
        ExtractMethodPlanner(store, ts).plan("m.py", 2, 2, "g")


def test_invalid_name(tmp_path):
    project, store = _project(tmp_path, {"m.py": "def f():\n    x = 1\n"})
    ts = TransactionStore(store.project_root)
    with pytest.raises(ExtractMethodError, match="identifier"):
        ExtractMethodPlanner(store, ts).plan("m.py", 1, 1, "1bad")


def test_refuses_non_utf8_source(tmp_path):
    """A file whose bytes don't decode as UTF-8 is refused, even when the
    selected range itself is pure ASCII — the whole file must be
    line-splittable to build the edits."""
    from pypeeker.adapters.python_adapter import PythonAdapter
    from pypeeker.binder.binder import bind
    from pypeeker.paths import module_path_from
    from pypeeker.refactor.preconditions import SourceIsUtf8

    project, store = _project(tmp_path, {"m.py": "def f(a):\n    c = a + 1\n    return c\n"})
    latin1 = b'def f(a):\n    c = a + 1\n    s = "caf\xe9"\n    return c\n'
    (project / "m.py").write_bytes(latin1)
    ad = PythonAdapter()
    store.save(
        bind(
            ad, "m.py", latin1, ad.parse(latin1).root_node,
            module_path=module_path_from("m.py"),
        )
    )
    ts = TransactionStore(store.project_root)
    with pytest.raises(ExtractMethodError) as exc:
        ExtractMethodPlanner(store, ts).plan("m.py", 1, 1, "g")
    assert exc.value.precondition == "source-is-utf8"
    assert str(exc.value) == SourceIsUtf8(latin1, "m.py").evaluate().reason
    assert ts.list() == []
