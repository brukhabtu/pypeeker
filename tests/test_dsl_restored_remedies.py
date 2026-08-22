"""The restored remedies, graded against the frozen engine's own ids.

``dsl-rewrite.md``'s phase-3d ledger entry records ``star-imports``' remedy as
deliberately absent pending the mutation terminals, and
:class:`pypeeker.dsl.Finding` documented the same gap for every rule that
attaches one. There are **five** such rules, not the three the phase-4 brief
named: ``check/rules.py`` attaches a ``TuplifyIntent`` to every
``prefer-tuple`` finding and a ``DeleteSymbolIntent`` to a non-public
``unused-public-symbol`` finding, alongside the three in ``check/builtin/``.
The differential oracle found the first of those two; the second is invisible
to it (see below). This module closes that loop the only way worth closing it:
by running the **frozen rule** and the **ported rule** over one corpus and
asserting the repair ids agree character for character.

That is a stronger check than pinning literals, because fork #5 derives the id
from three moving parts — the origin, the mutation's name and the row's anchor
— and a literal would still pass if all three moved together in the wrong
direction. Reading the frozen engine's output (not its source) is also the
standing reading discipline for a frozen path.

**Shapes, not fields.** The frozen engine carries its repair *inside* a
violation; the port stands it *beside* the finding as a
:class:`pypeeker.dsl.Remediation`, which is what the phase-3d entry asked for
when it predicted the restoration would add a terminal and not a field. So the
comparison here is between two maps keyed on the rendered violation line — the
one string both engines spell identically — rather than between two object
graphs that were never going to have the same shape.
"""

import pytest

from pypeeker.check import CheckEngine
from pypeeker.check.config import CheckConfig
from pypeeker.dsl import Corpus, dsl_rule
from pypeeker.models import Confidence

STARS = '''"""App."""

from target import *

print(VALUE)
'''

TARGET = '''"""Target."""

VALUE = 1
OTHER = 2
'''

DRIFT = '''"""App."""


def renamed(new_name):
    """Do it.

    Args:
        old_name: the stale documented name.
    """
    return new_name


def two_ghosts(real):
    """Do it.

    Args:
        gone_a: one.
        gone_b: two.
    """
    return real
'''

UNUSED = '''"""App."""

import json
'''

TUPLES = '''"""App."""


def go():
    """Bind a list that is never mutated and never escapes."""
    a = [1, 2]
    return a[0]
'''

DEAD_PRIVATE = '''"""App."""


def _dead():
    """Never referenced, and private — the only shape that earns a delete."""
    return 1
'''


def frozen_remedy_ids(store, rule, options=None):
    """``{violation str: fix id}`` as the frozen engine reports them."""
    config = CheckConfig(
        src=(), rules=(rule,), rule_options={rule: dict(options or {})}
    )
    engine = CheckEngine(store, config)
    return {
        str(v): (None if v.remedy is None else v.remedy.intent_id)
        for v in engine.run()
    }


def ported_remedy_ids(corpus, rule, options=None):
    """``{finding str: fix id}`` as the ported rule reports them.

    Asks the rule both of its questions and rejoins the answers on the finding
    itself, which is exactly how a caller wanting the frozen engine's combined
    shape rebuilds it: a row absent from ``remediations`` earned no repair.
    """
    ported = dsl_rule(rule)
    findings = ported.findings(dict(options or {}), corpus)
    repaired = {
        repair.finding: repair.fix_id
        for repair in ported.remediations(dict(options or {}), corpus)
    }
    return {str(finding): repaired.get(finding) for finding in findings}


def ported_repairs(corpus, rule, options=None):
    """The ported rule's :class:`~pypeeker.dsl.Remediation` list."""
    return dsl_rule(rule).remediations(dict(options or {}), corpus)


@pytest.mark.parametrize(
    ("files", "rule", "options"),
    [
        pytest.param(
            {"app.py": UNUSED},
            "unused-imports",
            {},
            id="unused-imports",
        ),
        pytest.param(
            {"app.py": DRIFT},
            "docstring-drift",
            {"require-complete": True},
            id="docstring-drift",
        ),
        pytest.param(
            {
                        "target.py": TARGET,
                "app.py": STARS,
            },
            "star-imports",
            {},
            id="star-imports",
        ),
        pytest.param(
            {"app.py": TUPLES},
            "prefer-tuple",
            {},
            id="prefer-tuple",
        ),
    ],
)
def test_the_ported_rule_derives_the_frozen_engines_fix_ids_exactly(
    indexed_project, files, rule, options
):
    # Fork #5 lands in phase 4 rather than at the flip precisely because this
    # assertion is available now: with the mutation names `remove`, `rewrite`,
    # `rename-param` and `tuplify`, the derived id IS the frozen id. If it ever
    # stops being, that is a real user-visible contract break and this fails.
    _, store = indexed_project(files)
    frozen = frozen_remedy_ids(store, rule, options)
    # Non-vacuity first: a comparison of two empty maps, or of two maps whose
    # every value is None, would pass while proving nothing about the ids.
    assert any(fix_id is not None for fix_id in frozen.values())
    assert ported_remedy_ids(Corpus(store, ()), rule, options) == frozen


def test_the_delete_remedy_is_the_one_id_fork_five_changes(indexed_project):
    # The fifth restored remedy, and the only one whose derived id differs from
    # the frozen literal: `check/rules.py` spells it `unused-symbol:delete:...`,
    # naming a rule that does not exist, where `<rule>:<mutation>:<anchor>`
    # gives `unused-public-symbol:delete:...`. Ledgered in dsl-rewrite.md.
    #
    # `also-private` is what makes the frozen rule reach a non-public symbol at
    # all, and therefore the only way this remedy is observable. No differential
    # corpus sets it, so this test is the ONLY place either engine's delete
    # repair is exercised — which is also why the id change is ungraded by the
    # oracle and has to be pinned here.
    options = {"also-private": True}
    _, store = indexed_project({"app.py": DEAD_PRIVATE})
    frozen = frozen_remedy_ids(store, "unused-public-symbol", options)
    ported = ported_remedy_ids(Corpus(store, ()), "unused-public-symbol", options)

    # Same findings, and each carries a repair on both sides: the divergence is
    # the id alone, not which rows earn one.
    assert set(frozen) == set(ported)
    assert all(fix_id is not None for fix_id in frozen.values())
    assert list(frozen.values()) == ["unused-symbol:delete:app:_dead"]
    assert list(ported.values()) == ["unused-public-symbol:delete:app:_dead"]


def test_a_public_dead_symbol_earns_no_delete_repair(indexed_project):
    # The frozen guard is `visibility is not PUBLIC` — dead private code is
    # deletable, dead public API is somebody else's contract. Ported as
    # DELETE_SYMBOL's `public-api` precondition, so the finding still fires and
    # only the repair is withheld.
    public_dead = '"""App."""\n\n\ndef dead():\n    """Never referenced."""\n    return 1\n'
    _, store = indexed_project({"app.py": public_dead})
    corpus = Corpus(store, ())
    findings = dsl_rule("unused-public-symbol").findings({}, corpus)
    assert [f.message for f in findings] == [
        "public function 'app:dead' has no references in the project"
    ]
    assert ported_repairs(corpus, "unused-public-symbol") == []


def test_the_star_remedy_lands_on_the_one_finding_that_earns_it(indexed_project):
    _, store = indexed_project({
        "target.py": TARGET,
        "app.py": STARS,
    })
    (repair,) = ported_repairs(Corpus(store, ()), "star-imports")
    assert repair.fix_id == "star-imports:rewrite:app:*"
    # The repair names the row it came from, so a remedy landing on the wrong
    # finding is visible without re-deriving which row that was.
    assert repair.finding.message == (
        "star import from 'target' — 1 name actually used: VALUE"
    )
    assert repair.intent.module == "target"


def test_a_star_import_of_an_unindexed_module_earns_no_remedy(indexed_project):
    _, store = indexed_project({
        "app.py": '"""App."""\n\nfrom elsewhere import *\n',
    })
    corpus = Corpus(store, ())
    findings = dsl_rule("star-imports").findings({}, corpus)
    assert [f.confidence for f in findings] == [Confidence.HEURISTIC]
    assert ported_repairs(corpus, "star-imports") == []


def test_two_stars_in_one_file_earn_no_remedy_because_the_tier_drops(indexed_project):
    # The frozen guard is `names and file_confidence is DECLARED`, and the
    # DECLARED half is reached here purely through the mutation's floor: the
    # row source already carries HEURISTIC for a multi-star file.
    _, store = indexed_project({
        "target.py": TARGET,
        "other.py": '"""Other."""\n\nTHING = 3\n',
        "app.py": (
            '"""App."""\n\nfrom target import *\nfrom other import *\n'
            "\nprint(VALUE, THING)\n"
        ),
    })
    corpus = Corpus(store, ())
    findings = dsl_rule("star-imports").findings({}, corpus)
    assert len(findings) == 2
    assert {f.confidence for f in findings} == {Confidence.HEURISTIC}
    assert ported_repairs(corpus, "star-imports") == []


def test_only_the_unambiguous_ghost_earns_a_rename(indexed_project):
    _, store = indexed_project({
        "app.py": DRIFT,
    })
    corpus = Corpus(store, ())
    options = {"require-complete": True}
    findings = dsl_rule("docstring-drift").findings(options, corpus)
    repairs = ported_repairs(corpus, "docstring-drift", options)
    assert [r.fix_id for r in repairs] == [
        "docstring-drift:rename-param:app:renamed:old_name"
    ]
    # two_ghosts has two ghosts and one missing parameter, so no rename is
    # unambiguous; the `require-complete` missing findings never earn one.
    assert len(findings) > 1
    assert len(repairs) == 1


def test_the_rename_intent_carries_the_detected_style_not_the_configured_one(
    indexed_project,
):
    _, store = indexed_project({
        "app.py": DRIFT,
    })
    repairs = ported_repairs(Corpus(store, ()), "docstring-drift")
    assert [r.intent.style for r in repairs] == ["google"]
    assert [r.intent.new_param for r in repairs] == ["new_name"]


def test_a_dynamic_access_file_downgrades_the_import_finding_below_the_floor(
    indexed_project,
):
    # The frozen engine attaches the remedy anyway and `auto_fixable` discards
    # it; the port decides once, at the floor. Ledgered as an effect-only
    # divergence — no consumer ever observed the discarded remedy.
    _, store = indexed_project({
        "app.py": (
            '"""App."""\n\nimport json\n\n\ndef fetch(o, n):\n'
            '    """Fetch."""\n    return getattr(o, n)\n'
        ),
    })
    corpus = Corpus(store, ())
    findings = dsl_rule("unused-imports").findings({}, corpus)
    assert [f.confidence for f in findings] == [Confidence.HEURISTIC]
    assert ported_repairs(corpus, "unused-imports") == []


def test_a_declared_unused_import_always_earns_its_removal(indexed_project):
    _, store = indexed_project({
        "app.py": UNUSED,
    })
    (repair,) = ported_repairs(Corpus(store, ()), "unused-imports")
    assert repair.fix_id == "unused-imports:remove:app:json"
    assert repair.intent.name == "json"
    assert repair.finding.message == "import 'json' is unused in this module"


def test_a_rule_declaring_no_repair_produces_findings_and_no_remediations(
    indexed_project,
):
    # `require-docstrings` is one of the seventeen rules that declare no
    # mutation. It still fires — so the empty repair list is the rule's own
    # declaration and not an accident of an empty corpus, which is the pairing
    # fork #12 asks for.
    _, store = indexed_project({"app.py": "def undocumented():\n    return 1\n"})
    corpus = Corpus(store, ())
    assert dsl_rule("require-docstrings").findings({}, corpus)
    assert ported_repairs(corpus, "require-docstrings") == []
