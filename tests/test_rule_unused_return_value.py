"""Tests for the unused-return-value builtin rule and the binder's
``result_used`` CALL-reference fact (TASK-80)."""

from __future__ import annotations

from pypeeker.check.builtin.unused_return_value import (
    UNUSED_RETURN_VALUE,
    unused_return_value,
)
from pypeeker.check.context import CheckContext
from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.serialize import from_dict, from_json, to_dict, to_json


def _call_refs(index: FileIndex, name: str) -> list[Reference]:
    """All CALL references whose symbol_id's leaf name is ``name``."""
    return [
        r
        for r in index.references
        if r.kind is ReferenceKind.CALL
        and r.symbol_id.replace(":", ".").rsplit(".", 1)[-1] == name
    ]


# ── binder fact: result_used ────────────────────────────────────────────────


class TestBinderResultUsed:
    def test_bare_call_statement_is_discarded(self, bind_source):
        index = bind_source("def g() -> int:\n    return 1\n\ndef f():\n    g()\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is False

    def test_bare_method_call_statement_is_discarded(self, bind_source):
        src = (
            "class C:\n"
            "    def m(self) -> int:\n"
            "        return 1\n"
            "    def run(self):\n"
            "        self.m()\n"
        )
        index = bind_source(src)
        (ref,) = _call_refs(index, "m")
        assert ref.symbol_id == "test:C.m"
        assert ref.result_used is False

    def test_unresolved_method_call_statement_is_discarded(self, bind_source):
        index = bind_source("def f(obj):\n    obj.do()\n")
        (ref,) = _call_refs(index, "do")
        assert ref.symbol_id == "<unresolved>.do"
        assert ref.result_used is False

    def test_call_in_assignment_is_used(self, bind_source):
        index = bind_source("def f():\n    x = g()\n    return x\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is True

    def test_call_in_return_is_used(self, bind_source):
        index = bind_source("def f():\n    return g()\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is True

    def test_call_in_argument_position_is_used(self, bind_source):
        index = bind_source("def f():\n    h(g())\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is True
        # The enclosing call h(...) is itself a bare statement, so discarded.
        (outer,) = _call_refs(index, "h")
        assert outer.result_used is False

    def test_call_in_comparison_is_used(self, bind_source):
        index = bind_source("def f():\n    if g() == 1:\n        pass\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is True

    def test_awaited_bare_call_is_discarded(self, bind_source):
        index = bind_source("async def f():\n    await g()\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is False

    def test_awaited_call_in_assignment_is_used(self, bind_source):
        index = bind_source("async def f():\n    x = await g()\n    return x\n")
        (ref,) = _call_refs(index, "g")
        assert ref.result_used is True

    def test_awaited_bare_method_call_is_discarded(self, bind_source):
        src = (
            "class C:\n"
            "    async def m(self) -> int:\n"
            "        return 1\n"
            "    async def run(self):\n"
            "        await self.m()\n"
        )
        index = bind_source(src)
        (ref,) = _call_refs(index, "m")
        assert ref.result_used is False

    def test_chained_call_inner_used_outer_discarded(self, bind_source):
        index = bind_source("def f(obj):\n    obj.make().run()\n")
        (inner,) = _call_refs(index, "make")
        (outer,) = _call_refs(index, "run")
        assert inner.result_used is True  # receiver of .run()
        assert outer.result_used is False

    def test_method_call_used_as_receiver_argument(self, bind_source):
        index = bind_source("def f(obj):\n    x = obj.make()\n    return x\n")
        (ref,) = _call_refs(index, "make")
        assert ref.result_used is True

    def test_non_call_references_stay_used(self, bind_source):
        index = bind_source("def g():\n    return 1\n\ndef f():\n    return g\n")
        reads = [
            r
            for r in index.references
            if r.kind is ReferenceKind.READ and r.symbol_id == "test:g"
        ]
        assert reads and all(r.result_used is True for r in reads)


class TestSerialization:
    def test_round_trip_preserves_result_used(self, bind_source):
        index = bind_source("def f():\n    g()\n    x = h()\n    return x\n")
        restored = from_json(FileIndex, to_json(index))
        (g_ref,) = _call_refs(restored, "g")
        (h_ref,) = _call_refs(restored, "h")
        assert g_ref.result_used is False
        assert h_ref.result_used is True

    def test_old_index_without_field_defaults_to_used(self, bind_source):
        # Forward compatibility: an index written before result_used existed
        # has no such key — deserialization falls back to the default (True).
        index = bind_source("def f():\n    g()\n")
        data = to_dict(index)
        for ref in data["references"]:
            ref.pop("result_used", None)
        restored = from_dict(FileIndex, data)
        (ref,) = _call_refs(restored, "g")
        assert ref.result_used is True


# ── the rule ────────────────────────────────────────────────────────────────


def _run(indexed_project, files, options=None):
    _, store = indexed_project(files)
    indexes = [
        idx
        for idx in (store.load(p) for p in store.list_indexed_files())
        if idx is not None
    ]
    context = CheckContext(store, indexes)
    return unused_return_value(context, options or {})


ALWAYS_DISCARDED_SRC = """\
def compute() -> int:
    return 1

def run():
    compute()
"""

MIXED_SRC = """\
def compute() -> int:
    return 1

def run():
    compute()
    x = compute()
    return x
"""


class TestFlagged:
    def test_always_discarded_function_flagged(self, indexed_project):
        violations = _run(indexed_project, {"pkg/mod.py": ALWAYS_DISCARDED_SRC})
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == UNUSED_RETURN_VALUE
        assert v.file_path == "pkg/mod.py"
        assert v.line == 1  # anchored at the definition, 1-indexed
        assert "'pkg.mod:compute'" in v.message
        assert "'int'" in v.message
        assert "pkg/mod.py:5" in v.message  # call site listed

    def test_method_always_discarded_flagged(self, indexed_project):
        src = (
            "class Svc:\n"
            "    def helper(self) -> int:\n"
            "        return 1\n"
            "    def run(self):\n"
            "        self.helper()\n"
        )
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "'pkg.mod:Svc.helper'" in violations[0].message
        assert violations[0].line == 2

    def test_cross_file_call_via_import_flagged(self, indexed_project):
        files = {
            "pkg/lib.py": "def compute() -> int:\n    return 1\n",
            "pkg/app.py": (
                "from pkg.lib import compute\n\ndef run():\n    compute()\n"
            ),
        }
        violations = _run(indexed_project, files)
        assert len(violations) == 1
        v = violations[0]
        assert v.file_path == "pkg/lib.py"
        assert "'pkg.lib:compute'" in v.message
        assert "pkg/app.py:4" in v.message

    def test_awaited_discarded_async_function_flagged(self, indexed_project):
        src = (
            "async def fetch() -> int:\n"
            "    return 1\n\n"
            "async def run():\n"
            "    await fetch()\n"
        )
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "'pkg.mod:fetch'" in violations[0].message

    def test_message_lists_at_most_three_call_sites(self, indexed_project):
        src = (
            "def compute() -> int:\n"
            "    return 1\n\n"
            "def run():\n"
            "    compute()\n"
            "    compute()\n"
            "    compute()\n"
            "    compute()\n"
        )
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        msg = violations[0].message
        assert "all 4 call site(s)" in msg
        assert msg.count("pkg/mod.py:") == 3
        assert "+1 more" in msg


class TestNotFlagged:
    def test_used_somewhere_not_flagged(self, indexed_project):
        assert _run(indexed_project, {"pkg/mod.py": MIXED_SRC}) == []

    def test_none_returning_not_flagged(self, indexed_project):
        src = "def proc() -> None:\n    pass\n\ndef run():\n    proc()\n"
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_string_none_annotation_not_flagged(self, indexed_project):
        src = 'def proc() -> "None":\n    pass\n\ndef run():\n    proc()\n'
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_unannotated_not_flagged(self, indexed_project):
        src = "def compute():\n    return 1\n\ndef run():\n    compute()\n"
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_zero_calls_not_flagged(self, indexed_project):
        # Never-called functions are dead-code-rule territory.
        src = "def compute() -> int:\n    return 1\n"
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_dunder_not_flagged(self, indexed_project):
        src = (
            "class Box:\n"
            "    def __exit__(self, *a) -> bool:\n"
            "        return False\n"
            "    def run(self):\n"
            "        self.__exit__()\n"
        )
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_function_escaping_as_value_not_flagged(self, indexed_project):
        # `cb = compute` aliases the function; calls through the alias are
        # invisible, so the conservative answer is silence.
        src = (
            "def compute() -> int:\n"
            "    return 1\n\n"
            "def run():\n"
            "    compute()\n"
            "    cb = compute\n"
            "    return cb\n"
        )
        assert _run(indexed_project, {"pkg/mod.py": src}) == []


class TestOptions:
    def test_allow_suppresses_matching_symbol(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": ALWAYS_DISCARDED_SRC},
            {"allow": ["pkg.mod:compute"]},
        )
        assert violations == []

    def test_allow_matches_module_path(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": ALWAYS_DISCARDED_SRC},
            {"allow": ["pkg.mod"]},
        )
        assert violations == []

    def test_allow_glob_pattern(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": ALWAYS_DISCARDED_SRC},
            {"allow": ["pkg.*:comp*"]},
        )
        assert violations == []

    def test_allow_does_not_suppress_others(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": ALWAYS_DISCARDED_SRC},
            {"allow": ["pkg.other:*"]},
        )
        assert len(violations) == 1


class TestRegistration:
    def test_registered_as_project_rule(self):
        # Importing pypeeker.check.builtin triggers auto-discovery.
        import pypeeker.check.builtin  # noqa: F401
        from pypeeker.check.rules import get_project_rule

        assert get_project_rule(UNUSED_RETURN_VALUE) is unused_return_value

    def test_opt_in_not_enabled_by_default(self):
        import tomllib
        from pathlib import Path

        # Anchor to this file, not cwd — earlier tests may chdir.
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        enabled = data["tool"]["pypeeker"]["rules"]
        assert UNUSED_RETURN_VALUE not in enabled
