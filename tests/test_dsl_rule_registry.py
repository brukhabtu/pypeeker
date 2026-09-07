"""The new engine's custom-rule extension point: ``register_dsl_rule`` and ``dsl_rule``.

``[tool.pypeeker].plugins`` names modules a consumer project imports for their
side effects; what those modules *do* is call
:func:`~pypeeker.dsl.register_dsl_rule`, and what the run service then does is
resolve every configured rule name through :func:`~pypeeker.dsl.dsl_rule`. This
file pins that contract: a custom rule is reachable by its own ``rule_id``, a
second registration of one id replaces the first, a custom rule **shadows** a
builtin of the same id, and an unknown name raises loudly naming every id that
is reachable — builtin and custom alike.

The shadowing direction is deliberate and inverts the frozen
``check.rules.get_rule``, which lets a builtin win a name clash. It matches
:func:`pypeeker.analysis.traits.register_trait`, whose single overridable table
the DSL registry is modelled on.
"""

import pytest

from pypeeker.dsl import (
    Corpus,
    DslRule,
    dsl_rule,
    install_expressions,
    register_dsl_rule,
    symbols,
)
from pypeeker.dsl import rules as rules_module
from pypeeker.dsl.errors import UnknownExpressionError

_SOURCES = {"m.py": "def alpha():\n    pass\n"}


@pytest.fixture(autouse=True)
def isolated_registry():
    """Snapshot and restore the custom-rule table so registrations cannot leak."""
    saved = dict(rules_module._REGISTERED)
    try:
        yield
    finally:
        rules_module._REGISTERED.clear()
        rules_module._REGISTERED.update(saved)


@pytest.fixture
def corpus(indexed_project):
    """A one-module corpus with no source-root filter."""
    install_expressions()
    _, store = indexed_project(_SOURCES)
    return Corpus(store, ())


def _rule(rule_id: str, message: str = "custom saw {name}") -> DslRule:
    """A minimal custom rule: every symbol, worded by ``message``."""
    return DslRule(rule_id=rule_id, build=lambda options: symbols(), message=message)


def test_a_registered_rule_is_reachable_by_its_own_id():
    """Registration keys on ``rule.rule_id``; nothing names the rule twice."""
    custom = _rule("acme-house-style")
    register_dsl_rule(custom)
    assert dsl_rule("acme-house-style") is custom


def test_a_registered_rule_runs_over_a_corpus(corpus):
    """The extension point yields a rule the engine can actually evaluate."""
    register_dsl_rule(_rule("acme-house-style"))
    findings = dsl_rule("acme-house-style").findings({}, corpus)
    assert sorted(f.message for f in findings) == ["custom saw alpha", "custom saw m"]
    assert {f.rule for f in findings} == {"acme-house-style"}


def test_register_returns_the_rule_it_was_given():
    """Returning the argument is what makes it usable as a decorator."""
    custom = _rule("acme-house-style")
    assert register_dsl_rule(custom) is custom


def test_the_last_registration_of_an_id_wins():
    """Two plugins claiming one id: the one imported last is the one that runs."""
    register_dsl_rule(_rule("acme-house-style", "first"))
    second = _rule("acme-house-style", "second")
    register_dsl_rule(second)
    assert dsl_rule("acme-house-style") is second


def test_a_custom_rule_shadows_a_builtin_of_the_same_id():
    """Custom wins the clash — inverting the frozen engine's builtin-wins precedence."""
    builtin = dsl_rule("prefer-tuple")
    custom = _rule("prefer-tuple")
    register_dsl_rule(custom)
    assert dsl_rule("prefer-tuple") is custom
    assert dsl_rule("prefer-tuple") is not builtin


def test_an_unknown_id_raises_naming_builtin_and_custom_ids():
    """The refusal is a discovery surface: every reachable id is in ``valid``."""
    register_dsl_rule(_rule("acme-house-style"))
    with pytest.raises(UnknownExpressionError) as excinfo:
        dsl_rule("nope")
    valid = set(excinfo.value.valid)
    assert "prefer-tuple" in valid
    assert "acme-house-style" in valid
