"""The cross-file residue (phase 3d): star-imports and barrel-only.

``scripts/differential-check.py`` grades both against the frozen engine on
``tests/fixtures/parity/crossfile`` — 5 and 2 findings — and ``star-imports``
once more on ``filelocal``. That corpus is where the four message shapes, both
confidence tiers and the five barrel exemptions are proven *against the oracle*.
This file is the other half: the shapes no source file can put in front of the
oracle at the right angle, and the derivations a count-based comparison would
not notice going wrong.

For ``star-imports`` those are the two attribution exclusions (an
underscore-prefixed unresolved name, and an ``<unresolved>.attr`` sentinel),
the complementarity of the four partitions, and the absent
``RewriteStarImportIntent`` remedy that ``dsl-rewrite.md``'s ledger records as
a divergence the oracle is structurally blind to.

For ``barrel-only`` they are the dynamic-import exemption — which needs an
``import_confidence`` only ``importlib.import_module`` produces, on a shape
where *every other* test the rule makes is satisfied — and the ``in_barrel``
exemption applied to a package ``__init__`` deep-importing a **different**
package, which is the only arrangement where that clause decides anything on
its own.
"""

import dataclasses
import tomllib
from pathlib import Path

import pytest

from pypeeker.dsl import Corpus, Reach, dsl_rule
from pypeeker.dsl.rules import Finding
from pypeeker.dsl.sweeps import _barrel_sweep
from pypeeker.models import Confidence

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "scripts" / "parity-manifest.toml"

CLAIMED_HERE = ("barrel-only", "star-imports")


@pytest.fixture
def corpus_of(indexed_project):
    """Build a corpus over ``files`` with no source-root filter.

    Paths carry no ``src/`` prefix, for the reason
    ``tests/test_dsl_visibility_rules.py`` gives: the binder derives a module id
    from the indexed path, so ``names.py`` is the module ``names`` and a
    sibling's ``from names import *`` resolves against it.
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


def test_the_crossfile_target_is_wired_into_the_manifest():
    """Both rules are structurally 0-vs-0 on `self`; this target is what grades them."""
    manifest = tomllib.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    targets = {entry["name"]: entry["path"] for entry in manifest["target"]}
    assert targets["crossfile"] == "tests/fixtures/parity/crossfile"


@pytest.mark.parametrize("rule_id", CLAIMED_HERE)
def test_both_rules_reach_project(rule_id):
    rule = dsl_rule(rule_id)
    builds = [rule.build] if hasattr(rule, "build") else [part.build for part in rule.parts]
    for build in builds:
        assert build({}).reach is Reach.PROJECT


# ---------------------------------------------------------------------------
# star-imports
# ---------------------------------------------------------------------------

NAMES = '''"""Supplies public module-level names to a star."""

ALPHA = "alpha"


def beta() -> str:
    """Public, so a star supplies it."""
    return "beta"


def _hidden() -> str:
    """Underscore-prefixed: never supplied by a star."""
    return "hidden"
'''

OTHER = '''"""A second star target."""


def gamma() -> str:
    """Public."""
    return "gamma"
'''


def _star_corpus(corpus_of, **extra: str) -> Corpus:
    return corpus_of({"names.py": NAMES, "other.py": OTHER, **extra})


def test_a_lone_star_supplying_two_names_uses_the_plural_wording(corpus_of):
    """The used names are sorted and joined, and the count is interpolated."""
    corpus = _star_corpus(
        corpus_of,
        **{
            "user.py": '"""Uses two."""\n\nfrom names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return ALPHA + beta()\n'
        },
    )
    assert _messages("star-imports", corpus) == [
        "star import from 'names' — 2 names actually used: ALPHA, beta"
    ]


def test_a_lone_star_supplying_two_names_reports_at_declared(corpus_of):
    """One star in the file, so first-star-wins cannot have guessed wrong."""
    corpus = _star_corpus(
        corpus_of,
        **{
            "user.py": '"""Uses two."""\n\nfrom names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return ALPHA + beta()\n'
        },
    )
    assert [f.confidence for f in _findings("star-imports", corpus)] == [Confidence.DECLARED]


def test_a_lone_star_supplying_one_name_uses_the_singular_wording(corpus_of):
    """``1 name``, not ``1 names`` — the frozen ``plural`` computation."""
    corpus = _star_corpus(
        corpus_of,
        **{
            "one.py": '"""Uses one."""\n\nfrom names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return ALPHA\n'
        },
    )
    assert _messages("star-imports", corpus) == [
        "star import from 'names' — 1 name actually used: ALPHA"
    ]


def test_a_star_supplying_nothing_suggests_deleting_the_import(corpus_of):
    corpus = _star_corpus(
        corpus_of,
        **{
            "quiet.py": '"""Uses nothing."""\n\nfrom names import *\n\n\n'
            'def use() -> int:\n    """Use."""\n    return 1\n'
        },
    )
    assert _messages("star-imports", corpus) == [
        "star import from 'names' — 0 names actually used; consider deleting the import"
    ]


def test_an_unindexed_target_reports_unknown_names_at_heuristic(corpus_of):
    """The one branch where the file's star count does not decide the tier."""
    corpus = _star_corpus(
        corpus_of,
        **{"outside.py": '"""Targets a module outside the corpus."""\n\nfrom nowhere import *\n'},
    )
    findings = _findings("star-imports", corpus)
    assert [f.message for f in findings] == [
        "star import from 'nowhere' — target module is not indexed; used names unknown"
    ]
    assert findings[0].confidence is Confidence.HEURISTIC


def test_two_stars_in_one_file_attribute_first_star_wins_at_heuristic(corpus_of):
    """``ALPHA`` goes to the star that can supply it; ``gamma`` to the one left.

    Python's runtime shadowing is *last*-wins, so the frozen rule marks every
    multi-star finding ``HEURISTIC``. A port attributing in the other direction
    produces the same finding count with the two names swapped, which is the
    failure a count comparison cannot see.
    """
    corpus = _star_corpus(
        corpus_of,
        **{
            "multi.py": '"""Two stars."""\n\nfrom names import *\nfrom other import *\n\n\n'
            'def use() -> str:\n    """Use one from each."""\n    return ALPHA + gamma()\n'
        },
    )
    findings = _findings("star-imports", corpus)
    assert [f.message for f in findings] == [
        "star import from 'names' — 1 name actually used: ALPHA",
        "star import from 'other' — 1 name actually used: gamma",
    ]
    assert {f.confidence for f in findings} == {Confidence.HEURISTIC}


def test_an_underscore_prefixed_unresolved_name_is_not_attributed(corpus_of):
    """A star never supplies ``_hidden``, so using it leaves the star at zero names."""
    corpus = _star_corpus(
        corpus_of,
        **{
            "sneaky.py": '"""Uses only an underscore name."""\n\nfrom names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return _hidden()\n'
        },
    )
    assert _messages("star-imports", corpus) == [
        "star import from 'names' — 0 names actually used; consider deleting the import"
    ]


def test_an_unresolved_attribute_sentinel_is_not_attributed(corpus_of):
    """``ALPHA.upper()`` is one attributed name, not two.

    The receiver is a bare unresolved reference the star supplies; the
    attribute access is recorded as the sentinel ``<unresolved>.upper``, which
    the frozen ``_unresolved_bare_names`` drops. A port that forgot
    ``is_unresolved_attr`` would still find one attributable name here and
    report the same count, so the pin is on the *message*.
    """
    corpus = _star_corpus(
        corpus_of,
        **{
            "attr.py": '"""Uses a starred name as an attribute receiver."""\n\n'
            'from names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return ALPHA.upper()\n'
        },
    )
    assert _messages("star-imports", corpus) == [
        "star import from 'names' — 1 name actually used: ALPHA"
    ]


def test_every_star_is_worded_exactly_once(corpus_of):
    """The four partitions are complementary: one finding per star, never two.

    ``indexed`` splits the rows once and ``name_count`` splits the indexed half
    into 0 / 1 / neither, so a row that fell through every part — or matched
    two — shows up here as a count mismatch against the number of stars.
    """
    corpus = _star_corpus(
        corpus_of,
        **{
            "user.py": '"""Two names."""\n\nfrom names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return ALPHA + beta()\n',
            "one.py": '"""One name."""\n\nfrom other import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return gamma()\n',
            "quiet.py": '"""No names."""\n\nfrom names import *\n',
            "outside.py": '"""Unindexed."""\n\nfrom nowhere import *\n',
            "multi.py": '"""Two stars."""\n\nfrom names import *\nfrom other import *\n',
        },
    )
    assert len(_findings("star-imports", corpus)) == 6


def test_the_remediable_shape_is_reported_without_a_remedy(corpus_of):
    """The ledger's phase-3d divergence, made explicit.

    A lone ``DECLARED`` star with at least one used name is exactly the subset
    the frozen rule decorates with a ``RewriteStarImportIntent``. The port emits
    the finding, at the same tier and in the same words, and a ``Finding``
    carries no remedy at all — the read half has no mutation terminals. The
    oracle compares five fields and cannot see this, which is why it is a
    ledger entry and a test rather than a manifest divergence.
    """
    corpus = _star_corpus(
        corpus_of,
        **{
            "user.py": '"""Uses two."""\n\nfrom names import *\n\n\n'
            'def use() -> str:\n    """Use."""\n    return ALPHA + beta()\n'
        },
    )
    (finding,) = _findings("star-imports", corpus)
    assert finding.confidence is Confidence.DECLARED
    assert {f.name for f in dataclasses.fields(Finding)} == {
        "rule",
        "path",
        "line",
        "message",
        "confidence",
    }


def test_a_star_import_is_anchored_on_its_own_line(corpus_of):
    """One-based, off the star IMPORT symbol's own span."""
    corpus = _star_corpus(
        corpus_of,
        **{"quiet.py": '"""Doc."""\n\nfrom names import *\n'},
    )
    (finding,) = _findings("star-imports", corpus)
    assert (finding.path, finding.line) == ("quiet.py", 3)


# ---------------------------------------------------------------------------
# barrel-only
# ---------------------------------------------------------------------------

BARREL_FILES = {
    "proj/core/__init__.py": '"""Curated barrel: declares __all__."""\n\n'
    "from proj.core.engine import Engine, helper\n\n"
    '__all__ = ["Engine", "helper"]\n',
    "proj/core/engine.py": '"""Internal module the barrel re-exports from."""\n\n\n'
    'class Engine:\n    """E."""\n\n\n'
    'def helper() -> str:\n    """H."""\n    return "h"\n',
    "proj/core/internal.py": '"""Internal module the barrel does NOT re-export."""\n\n\n'
    'def internal_helper() -> str:\n    """I."""\n    return "i"\n',
    "proj/plain/__init__.py": '"""Uncurated: re-exports but declares no __all__."""\n\n'
    "from proj.plain.widget import Widget\n",
    "proj/plain/widget.py": '"""W."""\n\n\nclass Widget:\n    """W."""\n',
    "proj/api/sibling.py": '"""Same-package sibling."""\n\n\n'
    'def sibling_value() -> str:\n    """S."""\n    return "s"\n',
    "proj/api/handler.py": '"""One violation plus four exemptions."""\n\n'
    "from proj.api.sibling import sibling_value\n"
    "from proj.core import helper\n"
    "from proj.core.engine import Engine\n"
    "from proj.core.internal import internal_helper\n"
    "from proj.plain.widget import Widget\n",
}


@pytest.fixture
def barrels(corpus_of) -> Corpus:
    return corpus_of(dict(BARREL_FILES))


def test_a_deep_import_of_a_re_exported_name_is_flagged(barrels):
    assert _messages("barrel-only", barrels) == [
        "import 'Engine' via the 'proj.core' barrel, not its internal module "
        "'proj.core.engine'"
    ]


def test_the_finding_points_at_the_import_statement(barrels):
    (finding,) = _findings("barrel-only", barrels)
    assert (finding.path, finding.line) == ("proj/api/handler.py", 5)
    assert finding.confidence is Confidence.DECLARED


@pytest.mark.parametrize(
    "exempt_name",
    ["sibling_value", "internal_helper", "Widget", "helper"],
)
def test_the_exemptions_stay_silent(barrels, exempt_name):
    """A same-package sibling, a name the barrel does not re-export, a package
    with no ``__all__``, and an import already at the barrel level.

    Keyed on the quoted name each finding would carry, so the assertion fails
    if any of the four starts firing rather than merely if the total moves.
    """
    quoted = f"import '{exempt_name}'"
    assert not [m for m in _messages("barrel-only", barrels) if m.startswith(quoted)]


def test_an_aliased_deep_import_quotes_the_name_the_barrel_exports(corpus_of):
    """``from a.b import C as D`` is worded ``import 'C'``, never ``import 'D'``.

    The frozen message interpolates ``imported_from.rpartition(".")[2]`` — what
    the barrel actually exports — and not ``symbol.name``, which is the
    consumer's local alias. A port publishing the binding's name on its rows
    passes every other case in this file and fails only this one.
    """
    files = dict(BARREL_FILES)
    files["proj/api/handler.py"] = (
        '"""Aliases the violating import."""\n\n'
        "from proj.core.engine import helper as core_helper\n"
    )
    corpus = corpus_of(files)
    assert _messages("barrel-only", corpus) == [
        "import 'helper' via the 'proj.core' barrel, not its internal module "
        "'proj.core.engine'"
    ]


def test_a_barrel_deep_importing_another_package_is_exempt(corpus_of):
    """``in_barrel`` is the only clause standing between this row and a finding.

    A package ``__init__`` reaching into *its own* submodules is already
    covered by the same-package skip. Reaching into a **different** package's
    internals is the arrangement where the frozen rule's ``__init__.py``
    ``continue`` is load-bearing on its own — that is how a barrel that
    re-exports another package's surface is built.
    """
    files = dict(BARREL_FILES)
    files["proj/agg/__init__.py"] = (
        '"""An aggregating barrel built from another package\'s internals."""\n\n'
        "from proj.core.engine import Engine\n\n"
        '__all__ = ["Engine"]\n'
    )
    corpus = corpus_of(files)
    assert not [m for m in _messages("barrel-only", corpus) if "proj/agg" in m]
    assert len(_findings("barrel-only", corpus)) == 1


def test_a_dynamic_import_is_out_of_scope(corpus_of):
    """The row satisfies every other test the rule makes, and stays silent.

    ``importlib.import_module`` is the only thing that sets
    ``import_confidence``, and the binder only ever sets it to ``HEURISTIC``.
    The sweep row below is checked directly, because asserting silence alone
    would also pass for a port that never produced the row at all.
    """
    files = dict(BARREL_FILES)
    files["proj/api/handler.py"] = (
        '"""One static violation and one dynamic near-miss."""\n\n'
        "import importlib\n\n"
        "from proj.core.engine import Engine\n\n"
        'DYNAMIC = importlib.import_module("proj.core.engine.Engine")\n'
    )
    corpus = corpus_of(files)
    dynamic_rows = [
        entry
        for entry in _barrel_sweep(corpus, "proj")
        if entry.dynamic and entry.file_path == "proj/api/handler.py"
    ]
    assert len(dynamic_rows) == 1
    assert dynamic_rows[0].cross_package
    assert dynamic_rows[0].deeper_than_barrel
    assert dynamic_rows[0].curated
    assert len(_findings("barrel-only", corpus, {"root": "proj"})) == 1


def test_the_root_option_scopes_the_rule(corpus_of):
    """A configured ``root`` no file sits under leaves the rule with no jurisdiction.

    The frozen rule's ``_package_under(module_id, root) is None -> continue`` is
    a *jurisdiction* test, so those files produce no rows at all rather than
    rows the selection rejects.
    """
    files = dict(BARREL_FILES)
    corpus = corpus_of(files)
    assert _findings("barrel-only", corpus, {"root": "elsewhere"}) == []
    assert len(_findings("barrel-only", corpus, {"root": "proj"})) == 1


def test_the_root_option_defaults_to_each_files_top_level_segment(barrels):
    """No ``root`` configured is the same answer here, since everything is under ``proj``."""
    assert _messages("barrel-only", barrels) == _messages(
        "barrel-only", barrels, {"root": "proj"}
    )
