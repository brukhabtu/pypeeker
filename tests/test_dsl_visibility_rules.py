"""The visibility / reference-counting family: what fires, what is exempt, on what evidence.

``scripts/differential-check.py`` grades three of these five against the frozen
engine on this repository — over-exposed-export, over-exposed-module-symbol and
unused-public-symbol all report findings there, tiers included. It grades the
other two at 0-vs-0, which proves only that they do not over-fire: the
materialized target holds no test paths, so ``test-only-production-code`` has
nothing to find, and ``born-private``'s ratchet is unarmed there, so both
engines are silent. This file is
the other half of the proof — every rule made to *actually fire* over a
hand-built corpus, on the exact wording and the exact confidence tier — plus
the two acceptance criteria the oracle cannot express:

* **AC #1** — the barrel exemption is a semi-join whose exemption set is
  exactly right, and it suppresses a finding that reappears when the re-export
  is deleted.
* **AC #2** — the dynamic-access weakening applies to exactly five rules,
  checked structurally against the built expressions rather than asserted.
"""

import json
import tomllib
from pathlib import Path

import pytest

from pypeeker.dsl import (
    BARREL_EXPORTS,
    BASELINE_NAMESPACES,
    DYNAMIC_ACCESS_WEAKENED_RULES,
    RECORDED_PUBLIC_SYMBOLS,
    RULES,
    Const,
    Corpus,
    Expr,
    ProjectedSet,
    Reach,
    SemiJoin,
    Weaken,
    dsl_rule,
)
from pypeeker.dsl.selection import Selection, _Where
from pypeeker.dsl import visibility
from pypeeker.models import Confidence

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "scripts" / "parity-manifest.toml"

FAMILY = (
    "born-private",
    "over-exposed-export",
    "over-exposed-module-symbol",
    "test-only-production-code",
    "unused-public-symbol",
)

# A two-file package: the barrel re-exports `Thing`, nothing else uses it.
# `helper` is used from another module, so it is not module-local.
BARREL_INIT = "from pkg.mod import Thing\n"
MOD = """\
class Thing:
    pass


def helper():
    return 1
"""
USER = """\
from pkg.mod import helper


def go():
    return helper()
"""


@pytest.fixture
def corpus_of(indexed_project):
    """Build a corpus over ``files`` with no source-root filter.

    Paths carry no ``src/`` prefix on purpose: the binder derives a module id
    from the indexed path, so ``pkg/mod.py`` is the module ``pkg.mod`` and an
    ``import pkg.mod`` in a sibling file resolves. Prefixing every path with
    ``src/`` would make the module ``src.pkg.mod`` and quietly break every
    cross-file resolution these rules are about.
    """

    def _build(files: dict[str, str]) -> Corpus:
        _, store = indexed_project(files)
        return Corpus(store, ())

    return _build


@pytest.fixture
def package(corpus_of):
    """The three-file package above."""
    return corpus_of({
        "pkg/__init__.py": BARREL_INIT,
        "pkg/mod.py": MOD,
        "pkg/user.py": USER,
    })


def _weaken_nodes(selection: Selection) -> list[Weaken]:
    """Every :class:`Weaken` node in a built selection's filter stages.

    Walks ``_stages`` directly: a selection publishes its stage *names*, not
    its expressions, and the question here — does this rule's expression carry
    the weakening — is exactly a question about the expression tree.
    """
    found: list[Weaken] = []
    for stage in selection._stages:
        if not isinstance(stage, _Where):
            continue
        pending: list[Expr] = [stage.expr]
        while pending:
            node = pending.pop()
            if isinstance(node, Weaken):
                found.append(node)
            pending.extend(node.children)
    return found


def _messages(rule_id: str, corpus: Corpus, options: dict | None = None) -> list[str]:
    return [f.message for f in dsl_rule(rule_id).findings(options or {}, corpus)]


def _record_baseline(corpus: Corpus, symbol_ids: list[str]) -> None:
    """Hand-write the symbol baseline the frozen rule would have seeded.

    The port never writes one — seeding is a mutation, and the read half has no
    mutation terminals — so writing it here is how the *read* side gets
    exercised at all. It also arms ``born-private``'s gate: the namespace being
    present is what makes the rule fire on anything, whatever it lists.
    """
    path = corpus.store.project_root / ".pypeeker" / "check-baseline.json"
    path.write_text(json.dumps({"symbols": symbol_ids}), encoding="utf-8")


# ---------------------------------------------------------------------------
# AC #1 — the barrel exemption as a semi-join
# ---------------------------------------------------------------------------


def test_the_barrel_exemption_set_is_the_canonical_ids_the_barrel_re_exports(package):
    """The semi-join's right-hand side holds the *definition*, not the alias.

    ``pkg/__init__.py`` binds an IMPORT symbol ``pkg:Thing``; the exemption is
    for what that resolves to, ``pkg.mod:Thing``, because that is the id a
    candidate row carries.
    """
    assert BARREL_EXPORTS.values(package) == frozenset({"pkg.mod:Thing"})


def test_the_projected_set_is_one_column_and_names_it(package):
    assert isinstance(BARREL_EXPORTS, ProjectedSet)
    assert BARREL_EXPORTS.column == "symbol_id"


def test_a_barrel_re_export_is_exempt_from_unused_public_symbol(package):
    """`Thing` has no references at all, and is still not flagged."""
    assert "public class 'pkg.mod:Thing' has no references in the project" not in _messages(
        "unused-public-symbol", package
    )


def test_deleting_the_re_export_makes_the_same_symbol_fire(corpus_of):
    """The exemption is doing the work — remove it and the finding appears."""
    without_barrel = corpus_of({"pkg/__init__.py": "", "pkg/mod.py": MOD, "pkg/user.py": USER})
    assert BARREL_EXPORTS.values(without_barrel) == frozenset()
    assert (
        "public class 'pkg.mod:Thing' has no references in the project"
        in _messages("unused-public-symbol", without_barrel)
    )


def test_the_exemption_set_is_computed_once_per_corpus(package):
    """Five rules asking the same question pay for one scan.

    ``Corpus.materialize`` keys structurally, so the second call returns the
    identical object rather than an equal one.
    """
    first = BARREL_EXPORTS.values(package)
    assert BARREL_EXPORTS.values(package) is first


def test_every_rule_in_the_family_is_claimed_by_the_parity_manifest():
    """Ported means graded. A rule in ``RULES`` that no target grades is a claim
    nobody checks — including ``born-private``, whose gate exists precisely so
    that its agreement with the frozen rule survives being graded.

    Subset rather than equality: the manifest's ``claimed`` list is shared with
    every other phase-3 port, so this asserts what *this* family owes it.
    """
    manifest = tomllib.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(FAMILY) <= set(manifest["claimed"])


# ---------------------------------------------------------------------------
# AC #2 — the dynamic-access weakening, and exactly where it applies
# ---------------------------------------------------------------------------


def test_the_weakened_rule_inventory_is_the_five_the_frozen_engine_weakens():
    assert tuple(sorted(DYNAMIC_ACCESS_WEAKENED_RULES)) == FAMILY


def test_exactly_the_inventoried_rules_carry_a_weaken_node():
    """Structural, not behavioural: the node is in the expression or it is not.

    A rule is either a single selection (``DslRule.build``) or several parts
    (``MultiPartRule.parts``, phase 3c), and a part's build may return ``None``
    when configuration switches it off — both shapes are walked.
    """
    weakening = set()
    for name, rule in RULES.items():
        builds = [rule.build] if hasattr(rule, "build") else [p.build for p in rule.parts]
        selections = [s for s in (b({}) for b in builds) if s is not None]
        if any(_weaken_nodes(s) for s in selections):
            weakening.add(name)
    assert weakening == set(DYNAMIC_ACCESS_WEAKENED_RULES)


def test_a_weakening_rule_carries_exactly_one_weaken_node():
    for name in DYNAMIC_ACCESS_WEAKENED_RULES:
        nodes = _weaken_nodes(dsl_rule(name).build({}))
        assert len(nodes) == 1, name
        assert nodes[0].level is Confidence.HEURISTIC, name


@pytest.mark.parametrize("rule_id", FAMILY)
def test_every_rule_in_the_family_reaches_project(rule_id):
    """Reach is derived: each of these consults the resolver, and says so."""
    assert dsl_rule(rule_id).build({}).reach is Reach.PROJECT


# ---------------------------------------------------------------------------
# AC #3 (TASK-165) — module-less files: MODULE_FILES ports the frozen
# ``module_id is None -> continue`` guard to the row side
# ---------------------------------------------------------------------------
#
# A root-level ``__init__.py`` is genuinely module-less: ``paths
# .module_path_from`` collapses a bare ``__init__`` to ``""``, and the binder
# (``binder/binder.py``, ``if not state.module_path: return``) emits no
# MODULE symbol for an empty module path — confirmed directly below, not
# assumed. Its top-level symbols still bind, with ``parent_scope_id == ""``.
#
# The end-to-end "no finding from the module-less file" assertion near the
# bottom of this section is a behavioural lock, not proof the fix is wired
# in: clause 4 of ``_candidate_clauses`` (``is_module_level``,
# ``parent_scope_id == env.module``) already happens to exclude these rows
# today, fix or no fix, because ``env.module`` is a file path while
# ``parent_scope_id`` is ``""`` — this is masked on this codebase's binder by
# construction, not by the fix. The test that actually distinguishes
# "wired in" from "not wired in" is the structural one right below the
# set-level checks: it inspects the built expression tree for a
# ``SemiJoin`` against :data:`visibility.MODULE_FILES`, which a manual
# rollback (confirmed while writing this test, not left in the tree) makes
# fail.


def test_a_root_level_init_file_binds_no_module_symbol(corpus_of):
    corpus = corpus_of({"__init__.py": "def orphan():\n    return 1\n"})
    index = corpus.store.load("__init__.py")
    assert not any(s.kind.value == "module" for s in index.symbols)
    (orphan,) = [s for s in index.symbols if s.name == "orphan"]
    assert orphan.parent_scope_id == ""


def test_module_files_excludes_a_file_with_no_module_symbol(corpus_of):
    corpus = corpus_of({
        "__init__.py": "def orphan():\n    return 1\n",
        "pkg/mod.py": MOD,
        "pkg/user.py": USER,
    })
    assert visibility.MODULE_FILES.column == "file_path"
    values = visibility.MODULE_FILES.values(corpus)
    assert "__init__.py" not in values
    assert {"pkg/mod.py", "pkg/user.py"} <= values


def test_the_module_files_semi_join_drops_rows_from_the_module_less_file(corpus_of):
    """The set-and-semi-join mechanics in isolation, independent of any rule."""
    from pypeeker.dsl import all_of, in_set, row, symbols

    corpus = corpus_of({
        "__init__.py": "def orphan():\n    return 1\n",
        "pkg/mod.py": MOD,
        "pkg/user.py": USER,
    })
    unfiltered = symbols().where(row.file_path.eq("__init__.py")).rows(corpus)
    assert any(m.fields["name"] == "orphan" for m in unfiltered)

    filtered = symbols().where(
        all_of(row.file_path.eq("__init__.py"), in_set(row.file_path, visibility.MODULE_FILES))
    ).rows(corpus)
    assert filtered == ()

    other_files = symbols().where(in_set(row.file_path, visibility.MODULE_FILES)).rows(corpus)
    assert any(m.fields["file_path"] == "pkg/mod.py" for m in other_files)
    assert not any(m.fields["file_path"] == "__init__.py" for m in other_files)


def _semi_join_targets(selection: Selection) -> list[SemiJoin]:
    """Every :class:`SemiJoin` node in a built selection's filter stages.

    Mirrors :func:`_weaken_nodes`: the question here — does this rule's
    candidate prefix actually carry the ``MODULE_FILES`` semi-join, as
    opposed to merely having the set defined somewhere in the module — is a
    question about the expression tree, not about behaviour that this
    codebase's binder happens to mask end-to-end (see the section comment
    above).
    """
    found: list[SemiJoin] = []
    for stage in selection._stages:
        if not isinstance(stage, _Where):
            continue
        pending: list[Expr] = [stage.expr]
        while pending:
            node = pending.pop()
            if isinstance(node, SemiJoin):
                found.append(node)
            pending.extend(node.children)
    return found


@pytest.mark.parametrize("rule_id", FAMILY)
def test_every_rule_in_the_family_wires_module_files_into_its_candidate_clauses(rule_id):
    """The proof: the built selection actually carries the semi-join.

    Every rule in the family opens with :func:`visibility._candidate_clauses`
    (four of them) or, for ``over-exposed-export``, its own clause list that
    ports the same guard — both now conjoin
    ``in_set(row.file_path, MODULE_FILES)``. Structural equality on
    :class:`~pypeeker.dsl.ProjectedSet` (identity is the built selection, not
    object identity — see ``test_the_exemption_set_is_computed_once_per_corpus``
    for the same property on ``BARREL_EXPORTS``) lets this assert equality to
    the real :data:`visibility.MODULE_FILES` rather than merely "some
    ``SemiJoin`` exists".
    """
    targets = _semi_join_targets(dsl_rule(rule_id).build({}))
    assert any(node.rhs == visibility.MODULE_FILES for node in targets), [
        node.rhs for node in targets
    ]


@pytest.mark.parametrize("rule_id", FAMILY)
def test_no_rule_in_the_family_reports_a_finding_anchored_in_a_module_less_file(
    corpus_of, rule_id
):
    corpus = corpus_of({
        "__init__.py": "def orphan():\n    return 1\n",
        "pkg/mod.py": MOD,
        "pkg/user.py": USER,
    })
    findings = dsl_rule(rule_id).findings({}, corpus)
    assert not any(f.path == "__init__.py" for f in findings)


DYNAMIC_MOD = """\
import os


class Thing:
    pass


def helper():
    return getattr(os, "sep")
"""


def test_a_finding_is_declared_when_its_module_does_not_use_dynamic_access(corpus_of):
    corpus = corpus_of({"lone.py": "def orphan():\n    return 1\n"})
    findings = dsl_rule("unused-public-symbol").findings({}, corpus)
    assert [f.confidence for f in findings] == [Confidence.DECLARED]


def test_a_finding_is_heuristic_when_its_module_uses_dynamic_access(corpus_of):
    """The row still fires — the weakening labels, it never filters."""
    corpus = corpus_of({"lone.py": 'import os\n\n\ndef orphan():\n    return getattr(os, "sep")\n'})
    findings = dsl_rule("unused-public-symbol").findings({}, corpus)
    assert [(f.message, f.confidence) for f in findings] == [
        ("public function 'lone:orphan' has no references in the project", Confidence.HEURISTIC)
    ]


@pytest.mark.parametrize(
    ("rule_id", "subject"),
    [
        ("unused-public-symbol", "dyn:solo"),
        ("over-exposed-module-symbol", "dyn:solo"),
        ("born-private", "'solo'"),
        ("over-exposed-export", "exports 'Widget'"),
        ("test-only-production-code", "shipped:widget"),
    ],
)
def test_every_rule_in_the_family_weakens_near_dynamic_access(corpus_of, rule_id, subject):
    """One corpus giving each of the five a row whose defining module calls ``getattr``.

    ``dyn`` is module-local and unreferenced (the three symbol-side rules),
    ``bare/__init__`` is a barrel that calls ``getattr`` itself
    (over-exposed-export), and ``shipped`` is referenced only from a test
    (test-only-production-code). Each rule's finding about its own subject must
    come out ``HEURISTIC`` — the row is still reported, which is the point of
    weakening rather than filtering.

    The corpus is seeded with an empty symbol baseline so ``born-private``'s
    armed gate is open; the other four never read the file.
    """
    corpus = corpus_of({
        "dyn.py": 'import os\n\n\ndef solo():\n    return getattr(os, "sep")\n',
        "shipped.py": 'import os\n\n\ndef widget():\n    return getattr(os, "sep")\n',
        "bare/__init__.py": 'import os\n\nfrom bare.impl import Widget\n\ngetattr(os, "sep")\n',
        "bare/impl.py": "class Widget:\n    pass\n",
        "tests/test_shipped.py": (
            "from shipped import widget\n\n\ndef test_it():\n    assert widget()\n"
        ),
    })
    _record_baseline(corpus, [])
    matching = [f for f in dsl_rule(rule_id).findings({}, corpus) if subject in f.message]
    assert len(matching) == 1, [f.message for f in dsl_rule(rule_id).findings({}, corpus)]
    assert matching[0].confidence is Confidence.HEURISTIC


# ---------------------------------------------------------------------------
# unused-public-symbol
# ---------------------------------------------------------------------------


def test_unused_public_symbol_words_a_finding_as_the_frozen_rule_does(package):
    assert _messages("unused-public-symbol", package) == [
        "public function 'pkg.user:go' has no references in the project"
    ]


def test_a_referenced_symbol_is_not_flagged(package):
    """`helper` is called from `pkg.user`, so it is referenced."""
    assert not any("helper" in m for m in _messages("unused-public-symbol", package))


def test_also_private_widens_the_selection_to_underscore_names(corpus_of):
    corpus = corpus_of({"lone.py": "def _hidden():\n    return 1\n"})
    assert _messages("unused-public-symbol", corpus) == []
    assert _messages("unused-public-symbol", corpus, {"also-private": True}) == [
        "protected function 'lone:_hidden' has no references in the project"
    ]


def test_an_allowed_decorator_exempts_a_symbol(corpus_of):
    corpus = corpus_of({"lone.py": "import click\n\n\n@click.command()\ndef run():\n    return 1\n"})
    assert _messages("unused-public-symbol", corpus) != []
    assert _messages("unused-public-symbol", corpus, {"allow-decorators": ["click.command"]}) == []


def test_the_global_visibility_table_merges_into_allow_decorators(corpus_of):
    """The reserved ``visibility`` key ``check.config`` injects into every rule."""
    corpus = corpus_of({"lone.py": "import click\n\n\n@click.command()\ndef run():\n    return 1\n"})
    options = {"visibility": {"allow-decorators": ["click.command"]}}
    assert _messages("unused-public-symbol", corpus, options) == []


def test_dunders_and_main_and_main_modules_are_never_candidates(corpus_of):
    corpus = corpus_of({
        "lone.py": "def main():\n    return 1\n\n\ndef __getattr__(name):\n    return name\n",
        "__main__.py": "def orphan():\n    return 1\n",
    })
    assert _messages("unused-public-symbol", corpus) == []


# ---------------------------------------------------------------------------
# over-exposed-module-symbol
# ---------------------------------------------------------------------------


def test_over_exposed_module_symbol_words_a_finding_as_the_frozen_rule_does(package):
    assert _messages("over-exposed-module-symbol", package) == [
        "public 'pkg.user:go' is only used within its module — make it _go"
    ]


def test_cross_module_use_suppresses_over_exposure(corpus_of):
    """`helper` is used from `pkg.user`; `local` only from its own module."""
    corpus = corpus_of({
        "pkg/mod.py": "def helper():\n    return 1\n\n\ndef local():\n    return 2\n\n\n"
        "def caller():\n    return local()\n",
        "pkg/user.py": "from pkg.mod import helper\n\n\ndef go():\n    return helper()\n",
    })
    flagged = _messages("over-exposed-module-symbol", corpus)
    assert "public 'pkg.mod:local' is only used within its module — make it _local" in flagged
    assert not any("pkg.mod:helper" in m for m in flagged)


def test_the_kinds_option_widens_to_variables_and_drops_unknown_values(corpus_of):
    corpus = corpus_of({"lone.py": "CONSTANT = 1\n"})
    assert _messages("over-exposed-module-symbol", corpus) == []
    assert _messages("over-exposed-module-symbol", corpus, {"kinds": ["variable"]}) == [
        "public 'lone:CONSTANT' is only used within its module — make it _CONSTANT"
    ]
    # An unparseable kind is dropped, not defaulted — the frozen contract.
    assert _messages("over-exposed-module-symbol", corpus, {"kinds": ["nonsense"]}) == []


def test_the_allow_option_matches_a_symbol_id_or_its_module(package):
    assert _messages("over-exposed-module-symbol", package, {"allow": ["pkg.user:go"]}) == []
    assert _messages("over-exposed-module-symbol", package, {"allow": ["pkg.user"]}) == []
    assert _messages("over-exposed-module-symbol", package, {"allow": ["pkg.other"]}) != []


# ---------------------------------------------------------------------------
# over-exposed-export
# ---------------------------------------------------------------------------


def test_over_exposed_export_words_a_finding_as_the_frozen_rule_does(package):
    assert _messages("over-exposed-export", package) == [
        "package 'pkg' exports 'Thing' but no outside consumer uses it — drop the re-export"
    ]


def test_an_outside_consumer_keeps_the_re_export(corpus_of):
    corpus = corpus_of({
        "pkg/__init__.py": BARREL_INIT,
        "pkg/mod.py": MOD,
        "outside.py": "from pkg import Thing\n\n\ndef make():\n    return Thing()\n",
    })
    assert _messages("over-exposed-export", corpus) == []


def test_a_re_export_of_something_outside_the_corpus_is_not_an_export(corpus_of):
    """The locatability clause: a stdlib name has no in-package definition.

    This is the pair whose order is load-bearing — ``not_(kind.eq(IMPORT))`` is
    *true* for an unlocatable definition, so only the preceding locatability
    clause keeps this from being reported as a droppable re-export.
    """
    corpus = corpus_of({"pkg/__init__.py": "from collections import Counter\n"})
    assert _messages("over-exposed-export", corpus) == []


def test_a_protected_import_name_is_not_an_export(corpus_of):
    corpus = corpus_of({
        "pkg/__init__.py": "from pkg.mod import Thing as _Thing\n",
        "pkg/mod.py": MOD,
    })
    assert _messages("over-exposed-export", corpus) == []


def test_library_mode_protects_every_barrel_export_by_default(package):
    """No ``public-roots`` means every top-level package is a root."""
    assert _messages("over-exposed-export", package, {"visibility": {"mode": "library"}}) == []


def test_library_mode_with_explicit_roots_protects_only_those(package):
    protected = {"visibility": {"mode": "library", "public-roots": ["pkg"]}}
    elsewhere = {"visibility": {"mode": "library", "public-roots": ["other"]}}
    assert _messages("over-exposed-export", package, protected) == []
    assert _messages("over-exposed-export", package, elsewhere) != []


def test_an_unknown_visibility_mode_is_app_mode(package):
    """``parse_visibility_config`` coerces anything but ``library`` to ``app``."""
    assert _messages("over-exposed-export", package, {"visibility": {"mode": "libary"}}) != []


def test_the_allow_option_matches_the_export_or_its_definition(package):
    assert _messages("over-exposed-export", package, {"allow": ["pkg:Thing"]}) == []
    assert _messages("over-exposed-export", package, {"allow": ["pkg.mod:Thing"]}) == []
    assert _messages("over-exposed-export", package, {"allow": ["pkg.mod"]}) == []
    assert _messages("over-exposed-export", package, {"allow": ["nothing"]}) != []


# ---------------------------------------------------------------------------
# test-only-production-code (0-vs-0 on the oracle: it must fire here)
# ---------------------------------------------------------------------------


TEST_ONLY = {
    "shipped.py": "def widget():\n    return 1\n\n\ndef used():\n    return 2\n",
    "app.py": "from shipped import used\n\n\ndef go():\n    return used()\n",
    "tests/test_shipped.py": (
        "from shipped import used, widget\n\n\n"
        "def test_widget():\n    assert widget()\n\n\n"
        "def test_used():\n    assert used()\n"
    ),
}


def test_test_only_production_code_fires_on_a_symbol_only_tests_reference(corpus_of):
    assert _messages("test-only-production-code", corpus_of(TEST_ONLY)) == [
        "'shipped:widget' is referenced only from tests"
    ]


def test_the_shortened_message_is_the_exact_wording_the_ledger_sanctions(corpus_of):
    """The oracle grades nothing here (0 findings on target ``self``), so pin it.

    The frozen rule appends ``(N test reference[s])``; dropping the count is a
    declared ``kind = "message"`` divergence, which pre-authorizes *any*
    wording. This test is what the wording is actually pinned by.
    """
    findings = dsl_rule("test-only-production-code").findings({}, corpus_of(TEST_ONLY))
    assert [f.message for f in findings] == ["'shipped:widget' is referenced only from tests"]
    assert all("test reference" not in f.message for f in findings)


def test_a_production_reference_anywhere_suppresses_the_finding(corpus_of):
    """`used` is called from `app`, so it is not test-only."""
    assert not any("used" in m for m in _messages("test-only-production-code", corpus_of(TEST_ONLY)))


def test_a_symbol_with_no_references_at_all_belongs_to_unused_public_symbol(corpus_of):
    corpus = corpus_of({"shipped.py": "def orphan():\n    return 1\n"})
    assert _messages("test-only-production-code", corpus) == []
    assert _messages("unused-public-symbol", corpus) != []


def test_symbols_defined_in_test_files_are_out_of_scope(corpus_of):
    """`test_shipped:helper` is module-local and unreferenced, and still not flagged."""
    corpus = corpus_of({
        "shipped.py": "def widget():\n    return 1\n",
        "tests/test_shipped.py": (
            "from shipped import widget\n\n\ndef helper():\n    return 1\n\n\n"
            "def test_widget():\n    assert widget()\n"
        ),
    })
    flagged = _messages("test-only-production-code", corpus)
    assert flagged == ["'shipped:widget' is referenced only from tests"]


def test_custom_test_globs_replace_the_defaults(corpus_of):
    corpus = corpus_of({
        "shipped.py": "def widget():\n    return 1\n",
        "spec/spec_shipped.py": "from shipped import widget\n\n\ndef check():\n    return widget()\n",
    })
    assert _messages("test-only-production-code", corpus) == []
    assert _messages("test-only-production-code", corpus, {"test-globs": ["spec/**"]}) == [
        "'shipped:widget' is referenced only from tests"
    ]


def test_a_barrel_re_export_is_exempt_from_test_only_too(corpus_of):
    corpus = corpus_of({
        "pkg/__init__.py": BARREL_INIT,
        "pkg/mod.py": MOD,
        "tests/test_mod.py": "from pkg.mod import Thing\n\n\ndef test_it():\n    assert Thing()\n",
    })
    assert _messages("test-only-production-code", corpus) == []


# ---------------------------------------------------------------------------
# born-private (0-vs-0 on the oracle target: the firing behaviour lives here)
# ---------------------------------------------------------------------------


def test_born_private_is_silent_until_the_ratchet_is_armed(package):
    """No ``"symbols"`` namespace, no findings — the frozen rule's early return.

    ``check/builtin/born_private.py`` answers an unseeded project by writing the
    baseline and returning ``[]``. The port cannot write, but it must agree on
    the output, and it must agree *without* having been handed a file the old
    engine wrote — otherwise the oracle's 0-vs-0 on this rule would be an
    artefact of which engine ran first.
    """
    assert _messages("born-private", package) == []


def test_arming_the_ratchet_makes_the_same_symbol_fire(package):
    """The gate is doing the work: seed the namespace and the finding appears."""
    _record_baseline(package, [])
    assert _messages("born-private", package) == [
        "newly public 'go' is only used within its module — make it _go "
        "or record it (`check --update-baseline`)"
    ]


def test_a_seeded_empty_namespace_is_armed_not_absent(package):
    """``has_symbol_baseline``'s documented edge, ported exactly.

    A project seeded when it had no public symbols stores ``[]``. That must
    read as "already seeded — every later public symbol is new", which is the
    opposite of what the same empty id set means when the namespace is missing.
    """
    _record_baseline(package, [])
    assert BASELINE_NAMESPACES.values(package) == frozenset({"symbols"})
    assert RECORDED_PUBLIC_SYMBOLS.values(package) == frozenset()
    assert _messages("born-private", package) != []


def test_a_recorded_symbol_is_never_relitigated(package):
    _record_baseline(package, ["pkg.user:go"])
    assert _messages("born-private", package) == []


def test_a_baseline_recording_something_else_leaves_the_finding(package):
    _record_baseline(package, ["pkg.mod:helper"])
    assert _messages("born-private", package) != []


def test_a_baseline_with_no_symbols_namespace_leaves_the_ratchet_unarmed(package):
    """A ``"violations"``-only baseline is the unseeded state, not an empty one."""
    path = package.store.project_root / ".pypeeker" / "check-baseline.json"
    path.write_text(json.dumps({"violations": {}}), encoding="utf-8")
    assert BASELINE_NAMESPACES.values(package) == frozenset({"violations"})
    assert _messages("born-private", package) == []


def test_the_gate_is_the_first_clause_of_the_conjunction(package):
    """Order is normative (fork #3), and here it is also what empties the rule.

    The frozen early return precedes every finding, so the ported gate is the
    head of the ``all_of`` rather than one conjunct among many.
    """
    top = dsl_rule("born-private").build({})._stages[0].expr
    head = top.operands[0]
    assert isinstance(head, SemiJoin)
    assert head.rhs is BASELINE_NAMESPACES
    assert head.key == Const("symbols")


def test_cross_module_use_justifies_a_newly_public_symbol(corpus_of):
    corpus = corpus_of({
        "pkg/mod.py": "def helper():\n    return 1\n",
        "pkg/user.py": "from pkg.mod import helper\n\n\ndef go():\n    return helper()\n",
    })
    _record_baseline(corpus, [])
    assert not any("helper" in m for m in _messages("born-private", corpus))


def test_a_non_mapping_visibility_option_refuses_loudly():
    """A parsed VisibilityConfig cannot legally cross into dsl (project is
    outside its import boundary), so anything but the raw mapping is a wiring
    mistake — refused, never silently degraded to app-mode defaults."""

    class NotAMapping:
        mode = "library"

    with pytest.raises(TypeError, match="raw \\[tool.pypeeker.visibility\\] mapping"):
        visibility._visibility_table({"visibility": NotAMapping()})
