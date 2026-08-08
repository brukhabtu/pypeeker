"""Rules as expressions: what a ported rule selects, how it words a row, what it reports.

The differential oracle (``scripts/differential-check.py``) proves parity with
the frozen old engine on this repository. These tests prove the other half:
that the expression *fires* on a hand-built index, on the exact wording and the
exact confidence tier the ledger pins. A rule claimed in the parity manifest
with no test here that actually produces a finding is unproven — a 0-vs-0
comparison only shows the new engine does not over-fire.
"""

import pytest

from pypeeker.dsl import (
    RULES,
    Corpus,
    DslRule,
    Finding,
    Reach,
    UnknownExpressionError,
    dsl_rule,
)
from pypeeker.models import Confidence

# `a` is only iterated, which the binder marks as a non-escaping read, so it is
# tuple-equivalent and flagged. `b` is returned, which escapes, so it is not.
TUPLES = """\
def go():
    a = [1, 2]
    for v in a:
        print(v)
    b = [3, 4]
    return b
"""


@pytest.fixture
def tuple_corpus(indexed_project):
    """A one-file corpus under ``src/`` holding one flaggable list and one that escapes."""
    _, store = indexed_project({"src/app.py": TUPLES})
    return Corpus(store, ("src",))


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------


def test_the_ported_rules_are_exactly_the_ones_the_manifest_may_claim():
    assert tuple(sorted(RULES)) == (
        "born-private",
        "no-unresolved-refs",
        "over-exposed-export",
        "over-exposed-module-symbol",
        "prefer-tuple",
        "require-docstrings",
        "test-only-production-code",
        "unused-public-symbol",
    )


def test_a_ported_rule_is_a_selection_plus_a_template():
    rule = dsl_rule("prefer-tuple")
    assert isinstance(rule, DslRule)
    assert rule.rule_id == "prefer-tuple"
    assert rule.message == "list '{name}' is never mutated — consider a tuple"


def test_prefer_tuple_never_leaves_the_file():
    selection = dsl_rule("prefer-tuple").build({})
    assert selection.stages == ("where",)
    assert selection.reach is Reach.FILE


def test_an_unported_rule_is_refused_by_name_and_the_message_lists_the_ported_ones():
    with pytest.raises(UnknownExpressionError) as excinfo:
        dsl_rule("no-argument-mutation")
    assert excinfo.value.expression == "no-argument-mutation"
    assert "'no-argument-mutation'" in str(excinfo.value)
    assert "prefer-tuple" in str(excinfo.value)


# ---------------------------------------------------------------------------
# prefer-tuple: what it fires on, in what words, at what tier
# ---------------------------------------------------------------------------


def test_prefer_tuple_flags_the_unmutated_list_and_not_the_escaping_one(tuple_corpus):
    findings = dsl_rule("prefer-tuple").findings({}, tuple_corpus)
    assert findings == [
        Finding(
            rule="prefer-tuple",
            path="src/app.py",
            line=2,
            message="list 'a' is never mutated — consider a tuple",
            confidence=Confidence.DECLARED,
        )
    ]


def test_prefer_tuple_reports_declared_via_the_meta_read_law(tuple_corpus):
    # dsl-rewrite.md's ledger: "prefer-tuple reports at DECLARED via the
    # meta-read law (fork #4); a port that meets to INFERRED is wrong, not
    # divergent." The `.at(INFERRED)` clause in TUPLE_CANDIDATE is what buys
    # this; spelling it `.value.eq("list")` would meet the whole conjunction
    # down to INFERRED and silently kill the autofix at phase 4.
    (finding,) = dsl_rule("prefer-tuple").findings({}, tuple_corpus)
    assert finding.confidence is Confidence.DECLARED


def test_prefer_tuple_ignores_its_options(tuple_corpus):
    rule = dsl_rule("prefer-tuple")
    assert rule.findings({"nonsense": True}, tuple_corpus) == rule.findings({}, tuple_corpus)


def test_a_module_level_list_is_out_of_scope(indexed_project):
    _, store = indexed_project({"src/app.py": "top = [1, 2]\n"})
    assert dsl_rule("prefer-tuple").findings({}, Corpus(store, ("src",))) == []


def test_a_mutated_list_is_not_flagged(indexed_project):
    source = "def go():\n    a = [1, 2]\n    a.append(3)\n    for v in a:\n        print(v)\n"
    _, store = indexed_project({"src/app.py": source})
    assert dsl_rule("prefer-tuple").findings({}, Corpus(store, ("src",))) == []


# ---------------------------------------------------------------------------
# require-docstrings: kinds, visibility, and the option-injection trap
# ---------------------------------------------------------------------------

DOCSTRINGS = '''\
def documented():
    """It says something."""


def bare():
    pass


def _private():
    pass


class Thing:
    pass
'''


@pytest.fixture
def docstring_corpus(indexed_project):
    """One file holding a documented function, a bare one, a private one, and a bare class."""
    _, store = indexed_project({"src/app.py": DOCSTRINGS})
    return Corpus(store, ("src",))


def test_require_docstrings_flags_public_undocumented_symbols_in_the_old_wording(
    docstring_corpus,
):
    findings = dsl_rule("require-docstrings").findings({}, docstring_corpus)
    assert findings == [
        Finding(
            rule="require-docstrings",
            path="src/app.py",
            line=5,
            message="public function 'bare' has no docstring",
            confidence=Confidence.DECLARED,
        ),
        Finding(
            rule="require-docstrings",
            path="src/app.py",
            line=13,
            message="public class 'Thing' has no docstring",
            confidence=Confidence.DECLARED,
        ),
    ]


def test_require_docstrings_reports_declared_because_every_clause_reads_a_model_field(
    docstring_corpus,
):
    findings = dsl_rule("require-docstrings").findings({}, docstring_corpus)
    assert [f.confidence for f in findings] == [Confidence.DECLARED] * 2


def test_configured_visibility_changes_which_symbols_fire(docstring_corpus):
    # A single leading underscore is PROTECTED in pypeeker's model, not
    # PRIVATE — `private` is reserved for name-mangled `__x`. The point of the
    # test is that the option swaps the population entirely: the public
    # findings above vanish, `_private` appears.
    findings = dsl_rule("require-docstrings").findings(
        {"visibility": ["protected"]}, docstring_corpus
    )
    assert [f.message for f in findings] == ["protected function '_private' has no docstring"]


def test_configured_kinds_narrow_the_selection(docstring_corpus):
    findings = dsl_rule("require-docstrings").findings({"kinds": ["class"]}, docstring_corpus)
    assert [f.message for f in findings] == ["public class 'Thing' has no docstring"]


def test_the_injected_visibility_table_silently_empties_the_visibility_set(docstring_corpus):
    # check.config copies the whole project-wide [tool.pypeeker.visibility]
    # table into EVERY enabled rule's options under the key `visibility`, and
    # require-docstrings reads its own `visibility` option through a coercion
    # that drops unparseable values. The dict's keys are not Visibility members,
    # so the set comes out empty and the old engine reports NOTHING on any
    # project declaring that section. Measured on pypeeker itself: 1 finding
    # under a minimal config, 0 under the harness's generated one. Making this
    # "sensible" — falling back to the default on an unparseable option —
    # re-fires that finding and fails the differential oracle with extra=1.
    injected = {"visibility": {"allow-decorators": ["typing.overload"], "treat-as-public": []}}
    assert dsl_rule("require-docstrings").findings(injected, docstring_corpus) == []


def test_an_unparseable_entry_is_dropped_without_taking_its_neighbours_with_it(
    docstring_corpus,
):
    findings = dsl_rule("require-docstrings").findings(
        {"visibility": ["nonsense", "public"]}, docstring_corpus
    )
    assert [f.message for f in findings] == [
        "public function 'bare' has no docstring",
        "public class 'Thing' has no docstring",
    ]


def test_a_bare_string_option_is_wrapped_rather_than_iterated_per_character(
    docstring_corpus,
):
    findings = dsl_rule("require-docstrings").findings({"kinds": "class"}, docstring_corpus)
    assert [f.message for f in findings] == ["public class 'Thing' has no docstring"]


def test_require_docstrings_never_leaves_the_file():
    selection = dsl_rule("require-docstrings").build({})
    assert selection.stages == ("where",)
    assert selection.reach is Reach.FILE


# ---------------------------------------------------------------------------
# no-unresolved-refs: what the binder could not bind, minus the chain noise
# ---------------------------------------------------------------------------

UNRESOLVED = """\
import os


def go(thing):
    nowhere_at_all()
    print(thing.deep.chain)
    return os.sep
"""


@pytest.fixture
def unresolved_corpus(indexed_project):
    """One file mixing a genuinely unbound name, an attribute chain, and a builtin."""
    _, store = indexed_project({"src/app.py": UNRESOLVED})
    return Corpus(store, ("src",))


def test_no_unresolved_refs_flags_the_unbound_name_in_the_old_wording(unresolved_corpus):
    findings = dsl_rule("no-unresolved-refs").findings({}, unresolved_corpus)
    assert findings == [
        Finding(
            rule="no-unresolved-refs",
            path="src/app.py",
            line=5,
            message="unresolved reference: 'nowhere_at_all'",
            confidence=Confidence.DECLARED,
        )
    ]


def test_no_unresolved_refs_skips_attribute_chains_on_an_unresolved_receiver(
    unresolved_corpus,
):
    # `thing.deep.chain` binds to `<unresolved>.deep`-shaped ids: real, but
    # noise about a problem the receiver's own finding already names. The old
    # rule skips them through models.is_unresolved_attr; the port spells the
    # same test as not_(row.symbol_id.startswith(UNRESOLVED_PREFIX)).
    messages = [f.message for f in dsl_rule("no-unresolved-refs").findings({}, unresolved_corpus)]
    assert not any("<unresolved>" in message for message in messages)


def test_no_unresolved_refs_leaves_builtins_alone(indexed_project):
    # Builtins land on `<builtins>.*` with resolved=True, so the first clause
    # excludes them without the rule mentioning them.
    _, store = indexed_project({"src/app.py": "def go():\n    return len([1])\n"})
    assert dsl_rule("no-unresolved-refs").findings({}, Corpus(store, ("src",))) == []


def test_no_unresolved_refs_ignores_its_options(unresolved_corpus):
    rule = dsl_rule("no-unresolved-refs")
    assert rule.findings({"nonsense": True}, unresolved_corpus) == rule.findings(
        {}, unresolved_corpus
    )


def test_no_unresolved_refs_never_leaves_the_file():
    selection = dsl_rule("no-unresolved-refs").build({})
    assert selection.stages == ("where",)
    assert selection.reach is Reach.FILE
