"""The two references-universe fields phase 3f added: ``runs_at_import`` and ``module_scope_id``.

Both are pointwise ports of a frozen helper in
``check.builtin.import_time_side_effects`` — ``_import_time_scope_ids``'s inner
walk and ``_module_path`` — and both are published as model facts on every
references row rather than reconstructed inside a rule.

The differential oracle grades them only through the rule that reads them, and
only on the scope shapes a corpus happens to contain. These tests pin the ones
that decide the walk rather than exercise it: a chain deeper than one scope, a
comprehension nested in a class body, the function boundary that breaks the
chain in both directions, a scope id naming no scope at all, and the
module-less file where ``module_scope_id`` and ``module`` are deliberately
different answers.
"""

import pytest

from pypeeker.dsl.universes import _Env, universe_fields
from pypeeker.models import ScopeKind

NESTED = '''"""Scopes at every depth, so the walk has something to walk."""


def now() -> float:
    """A callee, so every call site below is a CALL reference."""
    return 1.0


AT_MODULE = now()


class Outer:
    """A class body at module scope runs during the import."""

    IN_CLASS = now()

    class Inner:
        """A class inside a class at module scope: still import time."""

        IN_NESTED_CLASS = now()

    IN_COMPREHENSION = [now() for _ in range(2)]


def deferred() -> float:
    """A function body waits to be called."""

    class Buried:
        """A class inside a function waits with it."""

        IN_BURIED_CLASS = now()

    return [now() for _ in range(2)][0]
'''


@pytest.fixture
def nested_env(indexed_project):
    """The ``_Env`` for a file carrying one call at every scope depth."""
    _, store = indexed_project({"nested.py": NESTED})
    return _Env.of(store.load("nested.py"))


def _call_lines(env, *, expected: bool) -> set[int]:
    """1-based lines of the CALL references whose scope answers ``expected``."""
    return {
        ref.location.span.start.line + 1
        for ref in env.index.references
        if ref.kind.value == "call" and env.runs_at_import(ref.in_scope_id) is expected
    }


def test_the_module_scope_and_every_class_chain_above_it_run_at_import(nested_env):
    """MODULE is True; CLASS and COMPREHENSION inherit their parent's answer.

    The nested class and the comprehension are the shapes that separate "every
    enclosing scope up to the module" from "the immediate scope" — a port
    testing only ``scope.kind`` would answer True inside ``deferred`` too.
    """
    assert _call_lines(nested_env, expected=True) == {9, 15, 20, 22}


def test_a_function_body_breaks_the_chain_for_everything_under_it(nested_env):
    """A class body and a comprehension inside a function wait for the call."""
    assert _call_lines(nested_env, expected=False) == {31, 33}


def test_a_scope_id_naming_no_scope_in_this_file_is_not_import_time(nested_env):
    """The answer the frozen membership test gives: that set holds this file's ids only."""
    assert nested_env.runs_at_import("nested:no.such.scope") is False
    assert nested_env.runs_at_import(None) is False


def test_the_module_scope_id_is_the_files_module_path(nested_env):
    assert nested_env.module_scope_id == "nested"
    assert nested_env.module == "nested"


ROOT_PACKAGE = '''"""A source root that is itself a package: the module path collapses to "".""" '''


def test_a_module_less_file_reports_an_empty_module_scope_id(indexed_project):
    """``module_scope_id`` is the frozen ``_module_path``; ``module`` is not.

    ``binder.binder._emit_module_symbol`` emits no MODULE symbol when the module
    path is empty, so ``_Env.of``'s display fallback puts the FILE PATH in
    ``module``. The frozen ``_module_path`` returns ``""`` there, and it is what
    ``import-time-side-effects``' allow patterns are matched against — so
    publishing ``module`` in its place would change which patterns match on this
    shape.
    """
    _, store = indexed_project({"__init__.py": ROOT_PACKAGE})
    env = _Env.of(store.load("__init__.py"))
    assert env.module_scope_id == ""
    assert env.module == "__init__.py"


def test_the_empty_module_scope_id_is_memoized_rather_than_recomputed(indexed_project):
    """``""`` is a legitimate answer, so the memo may not be keyed on truthiness."""
    _, store = indexed_project({"__init__.py": ROOT_PACKAGE})
    index = store.load("__init__.py")
    env = _Env.of(index)
    assert env.module_scope_id == ""
    object.__setattr__(index, "scopes", ())
    # a recomputing property would still answer "" here, so the memo is proven
    # by mutating the source out from under it and asking again
    assert "module_scope_id" in env._cache
    assert env.module_scope_id == ""


def test_both_fields_are_declared_on_the_references_universe():
    """Fields are declared, not discovered: a rule may only read what is published."""
    fields = universe_fields("references")
    assert "runs_at_import" in fields
    assert "module_scope_id" in fields


def test_every_module_scope_the_binder_records_answers_true(nested_env):
    """The MODULE arm of the walk, read straight off the scope table."""
    modules = [
        scope.scope_id
        for scope in nested_env.index.scopes
        if scope.kind is ScopeKind.MODULE
    ]
    assert modules == ["nested"]
    assert nested_env.runs_at_import(modules[0]) is True
