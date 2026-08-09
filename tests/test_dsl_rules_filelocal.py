"""The file-local rule family ported in TASK-158: naming-conventions, unused-imports, docstring-drift.

Companion to ``tests/test_dsl_rules.py``, which holds the registry and the
rules ported before this family; the split follows the same convention as
``test_dsl_rules_cycles.py`` / ``_boundaries.py`` / ``_purity.py``, one file per
family so no pre-existing test file has to be reopened to add a family.

The differential oracle (``scripts/differential-check.py``) proves parity with
the frozen old engine on the ``filelocal`` corpus. These tests prove the other
half: that each expression *fires* on a hand-built index, with the exact
wording, the exact anchor and the exact confidence tier the frozen rule
produces — including the one shape only this family has, a single symbol row
fanning out into N findings.
"""

import pytest

from pypeeker.dsl import Corpus, Finding, Reach, dsl_rule
from pypeeker.models import Confidence


# ---------------------------------------------------------------------------
# naming-conventions: two message shapes, four labels, and the option traps
# ---------------------------------------------------------------------------

NAMING = '''\
def getValue():
    """camelCase: a suggestion exists."""


def _9lives():
    """Stripped to "9lives", which the converter returns unchanged."""


def legacyAllowed():
    """Candidate for the allow list."""


def ____():
    """Underscore-only after stripping."""


class bad_class:
    """snake_case class: PascalCase label, with a suggestion."""

    def doThing(self):
        """camelCase method."""

    def __repr__(self):
        """A dunder: protocol surface."""
        return "bad_class()"


class _9Lives:
    """Stripped to "9Lives", which the PascalCase converter returns unchanged."""


badVar = 6
GOOD_CONSTANT = 7
'''


@pytest.fixture
def naming_corpus(indexed_project):
    """One file carrying every naming shape the message and the labels can take."""
    _, store = indexed_project({"src/app.py": NAMING})
    return Corpus(store, ("src",))


def _messages(rule_id, options, corpus):
    """The findings' messages, sorted, so a MultiPartRule's part order is not asserted."""
    return sorted(f.message for f in dsl_rule(rule_id).findings(options, corpus))


def test_naming_conventions_words_both_message_shapes_verbatim(naming_corpus):
    # The frozen rule appends " — suggested name: '...'" (U+2014, spaces both
    # sides) only when the converter produced a different name. Both shapes are
    # literal templates on two RuleParts; a single template with an
    # interpolated suffix would put half a message outside the DSL.
    assert _messages("naming-conventions", {}, naming_corpus) == [
        "class 'src.app:_9Lives' does not match the PascalCase naming convention",
        "class 'src.app:bad_class' does not match the PascalCase naming convention"
        " — suggested name: 'BadClass'",
        "function 'src.app:_9lives' does not match the snake_case naming convention",
        "function 'src.app:getValue' does not match the snake_case naming convention"
        " — suggested name: 'get_value'",
        "function 'src.app:legacyAllowed' does not match the snake_case naming convention"
        " — suggested name: 'legacy_allowed'",
        "method 'src.app:bad_class.doThing' does not match the snake_case naming convention"
        " — suggested name: 'do_thing'",
    ]


def test_naming_conventions_reports_declared(naming_corpus):
    findings = dsl_rule("naming-conventions").findings({}, naming_corpus)
    assert findings and all(f.confidence is Confidence.DECLARED for f in findings)


def test_naming_conventions_points_at_the_definition_line(naming_corpus):
    (getvalue,) = [
        f for f in dsl_rule("naming-conventions").findings({}, naming_corpus)
        if "getValue" in f.message
    ]
    assert (getvalue.path, getvalue.line) == ("src/app.py", 1)


def test_an_underscore_only_name_is_not_flagged(naming_corpus):
    # `____` is where the port's `not_(name.matches("__*__"))` and the frozen
    # `_is_dunder` (which additionally requires len > 4) disagree about WHY the
    # symbol is skipped. They agree that it IS skipped only because the
    # `stripped` clause rejects it; reorder those two clauses and this fires.
    assert not any(
        "'src.app:____'" in message
        for message in _messages("naming-conventions", {}, naming_corpus)
    )


def test_a_dunder_method_is_not_flagged(naming_corpus):
    assert not any(
        "__repr__" in message
        for message in _messages("naming-conventions", {}, naming_corpus)
    )


def test_a_conforming_name_is_not_flagged(naming_corpus):
    assert not any(
        "GOOD_CONSTANT" in message
        for message in _messages(
            "naming-conventions", {"kinds": ["variable"]}, naming_corpus
        )
    )


def test_the_variable_kind_is_opt_in_and_carries_the_upper_snake_label(naming_corpus):
    # `variable` is off by default (locals and parameters mirror external APIs
    # often enough to be noisy) and its label is the third of the four shapes:
    # UPPER_SNAKE is tolerated because constants are not statically
    # distinguishable from variables.
    assert not any(
        "badVar" in message
        for message in _messages("naming-conventions", {}, naming_corpus)
    )
    assert _messages("naming-conventions", {"kinds": ["variable"]}, naming_corpus) == [
        "variable 'src.app:badVar' does not match the snake_case (or UPPER_SNAKE_CASE)"
        " naming convention — suggested name: 'bad_var'"
    ]


def test_a_conventions_override_relabels_the_kind_and_keeps_its_suggester(naming_corpus):
    # The fourth label shape. The override redefines what CONFORMS, never what
    # shape to convert to, so `doThing` is still suggested as `do_thing` — the
    # default snake_case suggester — under a `pattern '...'` label.
    findings = _messages(
        "naming-conventions",
        {"kinds": ["method"], "conventions": {"method": "^[a-z][a-z0-9_]*$"}},
        naming_corpus,
    )
    assert findings == [
        "method 'src.app:bad_class.doThing' does not match the pattern '^[a-z][a-z0-9_]*$'"
        " naming convention — suggested name: 'do_thing'"
    ]


def test_an_uncompilable_override_is_ignored_and_the_default_label_survives(naming_corpus):
    assert _messages(
        "naming-conventions", {"kinds": ["method"], "conventions": {"method": "["}}, naming_corpus
    ) == [
        "method 'src.app:bad_class.doThing' does not match the snake_case naming convention"
        " — suggested name: 'do_thing'"
    ]


def test_a_non_mapping_conventions_option_is_ignored_wholesale(naming_corpus):
    assert _messages(
        "naming-conventions", {"kinds": ["method"], "conventions": ["nonsense"]}, naming_corpus
    ) == [
        "method 'src.app:bad_class.doThing' does not match the snake_case naming convention"
        " — suggested name: 'do_thing'"
    ]


def test_an_unparseable_kinds_option_selects_nothing_rather_than_the_default(naming_corpus):
    # The frozen `_selected_kinds` is asymmetric on purpose: `_as_str_list(raw)
    # or list(_DEFAULT_KINDS)` falls back to the default three only when the
    # option is ABSENT or EMPTY. A non-empty option whose every entry fails
    # SymbolKind(...) yields the EMPTY set, so the rule checks nothing. A port
    # that "sensibly" fell back to the default here over-fires on every
    # misconfigured project.
    assert dsl_rule("naming-conventions").findings({"kinds": ["bogus"]}, naming_corpus) == []
    assert _messages("naming-conventions", {"kinds": []}, naming_corpus) == _messages(
        "naming-conventions", {}, naming_corpus
    )


def test_a_kind_outside_the_convention_table_is_dropped_without_emptying_the_set(
    naming_corpus,
):
    assert _messages(
        "naming-conventions", {"kinds": ["module", "class"]}, naming_corpus
    ) == [
        "class 'src.app:_9Lives' does not match the PascalCase naming convention",
        "class 'src.app:bad_class' does not match the PascalCase naming convention"
        " — suggested name: 'BadClass'",
    ]


def test_allow_matches_the_bare_name_the_symbol_id_or_the_module(naming_corpus):
    by_name = _messages("naming-conventions", {"allow": ["legacyAllowed"]}, naming_corpus)
    by_id = _messages("naming-conventions", {"allow": ["src.app:legacyAllowed"]}, naming_corpus)
    by_module = _messages("naming-conventions", {"allow": ["src.app"]}, naming_corpus)
    assert not any("legacyAllowed" in message for message in by_name)
    assert by_id == by_name
    assert by_module == []


def test_naming_conventions_never_leaves_the_file():
    for part in dsl_rule("naming-conventions").parts:
        assert part.build({}).reach is Reach.FILE


# ---------------------------------------------------------------------------
# unused-imports: one finding, nine exclusions, one confidence downgrade
# ---------------------------------------------------------------------------

IMPORTS = '''\
"""A module whose only unused binding is `json`."""

from __future__ import annotations

import importlib
import json
import os as _os
import xml.etree.ElementTree
from decimal import Decimal


def load(path):
    """Uses importlib."""
    return importlib.import_module(path)


def annotate(value: "Decimal") -> None:
    """`Decimal` is visible only inside a quoted annotation."""
    del value
'''


@pytest.fixture
def imports_corpus(indexed_project):
    """One file holding one unused import and five of the rule's exclusions."""
    _, store = indexed_project({"src/app.py": IMPORTS})
    return Corpus(store, ("src",))


def test_unused_imports_flags_only_the_unused_binding_in_the_old_wording(imports_corpus):
    assert dsl_rule("unused-imports").findings({}, imports_corpus) == [
        Finding(
            rule="unused-imports",
            path="src/app.py",
            line=6,
            message="import 'json' is unused in this module",
            confidence=Confidence.DECLARED,
        )
    ]


def test_unused_imports_ignores_its_options(imports_corpus):
    rule = dsl_rule("unused-imports")
    assert rule.findings({"nonsense": True}, imports_corpus) == rule.findings({}, imports_corpus)


def test_a_package_barrel_is_skipped_entirely(indexed_project):
    _, store = indexed_project({"src/pkg/__init__.py": "import json\n"})
    assert dsl_rule("unused-imports").findings({}, Corpus(store, ("src",))) == []


def test_a_file_binding_dunder_all_is_skipped_entirely(indexed_project):
    _, store = indexed_project({"src/app.py": 'import json\n\n__all__ = ["json"]\n'})
    assert dsl_rule("unused-imports").findings({}, Corpus(store, ("src",))) == []


def test_a_star_import_is_left_to_the_star_imports_rule(indexed_project):
    _, store = indexed_project(
        {"src/other.py": "def helper():\n    pass\n", "src/app.py": "from other import *\n"}
    )
    assert dsl_rule("unused-imports").findings({}, Corpus(store, ("src",))) == []


def test_a_dynamically_recovered_import_is_not_reported(indexed_project):
    # importlib.import_module binds no name, so "unused" is meaningless and
    # there is no import statement a fix could remove.
    source = 'import importlib\n\nmod = importlib.import_module("json")\n'
    _, store = indexed_project({"src/app.py": source})
    messages = [f.message for f in dsl_rule("unused-imports").findings({}, Corpus(store, ("src",)))]
    assert "json" not in " ".join(messages)


def test_a_used_import_is_not_flagged(indexed_project):
    _, store = indexed_project({"src/app.py": "import json\n\nvalue = json.dumps({})\n"})
    assert dsl_rule("unused-imports").findings({}, Corpus(store, ("src",))) == []


def test_dynamic_attribute_access_downgrades_the_whole_file_to_heuristic(indexed_project):
    source = 'import json\n\n\ndef fetch(obj, name):\n    return getattr(obj, name)\n'
    _, store = indexed_project({"src/app.py": source})
    assert dsl_rule("unused-imports").findings({}, Corpus(store, ("src",))) == [
        Finding(
            rule="unused-imports",
            path="src/app.py",
            line=1,
            message="import 'json' is unused in this module",
            confidence=Confidence.HEURISTIC,
        )
    ]


def test_the_downgrade_does_not_leak_into_a_file_without_dynamic_access(indexed_project):
    _, store = indexed_project({
        "src/dyn.py": "import json\n\n\ndef fetch(o, n):\n    return getattr(o, n)\n",
        "src/plain.py": "import json\n",
    })
    tiers = {
        f.path: f.confidence for f in dsl_rule("unused-imports").findings({}, Corpus(store, ("src",)))
    }
    assert tiers == {"src/dyn.py": Confidence.HEURISTIC, "src/plain.py": Confidence.DECLARED}


def test_unused_imports_leaves_the_file_only_because_the_corpus_is_the_domain():
    # Every clause reads a field of the row source, and the row source is
    # per-file; the PROJECT reach comes from quantifying over the corpus rather
    # than from any clause looking outside its file.
    assert dsl_rule("unused-imports").build({}).stages == ("where",)


# ---------------------------------------------------------------------------
# docstring-drift: the fan-out — one symbol, N findings, one row each
# ---------------------------------------------------------------------------

DRIFT = '''\
"""Drifted docstrings in all three recognized styles."""


def two_ghosts(kept):
    """Two documented names the signature does not have.

    Args:
        kept: the surviving parameter.
        gone_first: deleted.
        gone_second: also deleted.
    """
    return kept


def undocumented_second(first, second):
    """Only the first parameter is documented.

    Args:
        first: documented.
    """
    return first, second


def numpy_ghost(kept):
    """A numpy section with a ghost.

    Parameters
    ----------
    kept
        the surviving parameter.
    stale
        deleted.
    """
    return kept


def sphinx_ghost(kept):
    """A sphinx field list with a ghost.

    :param kept: the surviving parameter.
    :param dropped: deleted.
    """
    return kept


def no_params_section(anything):
    """Prose only: demanding a section is require-docstrings' turf."""
    return anything


class Holder:
    """Holds the method case."""

    def method_ghost(self, kept):
        """`self` is never expected in a params section.

        Args:
            kept: the surviving parameter.
            vanished: deleted.
        """
        return kept
'''

COMPLETE = {"require-complete": True}


@pytest.fixture
def drift_corpus(indexed_project):
    """One file carrying every drift shape: both directions, three styles, a method."""
    _, store = indexed_project({"src/app.py": DRIFT})
    return Corpus(store, ("src",))


def test_one_symbol_with_two_ghosts_produces_two_findings_at_the_same_line(drift_corpus):
    # THE fan-out case. The frozen rule emits one finding per ghost parameter,
    # all at the function's own line; the port gets there by quantifying over
    # drifted parameters rather than over functions, so `rows` still yields
    # exactly one match per row.
    findings = [
        f for f in dsl_rule("docstring-drift").findings({}, drift_corpus)
        if "two_ghosts" in f.message
    ]
    assert findings == [
        Finding(
            rule="docstring-drift",
            path="src/app.py",
            line=4,
            message=(
                "docstring of function 'two_ghosts' documents parameter "
                "'gone_first' which does not exist"
            ),
            confidence=Confidence.DECLARED,
        ),
        Finding(
            rule="docstring-drift",
            path="src/app.py",
            line=4,
            message=(
                "docstring of function 'two_ghosts' documents parameter "
                "'gone_second' which does not exist"
            ),
            confidence=Confidence.DECLARED,
        ),
    ]


def test_every_ghost_in_every_style_is_worded_in_the_frozen_line(drift_corpus):
    assert _messages("docstring-drift", {}, drift_corpus) == [
        "docstring of function 'numpy_ghost' documents parameter 'stale'"
        " which does not exist",
        "docstring of function 'sphinx_ghost' documents parameter 'dropped'"
        " which does not exist",
        "docstring of function 'two_ghosts' documents parameter 'gone_first'"
        " which does not exist",
        "docstring of function 'two_ghosts' documents parameter 'gone_second'"
        " which does not exist",
        "docstring of method 'method_ghost' documents parameter 'vanished'"
        " which does not exist",
    ]


def test_require_complete_adds_only_the_missing_findings(drift_corpus):
    ghosts = _messages("docstring-drift", {}, drift_corpus)
    complete = _messages("docstring-drift", COMPLETE, drift_corpus)
    assert set(complete) - set(ghosts) == {
        "docstring of function 'undocumented_second' does not document parameter 'second'"
    }
    assert set(ghosts) - set(complete) == set()


def test_require_complete_off_switches_the_second_part_off_rather_than_filtering_it():
    # The frozen rule guards its second loop with an `if`, so the pass does not
    # run at all. A `None` build says that; an always-false predicate would
    # claim the rule looked at every undocumented parameter and forgave each.
    ghosts, missing = dsl_rule("docstring-drift").parts
    assert missing.build({}) is None
    assert missing.build(COMPLETE) is not None
    assert ghosts.build({}) is not None


def test_self_is_never_reported_as_an_undocumented_parameter(drift_corpus):
    assert not any(
        "'self'" in message
        for message in _messages("docstring-drift", COMPLETE, drift_corpus)
    )


def test_a_docstring_with_no_params_section_fires_in_neither_direction(drift_corpus):
    assert not any(
        "no_params_section" in message
        for message in _messages("docstring-drift", COMPLETE, drift_corpus)
    )


def test_allow_exempts_by_symbol_id_or_by_module(drift_corpus):
    by_id = _messages("docstring-drift", {"allow": ["src.app:two_ghosts"]}, drift_corpus)
    by_module = _messages("docstring-drift", {"allow": ["src.app"]}, drift_corpus)
    assert not any("two_ghosts" in message for message in by_id)
    assert len(by_id) == 3
    assert by_module == []


def test_an_unrecognized_style_falls_back_to_autodetection(drift_corpus):
    # The frozen rule's `style_opt if style_opt in DOCSTRING_STYLES else None`:
    # a bad value is not an error and must not silence the rule.
    assert _messages("docstring-drift", {"style": "epytext"}, drift_corpus) == _messages(
        "docstring-drift", {}, drift_corpus
    )


def test_forcing_one_style_parses_only_that_style(drift_corpus):
    assert _messages("docstring-drift", {"style": "sphinx"}, drift_corpus) == [
        "docstring of function 'sphinx_ghost' documents parameter 'dropped'"
        " which does not exist",
    ]


def test_each_fanned_finding_carries_the_parameters_own_symbol_id_as_its_anchor(
    drift_corpus,
):
    # Fork #6 keys the baseline on (rule_id, anchor_id) and fork #5 derives
    # fix_id as <rule>:<mutation>:<anchor>. Anchoring on the FUNCTION would give
    # two ghosts one key and one fix id; the parameter's own id — which is what
    # the symbol-id grammar `module:Scope.Chain:local` already spells — gives
    # each finding its own, and reproduces the frozen
    # RenameDocstringParamIntent id character for character.
    ghosts, missing = dsl_rule("docstring-drift").parts
    anchors = {
        (match.anchor.kind.value, match.anchor.id)
        for match in ghosts.build({}).rows(drift_corpus)
    }
    assert ("symbol", "src.app:two_ghosts:gone_first") in anchors
    assert ("symbol", "src.app:two_ghosts:gone_second") in anchors
    assert ("symbol", "src.app:Holder.method_ghost:vanished") in anchors
    assert len(anchors) == 5
    # A missing row's anchor is a symbol that genuinely exists: the parameter.
    missing_anchors = {match.anchor.id for match in missing.build(COMPLETE).rows(drift_corpus)}
    assert missing_anchors == {"src.app:undocumented_second:second"}


def test_the_missing_anchor_is_the_real_parameters_symbol_id(drift_corpus):
    indexed = {
        symbol.symbol_id
        for index in drift_corpus.indexes
        for symbol in index.symbols
    }
    assert "src.app:undocumented_second:second" in indexed


def test_each_fanned_finding_carries_its_own_derivation_chain(drift_corpus):
    # The claim the fan-out rests on: `--why` answers per emitted finding, not
    # per function. Two ghosts on one symbol are two Matches, each with its own
    # anchor and its own derivation tree, so serializing one never describes
    # the other.
    from pypeeker.dsl import derivation_document

    ghosts, _missing = dsl_rule("docstring-drift").parts
    matches = [
        match for match in ghosts.build({}).rows(drift_corpus)
        if match.anchor.id.startswith("src.app:two_ghosts:")
    ]
    assert len(matches) == 2
    assert len({match.anchor.id for match in matches}) == 2
    for match in matches:
        document = derivation_document(match.derivations)
        assert document["derivations"]
        assert document["derivations"][0]["value"] is True
