"""The cross-file residue (phase 3d): under-exposed-access and unused-return-value.

``scripts/differential-check.py`` grades both against the frozen engine on this
repository — 75 and 11 findings respectively — so the *majority* path of each is
proven by the oracle. This file is the other half: the shapes the oracle cannot
reach on a real corpus, and the ones it can only see by accident.

For ``under-exposed-access`` those are the ``allow`` option, a custom
``test-globs``, and the ``___`` case that separates the frozen ``_is_dunder``
from the four-character glob ``matches("__*__")`` — a distinction
``dsl-rewrite.md``'s ledger records as a spec note and that nothing on this
repository exercises.

For ``unused-return-value`` they are the ``+N more`` message tail, both quoted
``-> None`` spellings, the two value-escape kinds, the zero-call-site skip and
the ``allow`` option.
"""

import tomllib
from pathlib import Path

import pytest

from pypeeker.dsl import (
    ACCESS_TEST_GLOBS,
    DEFAULT_TEST_GLOBS,
    DYNAMIC_ACCESS_WEAKENED_RULES,
    Corpus,
    Reach,
    dsl_rule,
)
from pypeeker.dsl.visibility import _dunder_clause, _definition_dunder_clause
from pypeeker.models import Confidence

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "scripts" / "parity-manifest.toml"

CLAIMED_HERE = ("under-exposed-access", "unused-return-value")


@pytest.fixture
def corpus_of(indexed_project):
    """Build a corpus over ``files`` with no source-root filter.

    Paths carry no ``src/`` prefix, for the reason
    ``tests/test_dsl_visibility_rules.py`` gives: the binder derives a module id
    from the indexed path, so ``owner.py`` is the module ``owner`` and a
    sibling's ``from owner import ...`` resolves.
    """

    def _build(files: dict[str, str]) -> Corpus:
        _, store = indexed_project(files)
        return Corpus(store, ())

    return _build


def _findings(rule_id: str, corpus: Corpus, options: dict | None = None):
    return dsl_rule(rule_id).findings(options or {}, corpus)


def _messages(rule_id: str, corpus: Corpus, options: dict | None = None) -> list[str]:
    return [f.message for f in _findings(rule_id, corpus, options)]


# ---------------------------------------------------------------------------
# both rules are graded
# ---------------------------------------------------------------------------


def test_both_rules_are_claimed_by_the_parity_manifest():
    """Ported means graded: a rule in ``RULES`` no target grades is an unchecked claim."""
    manifest = tomllib.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(CLAIMED_HERE) <= set(manifest["claimed"])


@pytest.mark.parametrize("rule_id", CLAIMED_HERE)
def test_both_rules_reach_project(rule_id):
    rule = dsl_rule(rule_id)
    builds = [rule.build] if hasattr(rule, "build") else [part.build for part in rule.parts]
    for build in builds:
        assert build({}).reach is Reach.PROJECT


# ---------------------------------------------------------------------------
# under-exposed-access
# ---------------------------------------------------------------------------

OWNER = '''"""Owns the private names everybody reaches for."""

_SECRET = 1
___ = 2


def _protected_fn() -> int:
    """A protected helper, used inside its own module too."""
    return _SECRET


def public_fn() -> int:
    """Public surface, never a reach-in."""
    return 1
'''

INTRUDER = '''"""Production code reaching into another module's privates."""

from owner import ___, _protected_fn, _SECRET, public_fn


def use() -> int:
    """Touch all four."""
    return _SECRET + _protected_fn() + ___ + public_fn()
'''

TEST_PROBE = '''"""A test file, by the eight-pattern default globs, reaching in."""

from owner import _SECRET


def test_it() -> int:
    """Touch one."""
    return _SECRET
'''


@pytest.fixture
def reach_in(corpus_of):
    """Three modules: an owner, a production reach-in, and a test reach-in."""
    return corpus_of({
        "owner.py": OWNER,
        "intruder.py": INTRUDER,
        "test_probe.py": TEST_PROBE,
    })


def test_a_production_reach_in_is_worded_with_the_defining_module(reach_in):
    assert (
        "protected '_SECRET' accessed from 'intruder' outside its defining module 'owner'"
        in _messages("under-exposed-access", reach_in)
    )


def test_a_test_file_reach_in_is_worded_as_accessed_from_tests(reach_in):
    """``test_probe.py`` matches the rule's own ``test_*.py`` default glob."""
    assert "protected '_SECRET' accessed from tests ('test_probe')" in _messages(
        "under-exposed-access", reach_in
    )


def test_the_two_wordings_partition_the_reference_sites(reach_in):
    """Complementary by construction: no site is reported twice, none is dropped."""
    messages = _messages("under-exposed-access", reach_in)
    from_tests = [m for m in messages if "accessed from tests" in m]
    outside = [m for m in messages if "outside its defining module" in m]
    assert len(from_tests) + len(outside) == len(messages)
    assert from_tests and outside


def test_a_reach_in_reports_at_declared(reach_in):
    """The frozen rule passes no confidence, so its findings land on the default."""
    assert {f.confidence for f in _findings("under-exposed-access", reach_in)} == {
        Confidence.DECLARED
    }


def test_a_public_target_is_never_a_reach_in(reach_in):
    assert not [m for m in _messages("under-exposed-access", reach_in) if "public_fn" in m]


def test_a_same_module_reference_to_a_private_name_is_silent(reach_in):
    """``_protected_fn`` reads ``_SECRET`` in ``owner.py``; that is not a reach-in."""
    assert not [
        m for m in _messages("under-exposed-access", reach_in) if "accessed from 'owner'" in m
    ]


def test_a_triple_underscore_name_is_skipped_like_the_frozen_is_dunder(reach_in):
    """The ledger's spec note, made expensive to undo.

    ``___`` is ``PRIVATE`` (``get_visibility`` needs ``len(name) > 4`` for
    ``DUNDER``), so it survives the visibility clause and reaches the dunder
    exclusion. The frozen ``_is_dunder`` — ``startswith`` and ``endswith``, no
    length test — skips it. ``matches("__*__")`` would not, and this rule would
    then fire where the frozen engine is silent.
    """
    assert not [m for m in _messages("under-exposed-access", reach_in) if "'___'" in m]


def test_the_dunder_clause_is_the_two_clause_spelling_not_the_four_char_glob():
    """Structural companion to the test above: the shape itself is pinned."""
    for clause in (_dunder_clause(), _definition_dunder_clause()):
        rendered = repr(clause)
        assert "'__*__'" not in rendered
        assert "'*__'" in rendered


def test_allow_exempts_a_target_by_its_symbol_id(reach_in):
    messages = _messages("under-exposed-access", reach_in, {"allow": ["owner:_SECRET"]})
    assert not [m for m in messages if "_SECRET" in m]
    assert [m for m in messages if "_protected_fn" in m]


def test_allow_exempts_a_target_by_its_module_path(reach_in):
    """``_allowed`` fnmatches the id *or* ``module_of(id)``; ``owner`` is the latter."""
    assert _messages("under-exposed-access", reach_in, {"allow": ["owner"]}) == []


def test_custom_test_globs_move_a_file_across_the_partition(reach_in):
    """A configured list *replaces* the default, so both files change sides.

    ``intruder.py`` becomes test code and ``test_probe.py`` stops being it —
    the frozen ``_as_str_list(...) or _DEFAULT_TEST_GLOBS`` is an override, not
    an addition.
    """
    messages = _messages("under-exposed-access", reach_in, {"test-globs": ["intruder.py"]})
    assert "protected '_SECRET' accessed from tests ('intruder')" in messages
    assert (
        "protected '_SECRET' accessed from 'test_probe' outside its defining module 'owner'"
        in messages
    )
    assert not [m for m in messages if "accessed from tests ('test_probe')" in m]


def test_the_rules_two_test_glob_defaults_are_not_the_same_constant():
    """``ACCESS_TEST_GLOBS`` is the frozen eight; ``DEFAULT_TEST_GLOBS`` is another rule's three."""
    assert ACCESS_TEST_GLOBS == (
        "tests/*",
        "*/tests/*",
        "test_*.py",
        "*/test_*.py",
        "*_test.py",
        "*/*_test.py",
        "conftest.py",
        "*/conftest.py",
    )
    assert DEFAULT_TEST_GLOBS != ACCESS_TEST_GLOBS


def test_a_conftest_reach_in_is_test_code_only_under_the_eight_pattern_default(corpus_of):
    """``conftest.py`` is in the frozen eight and absent from the other rule's three."""
    corpus = corpus_of({
        "owner.py": OWNER,
        "conftest.py": '''"""A conftest reaching in."""

from owner import _SECRET


def fixture_value() -> int:
    """Touch one."""
    return _SECRET
''',
    })
    assert "protected '_SECRET' accessed from tests ('conftest')" in _messages(
        "under-exposed-access", corpus
    )
    assert "protected '_SECRET' accessed from 'conftest' outside its defining module 'owner'" in (
        _messages("under-exposed-access", corpus, {"test-globs": list(DEFAULT_TEST_GLOBS)})
    )


def test_under_exposed_access_is_not_a_dynamic_access_weakening_consumer():
    """The frozen enumeration is closed at five and names this rule as a non-member."""
    assert "under-exposed-access" not in DYNAMIC_ACCESS_WEAKENED_RULES


# ---------------------------------------------------------------------------
# unused-return-value
# ---------------------------------------------------------------------------

PRODUCER = '''"""Every candidate shape the frozen rule distinguishes."""


def discarded() -> int:
    """Called four times, result thrown away every time."""
    return 1


def used() -> int:
    """Called twice, once for its value."""
    return 2


def unannotated():
    """No return annotation at all."""
    return 3


def returns_none() -> None:
    """Annotated None."""
    return None


def returns_none_quoted() -> "None":
    """Annotated with the string spelling of None."""
    return None


def read_as_a_value() -> int:
    """Bound to a name as well as called."""
    return 4


def used_as_a_decorator() -> int:
    """Decorates something as well as being called."""
    return 5


def never_called() -> int:
    """Zero call sites: dead-code territory, not this rule's."""
    return 6


class Box:
    """Holds a dunder whose result flows through a protocol."""

    def __len__(self) -> int:
        """Dunder: never flagged."""
        return 0
'''

CONSUMER = '''"""Discards results in every way the rule cares about."""

from producer import (
    Box,
    discarded,
    read_as_a_value,
    returns_none,
    returns_none_quoted,
    unannotated,
    used,
    used_as_a_decorator,
)

ALIAS = read_as_a_value


@used_as_a_decorator
def decorated():
    """Decorated by a candidate, which therefore escapes as a value."""


def go() -> int:
    """Call everything, discarding almost everything."""
    discarded()
    discarded()
    discarded()
    discarded()
    unannotated()
    returns_none()
    returns_none_quoted()
    read_as_a_value()
    used_as_a_decorator()
    len(Box())
    used()
    return used()
'''


@pytest.fixture
def returns(corpus_of):
    """A producer of annotated functions and a consumer that mostly discards."""
    return corpus_of({"producer.py": PRODUCER, "consumer.py": CONSUMER})


def test_the_message_quotes_the_count_and_the_first_three_sites_with_a_more_tail(returns):
    """Four discarding calls: three ``file:line`` pairs and a ``+1 more``."""
    found = [m for m in _messages("unused-return-value", returns) if "'producer:discarded'" in m]
    assert len(found) == 1
    message = found[0]
    assert message.startswith(
        "function 'producer:discarded' declares return type 'int' but all 4 "
        "call site(s) discard the result (consumer.py:"
    )
    assert message.endswith("+1 more)")
    assert message.count("consumer.py:") == 3


def test_a_finding_reports_at_declared_and_points_at_the_definition(returns):
    found = [f for f in _findings("unused-return-value", returns) if "discarded" in f.message]
    assert [(f.path, f.confidence) for f in found] == [
        ("producer.py", Confidence.DECLARED)
    ]


@pytest.mark.parametrize(
    "name",
    [
        "used",
        "unannotated",
        "returns_none",
        "returns_none_quoted",
        "read_as_a_value",
        "used_as_a_decorator",
        "never_called",
        "__len__",
    ],
)
def test_the_conservative_exclusions_stay_silent(returns, name):
    """One used call site, no annotation, ``-> None`` twice over, both escape
    kinds, zero calls, and a dunder — every frozen exclusion, one per case."""
    assert not [m for m in _messages("unused-return-value", returns) if f"'{name}'" in m]


def test_discarded_is_the_only_finding(returns):
    """The exclusions above are exhaustive over this corpus."""
    assert len(_messages("unused-return-value", returns)) == 1


def test_allow_exempts_by_symbol_id(returns):
    assert _messages("unused-return-value", returns, {"allow": ["producer:discarded"]}) == []


def test_allow_exempts_by_module_path(returns):
    assert _messages("unused-return-value", returns, {"allow": ["producer"]}) == []
