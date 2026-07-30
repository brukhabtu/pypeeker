"""Tests for the builtin no-import-cycles rule (check/builtin/no_import_cycles)."""

from __future__ import annotations

import pytest

from pypeeker.check.builtin.no_import_cycles import (
    NO_IMPORT_CYCLES,
    _no_import_cycles as no_import_cycles,
)
from pypeeker.check.context import CheckContext


@pytest.fixture
def run_rule(indexed_project):
    """Index ``files``, build a CheckContext, run the rule -> violations."""

    def _run(files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        context = CheckContext(store, indexes)
        return no_import_cycles(context, options or {})

    return _run


def _members(violation):
    """The sorted module names named in a cycle violation's message."""
    head = violation.message.split(":", 1)[1].split(" — ", 1)[0]
    return sorted(m.strip() for m in head.split(","))


def test_flags_two_module_cycle(run_rule):
    violations = run_rule(
        {
            "pkg/a.py": "from pkg.b import b_thing\n\ndef a_thing():\n    return 1\n",
            "pkg/b.py": "from pkg.a import a_thing\n\ndef b_thing():\n    return 1\n",
        }
    )
    assert len(violations) == 1
    assert violations[0].rule == NO_IMPORT_CYCLES
    assert _members(violations[0]) == ["pkg.a", "pkg.b"]


def test_flags_three_module_cycle(run_rule):
    violations = run_rule(
        {
            "pkg/a.py": "from pkg.b import b_thing\n\ndef a_thing():\n    return 1\n",
            "pkg/b.py": "from pkg.c import c_thing\n\ndef b_thing():\n    return 1\n",
            "pkg/c.py": "from pkg.a import a_thing\n\ndef c_thing():\n    return 1\n",
        }
    )
    assert len(violations) == 1
    assert _members(violations[0]) == ["pkg.a", "pkg.b", "pkg.c"]


def test_type_checking_hidden_cycle_is_flagged(run_rule):
    # a -> b is guarded under TYPE_CHECKING; the binder still recovers it, so
    # the cycle must be caught just like a runtime one.
    violations = run_rule(
        {
            "pkg/a.py": (
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from pkg.b import b_thing\n"
                "\n"
                "def a_thing():\n"
                "    return 1\n"
            ),
            "pkg/b.py": "from pkg.a import a_thing\n\ndef b_thing():\n    return 1\n",
        }
    )
    assert len(violations) == 1
    assert _members(violations[0]) == ["pkg.a", "pkg.b"]


def test_function_local_import_does_not_form_a_cycle(run_rule):
    # A deferred (function-body) import runs only when called, so it creates no
    # module-load cycle. This is the idiomatic way to express mutual recursion
    # across modules (as the binder's visitors do with visit_node) and must not
    # be flagged.
    assert (
        run_rule(
            {
                "pkg/a.py": (
                    "from pkg.b import b_thing\n"
                    "\n"
                    "def a_thing():\n"
                    "    return b_thing()\n"
                ),
                "pkg/b.py": (
                    "def b_thing():\n"
                    "    from pkg.a import a_thing\n"
                    "    return a_thing\n"
                ),
            }
        )
        == []
    )


def test_acyclic_graph_has_no_findings(run_rule):
    assert (
        run_rule(
            {
                "pkg/a.py": "from pkg.b import b_thing\n\ndef a_thing():\n    return 1\n",
                "pkg/b.py": "from pkg.c import c_thing\n\ndef b_thing():\n    return 1\n",
                "pkg/c.py": "def c_thing():\n    return 1\n",
            }
        )
        == []
    )


def test_external_imports_do_not_form_a_cycle(run_rule):
    # Importing stdlib / third-party never closes a project cycle.
    assert (
        run_rule(
            {"pkg/a.py": "import os\nfrom collections import abc\n\nx = os.getcwd()\n"}
        )
        == []
    )


def test_allow_exempts_a_named_cycle(run_rule):
    files = {
        "pkg/a.py": "from pkg.b import b_thing\n\ndef a_thing():\n    return 1\n",
        "pkg/b.py": "from pkg.a import a_thing\n\ndef b_thing():\n    return 1\n",
    }
    assert run_rule(files, {"allow": [["pkg.a", "pkg.b"]]}) == []
    # A non-matching allow entry does not suppress it.
    assert len(run_rule(files, {"allow": [["pkg.a", "pkg.z"]]})) == 1


def test_enabled_as_self_lint_gate():
    # Enabled on pypeeker itself: the source is cycle-free and the rule keeps
    # it that way (TASK-114).
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert NO_IMPORT_CYCLES in data["tool"]["pypeeker"]["rules"]
