"""Tests for the no-hidden-global-mutation builtin rule (TASK-76)."""

from __future__ import annotations

from pypeeker.check.builtin.no_hidden_global_mutation import (
    NO_HIDDEN_GLOBAL_MUTATION,
    no_hidden_global_mutation,
)
from pypeeker.check.context import CheckContext


def _run(indexed_project, files, options=None):
    _, store = indexed_project(files)
    indexes = [
        idx
        for idx in (store.load(p) for p in store.list_indexed_files())
        if idx is not None
    ]
    context = CheckContext(store, indexes)
    return no_hidden_global_mutation(context, options or {})


GLOBAL_REBIND_SRC = """\
COUNT = 0

def bump():
    global COUNT
    COUNT = COUNT + 1
"""

LIST_APPEND_SRC = """\
ITEMS = []

def add(item):
    ITEMS.append(item)
"""

DICT_SUBSCRIPT_SRC = """\
REGISTRY = {}

def register(name, value):
    REGISTRY[name] = value
"""

IMPORT_ATTR_SRC = """\
import config

def configure(value):
    config.value = value
"""

LOCAL_MUTATION_SRC = """\
ITEMS = []

def build():
    items = []
    items.append(1)
    d = {}
    d["k"] = 2
    x = 0
    x = x + 1
    return items, d, x
"""


class TestFlaggedShapes:
    def test_global_keyword_rebind_flagged(self, indexed_project):
        violations = _run(indexed_project, {"pkg/mod.py": GLOBAL_REBIND_SRC})
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == NO_HIDDEN_GLOBAL_MUTATION
        assert "'pkg.mod:bump'" in v.message
        assert "'pkg.mod:COUNT'" in v.message
        assert v.line == 5  # the assignment line, 1-indexed
        assert v.file_path == "pkg/mod.py"

    def test_module_level_list_append_flagged(self, indexed_project):
        violations = _run(indexed_project, {"pkg/mod.py": LIST_APPEND_SRC})
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == NO_HIDDEN_GLOBAL_MUTATION
        assert "'.append()'" in v.message
        assert "'pkg.mod:ITEMS'" in v.message
        assert "'pkg.mod:add'" in v.message
        assert v.line == 4

    def test_module_level_dict_subscript_write_flagged(self, indexed_project):
        violations = _run(indexed_project, {"pkg/mod.py": DICT_SUBSCRIPT_SRC})
        assert len(violations) == 1
        v = violations[0]
        assert "'pkg.mod:REGISTRY'" in v.message
        assert "'pkg.mod:register'" in v.message
        assert v.line == 4

    def test_imported_module_attribute_write_flagged(self, indexed_project):
        violations = _run(indexed_project, {"pkg/mod.py": IMPORT_ATTR_SRC})
        assert len(violations) == 1
        v = violations[0]
        assert "'value'" in v.message
        assert "imported module 'config'" in v.message
        assert "'pkg.mod:configure'" in v.message
        assert v.line == 4

    def test_global_augmented_assignment_flagged(self, indexed_project):
        src = "COUNT = 0\n\ndef bump():\n    global COUNT\n    COUNT += 1\n"
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        v = violations[0]
        assert "'pkg.mod:COUNT'" in v.message
        assert "'pkg.mod:bump'" in v.message
        assert v.line == 5

    def test_nested_function_rebind_attributed_to_inner(self, indexed_project):
        src = (
            "COUNT = 0\n\n"
            "def outer():\n"
            "    def inner():\n"
            "        global COUNT\n"
            "        COUNT = 1\n"
            "    return inner\n"
        )
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "'pkg.mod:outer.inner'" in violations[0].message
        assert "'pkg.mod:COUNT'" in violations[0].message

    def test_method_body_is_covered(self, indexed_project):
        src = "STATE = {}\n\nclass Svc:\n    def set(self, k, v):\n        STATE[k] = v\n"
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert len(violations) == 1
        assert "'pkg.mod:STATE'" in violations[0].message


class TestNotFlagged:
    def test_local_mutation_not_flagged(self, indexed_project):
        assert _run(indexed_project, {"pkg/mod.py": LOCAL_MUTATION_SRC}) == []

    def test_module_scope_initialization_not_flagged(self, indexed_project):
        src = 'ITEMS = []\nITEMS.append(1)\nREGISTRY = {}\nREGISTRY["k"] = 2\n'
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_nonlocal_write_not_flagged(self, indexed_project):
        # nonlocal targets a function-scope symbol, not module scope.
        src = (
            "def outer():\n"
            "    count = 0\n"
            "    def inner():\n"
            "        nonlocal count\n"
            "        count = count + 1\n"
            "    return inner\n"
        )
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_pure_read_of_module_variable_not_flagged(self, indexed_project):
        src = "LIMIT = 10\n\ndef check(n):\n    return n < LIMIT\n"
        assert _run(indexed_project, {"pkg/mod.py": src}) == []

    def test_non_mutator_method_call_not_flagged(self, indexed_project):
        src = "ITEMS = []\n\ndef count():\n    return ITEMS.count(1)\n"
        assert _run(indexed_project, {"pkg/mod.py": src}) == []


class TestOptions:
    def test_allow_suppresses_matching_function(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": LIST_APPEND_SRC},
            {"allow": ["pkg.mod:add"]},
        )
        assert violations == []

    def test_allow_matches_module_path(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": GLOBAL_REBIND_SRC},
            {"allow": ["pkg.mod"]},
        )
        assert violations == []

    def test_allow_glob_pattern(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": DICT_SUBSCRIPT_SRC},
            {"allow": ["pkg.*:register*"]},
        )
        assert violations == []

    def test_allow_does_not_suppress_other_functions(self, indexed_project):
        violations = _run(
            indexed_project,
            {"pkg/mod.py": LIST_APPEND_SRC},
            {"allow": ["pkg.other:*"]},
        )
        assert len(violations) == 1

    def test_extra_mutators_extends_table(self, indexed_project):
        src = "BUS = make_bus()\n\ndef send(msg):\n    BUS.publish(msg)\n"
        # 'publish' is not in the default collection-mutation table.
        assert _run(indexed_project, {"pkg/mod.py": src}) == []
        violations = _run(
            indexed_project,
            {"pkg/mod.py": src},
            {"extra-mutators": ["publish"]},
        )
        assert len(violations) == 1
        assert "'.publish()'" in violations[0].message


class TestRegistration:
    def test_registered_as_project_rule(self):
        # Importing pypeeker.check.builtin triggers auto-discovery.
        import pypeeker.check.builtin  # noqa: F401
        from pypeeker.check.rules import get_project_rule

        assert (
            get_project_rule(NO_HIDDEN_GLOBAL_MUTATION)
            is no_hidden_global_mutation
        )

    def test_violations_are_sorted_and_deduplicated(self, indexed_project):
        src = (
            "A = []\nB = {}\n\n"
            "def f(x):\n"
            "    A.append(x)\n"
            "    B['k'] = x\n"
        )
        violations = _run(indexed_project, {"pkg/mod.py": src})
        assert violations == sorted(violations)
        assert len(violations) == len(set(violations)) == 2
