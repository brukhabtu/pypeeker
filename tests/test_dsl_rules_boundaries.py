"""``import-boundaries`` on the DSL: what fires, in what words, on what evidence.

The differential oracle grades this rule against the frozen engine on
``tests/fixtures/parity/boundaries``. These tests pin the half the oracle
cannot: pypeeker's own gated self-lint holds ``import-boundaries`` at **zero**
findings, so the ``self`` target compares 0-vs-0 and an engine that emitted
nothing at all would sail through it. Everything below builds an index by hand
and asserts the exact :class:`~pypeeker.dsl.Finding` list — path, line, wording
and confidence tier — for each of the rule's three quantifications.
"""

import pytest

from pypeeker.dsl import (
    Corpus,
    Finding,
    MultiPartRule,
    Reach,
    dsl_rule,
)
from pypeeker.dsl import sweeps
from pypeeker.models import Confidence

RULE = "import-boundaries"

# A project rooted at `app` with three interesting layering shapes:
#
#   api  -> may import core (and a `ghost` package that does not exist)
#   core -> may import nothing
#   db   -> may import core (never exercised)
#   util -> undeclared, and has NO __init__.py
#   zed  -> undeclared, and HAS an __init__.py
#   cli  -> declared unconstrained, and does violate the layering
#
# `core/__init__.py` re-exports a `db` symbol, so `api`'s import of it launders
# a db dependency through a core barrel; the resolver charges it to db.
BOUNDARY_FILES = {
    "app/__init__.py": "",
    "app/api/__init__.py": "",
    "app/api/handler.py": '''\
"""Api handler."""
import importlib

from app.core import open_session as laundered
from app.core.engine import run
from app.db.session import open_session

_dyn = importlib.import_module("app.db.session")


def handle():
    """Handle."""
    return run(), open_session(), laundered(), _dyn
''',
    "app/cli/__init__.py": "",
    "app/cli/main.py": '''\
"""Cli main, unconstrained."""
from app.db.session import open_session


def go():
    """Go."""
    return open_session()
''',
    "app/core/__init__.py": '''\
"""Core barrel that re-exports a db symbol (laundering)."""
from app.db.session import open_session

__all__ = ["open_session"]
''',
    "app/core/engine.py": '''\
"""Core engine."""
from app.db.session import open_session


def run():
    """Run it."""
    return open_session()
''',
    "app/db/__init__.py": "",
    "app/db/session.py": '''\
"""Db session."""


def open_session():
    """Open."""
    return 1
''',
    "app/util/alpha.py": '"""Alpha util."""\n',
    "app/util/beta.py": '"""Beta util."""\n',
    "app/zed/__init__.py": '"""Zed barrel."""\n',
    "app/zed/aaa.py": '"""Zed aaa."""\n',
}

OPTIONS = {
    "root": "app",
    "strict": True,
    "unconstrained": ["cli"],
    "report-unused-allowances": True,
    "allow": {"api": ["core", "ghost"], "core": [], "db": ["core"]},
}


def _finding(path: str, line: int, message: str, confidence=Confidence.DECLARED):
    return Finding(rule=RULE, path=path, line=line, message=message, confidence=confidence)


LAUNDERED = _finding(
    "app/api/handler.py",
    4,
    "package 'api' may not import 'db' (via re-export 'app.core.open_session')",
)
DIRECT = _finding(
    "app/api/handler.py",
    6,
    "package 'api' may not import 'db' (via 'app.db.session.open_session')",
)
DYNAMIC = _finding(
    "app/api/handler.py",
    8,
    "package 'api' may not import 'db' (via 'app.db.session')",
    Confidence.HEURISTIC,
)
CORE_BARREL = _finding(
    "app/core/__init__.py",
    2,
    "package 'core' may not import 'db' (via 'app.db.session.open_session')",
)
CORE_ENGINE = _finding(
    "app/core/engine.py",
    2,
    "package 'core' may not import 'db' (via 'app.db.session.open_session')",
)
UNDECLARED_UTIL = _finding(
    "app/util/alpha.py",
    1,
    "package 'util' is not declared in import-boundaries (add it to "
    "[tool.pypeeker.import-boundaries.allow] or to the 'unconstrained' list)",
)
UNDECLARED_ZED = _finding(
    "app/zed/__init__.py",
    1,
    "package 'zed' is not declared in import-boundaries (add it to "
    "[tool.pypeeker.import-boundaries.allow] or to the 'unconstrained' list)",
)
UNUSED_GHOST = _finding(
    "pyproject.toml",
    1,
    "unused import-boundaries allowance: 'api' is permitted to import 'ghost' but never does",
)
UNUSED_DB_CORE = _finding(
    "pyproject.toml",
    1,
    "unused import-boundaries allowance: 'db' is permitted to import 'core' but never does",
)


@pytest.fixture
def boundary_corpus(indexed_project):
    """The layered project above, indexed, scoped to ``app/``.

    Paths are rooted at the package itself rather than under a ``src/`` prefix:
    the binder derives a module id from the indexed path verbatim, and the
    ``root = "app"`` option has to match it. ``pypeeker index src`` strips the
    configured source root before binding, which is why the parity fixture on
    disk does keep its ``src/`` layer.
    """
    _, store = indexed_project(BOUNDARY_FILES)
    return Corpus(store, ("app",))


def _rule() -> MultiPartRule:
    rule = dsl_rule(RULE)
    assert isinstance(rule, MultiPartRule)
    return rule


def _part(index: int, options=OPTIONS):
    """One part's selection, built from ``options``. ``None`` when switched off."""
    return _rule().parts[index].build(options)


def _part_findings(index: int, corpus, options=OPTIONS):
    """One part's findings, run in isolation from its siblings."""
    part = _rule().parts[index]
    selection = part.build(options)
    assert selection is not None
    return MultiPartRule(RULE, (part,)).findings(options, corpus)


# ---------------------------------------------------------------------------
# the whole family
# ---------------------------------------------------------------------------


def test_the_family_reports_all_three_parts_in_written_order(boundary_corpus):
    assert _rule().findings(OPTIONS, boundary_corpus) == [
        LAUNDERED,
        DIRECT,
        DYNAMIC,
        CORE_BARREL,
        CORE_ENGINE,
        UNDECLARED_UTIL,
        UNDECLARED_ZED,
        UNUSED_GHOST,
        UNUSED_DB_CORE,
    ]


# ---------------------------------------------------------------------------
# part (a): per-import violations
# ---------------------------------------------------------------------------


def test_the_import_part_charges_each_violation_to_the_origin_package(boundary_corpus):
    assert _part_findings(0, boundary_corpus) == [
        LAUNDERED,
        DIRECT,
        DYNAMIC,
        CORE_BARREL,
        CORE_ENGINE,
    ]


def test_a_re_export_is_charged_to_the_defining_package_and_worded_as_laundering(
    boundary_corpus,
):
    # `from app.core import open_session` reads as a core import; the resolver
    # follows the barrel to app.db.session, so it is charged to db and the
    # message says so.
    reported = _part_findings(0, boundary_corpus)
    assert LAUNDERED in reported
    # Charged to db, never to core — the literal `imported_from` package is
    # quoted in the parenthetical but is not what the rule enforces against.
    assert not [f for f in reported if "may not import 'core'" in f.message]


def test_a_dynamically_recovered_import_reports_at_heuristic(boundary_corpus):
    # The tier is the import symbol's own import_confidence, carried onto the
    # judged-import row as its intrinsic evidence and never judged by the
    # sweep — the old rule's `symbol.import_confidence or DECLARED`, arrived at
    # through the meet.
    by_line = {f.line: f for f in _part_findings(0, boundary_corpus)}
    assert by_line[8].confidence is Confidence.HEURISTIC
    assert by_line[6].confidence is Confidence.DECLARED


def test_an_unconstrained_package_is_never_flagged_for_its_imports(boundary_corpus):
    # src/app/cli/main.py imports app.db.session, which no allow entry permits;
    # `cli` is absent from `allow` altogether, so it is unconstrained.
    assert not [f for f in _rule().findings(OPTIONS, boundary_corpus) if "cli" in f.path]


def test_a_permitted_import_is_not_reported(boundary_corpus):
    # api -> core (app.core.engine.run) is allowed, so handler.py:5 is silent
    # even though its neighbours on lines 4, 6 and 8 are not.
    assert 5 not in {f.line for f in _part_findings(0, boundary_corpus)}


def test_the_sweep_carries_permitted_imports_so_the_rule_does_the_rejecting(
    boundary_corpus,
):
    # The row source is one row per import occurrence in a policed package, not
    # one row per violation: `handler.py:5` (api -> core, allowed) and
    # `core/__init__.py:2`'s siblings are all present, tagged violates=False.
    # That is what keeps `row.violates.is_true()` a rejection the DSL performs
    # rather than one the sweep already made — and it is what part (c) reads,
    # since an occurrence that exercises an allowance is exactly one the rule
    # must not report.
    rows = sweeps._boundary_tables(boundary_corpus, sweeps.boundary_params(OPTIONS)).imports
    carried = {(entry.file_path, entry.line): entry for entry in rows}
    permitted = carried[("app/api/handler.py", 5)]
    assert (permitted.violates, permitted.exercises, permitted.dep_pkg) == (False, True, "core")
    assert 5 not in {f.line for f in _part_findings(0, boundary_corpus)}
    # Every violating row is reported, and nothing else is.
    assert {(entry.file_path, entry.line) for entry in rows if entry.violates} == {
        (f.path, f.line) for f in _part_findings(0, boundary_corpus)
    }


# A symbol id is `<module>:<local>`, and a module id is not unique over indexed
# files — so `proj/dup.py` and `proj/dup/__init__.py` bind the SAME import
# symbol id for the same local name. A verdict table keyed on that id holds one
# entry where two occurrences exist, and the second reads the first's answer.
# The three tests below are the three ways that shows, all verified end to end
# against `pypeeker check --strict` on materialized copies of these trees.
SHARED_SYMBOL_OPTIONS = {"root": "app", "allow": {"dup": ["ok"]}}


def _colliding(module_half: str, package_half: str) -> dict[str, str]:
    """A `dup` package whose two halves share the module id `app.dup.mod`.

    Both halves bind the local name `go`, so both bind `app.dup.mod:go`. The
    import targets are deliberately unresolvable: `_origin_package` then falls
    back to the literal `imported_from`, which is per-occurrence, so the two
    halves really do disagree about what they are charged to.
    """
    return {
        "app/__init__.py": "",
        "app/dup/__init__.py": "",
        "app/ok/__init__.py": "",
        "app/dup/mod.py": module_half,
        "app/dup/mod/__init__.py": package_half,
    }


def _shared_symbol_corpus(indexed_project, files):
    _, store = indexed_project(files)
    corpus = Corpus(store, ("app",))
    # Guard: without the collision every assertion below would pass vacuously.
    assert _module_ids(corpus)["app.dup.mod"] == ["app/dup/mod.py", "app/dup/mod/__init__.py"]
    return corpus


def test_only_the_half_that_violates_reports_when_two_files_share_a_symbol_id(
    indexed_project,
):
    # `dup/mod.py` imports a forbidden package; `dup/mod/__init__.py` imports a
    # permitted one. The frozen engine reports exactly one finding. A verdict
    # keyed on `app.dup.mod:go` reported two — the extra one charged to the file
    # whose only import is allowed, naming a package it does not import.
    corpus = _shared_symbol_corpus(
        indexed_project,
        _colliding("from app.ghost1.z import go\n", "from app.ok.zz import go\n"),
    )
    assert _part_findings(0, corpus, SHARED_SYMBOL_OPTIONS) == [
        _finding(
            "app/dup/mod.py",
            1,
            "package 'dup' may not import 'ghost1' (via 'app.ghost1.z.go')",
        )
    ]


def test_each_half_of_a_collision_names_the_package_it_actually_imports(
    indexed_project,
):
    # Both halves violate, for different reasons. The count matches either way,
    # so this is the shape no count comparison catches: with one shared verdict
    # both messages named `omega`, the package judged last.
    corpus = _shared_symbol_corpus(
        indexed_project,
        _colliding("from app.alpha.z import go\n", "from app.omega.z import go\n"),
    )
    assert _part_findings(0, corpus, SHARED_SYMBOL_OPTIONS) == [
        _finding(
            "app/dup/mod.py",
            1,
            "package 'dup' may not import 'alpha' (via 'app.alpha.z.go')",
        ),
        _finding(
            "app/dup/mod/__init__.py",
            1,
            "package 'dup' may not import 'omega' (via 'app.omega.z.go')",
        ),
    ]


def test_each_half_of_a_collision_is_worded_with_its_own_re_export_status(
    indexed_project,
):
    # Both halves are charged to `bad`, but only one arrives through a barrel.
    # `via` versus `via re-export` is decided by comparing the resolved origin
    # against the occurrence's own literal `imported_from`, so a shared verdict
    # put one half's parenthetical on the other half's finding.
    files = _colliding("from app.hop import go\n", "from app.bad.x import go\n")
    files["app/bad/__init__.py"] = ""
    files["app/bad/x.py"] = 'def go():\n    """G."""\n    return 1\n'
    files["app/hop/__init__.py"] = "from app.bad.x import go\n"
    corpus = _shared_symbol_corpus(indexed_project, files)
    assert _part_findings(0, corpus, SHARED_SYMBOL_OPTIONS) == [
        _finding(
            "app/dup/mod.py",
            1,
            "package 'dup' may not import 'bad' (via re-export 'app.hop.go')",
        ),
        _finding(
            "app/dup/mod/__init__.py",
            1,
            "package 'dup' may not import 'bad' (via 'app.bad.x.go')",
        ),
    ]


# ---------------------------------------------------------------------------
# part (b): the strict census
# ---------------------------------------------------------------------------


def test_the_strict_part_anchors_a_package_on_its_init_when_it_has_one(boundary_corpus):
    # `zed` has both __init__.py and aaa.py; the __init__ wins over the
    # path-sorted-first module.
    assert UNDECLARED_ZED in _part_findings(1, boundary_corpus)


def test_the_strict_part_falls_back_to_the_path_sorted_first_module(boundary_corpus):
    # `util` has alpha.py and beta.py and no __init__.py.
    assert UNDECLARED_UTIL in _part_findings(1, boundary_corpus)


def test_the_strict_part_reports_one_finding_per_package_not_per_file(boundary_corpus):
    reported = _part_findings(1, boundary_corpus)
    assert reported == [UNDECLARED_UTIL, UNDECLARED_ZED]
    assert "app/util/beta.py" not in {f.path for f in reported}
    assert "app/zed/aaa.py" not in {f.path for f in reported}


def test_without_strict_the_census_reports_nothing(boundary_corpus):
    options = {**OPTIONS, "strict": False}
    assert _part_findings(1, boundary_corpus, options) == []


# `proj/dup.py` and `proj/dup/__init__.py` are two indexed files with ONE module
# id: paths.module_path_from collapses a package's __init__ onto the package
# name. A multi-root `src` (["src", "lib"]) collides the same way. The census is
# per package, so the collision must not multiply the finding.
COLLIDING_FILES = {
    "proj/__init__.py": '"""Root."""\n',
    "proj/dup.py": '"""Dup, the module."""\n',
    "proj/dup/__init__.py": '"""Dup, the package."""\n',
    "proj/other.py": '"""Other."""\n',
}

COLLIDING_OPTIONS = {"root": "proj", "strict": True}


@pytest.fixture
def colliding_corpus(indexed_project):
    _, store = indexed_project(COLLIDING_FILES)
    return Corpus(store, ("proj",))


def test_two_files_sharing_one_module_id_still_report_the_package_once(colliding_corpus):
    # Guard first: if the binder ever stopped collapsing `dup/__init__.py` onto
    # `dup`, there would be no collision and the assertion below would pass
    # while proving nothing.
    assert _module_ids(colliding_corpus)["proj.dup"] == [
        "proj/dup.py",
        "proj/dup/__init__.py",
    ]
    # The frozen engine emits exactly these two, verified end to end against
    # `pypeeker check --strict` on a materialized copy of this tree. A census
    # keyed on the representative's module id and quantified over the per-file
    # modules universe emitted a third — `proj/dup.py:1`, a file the
    # representative election explicitly passed over.
    assert _part_findings(1, colliding_corpus, COLLIDING_OPTIONS) == [
        _finding(
            "proj/dup/__init__.py",
            1,
            "package 'dup' is not declared in import-boundaries (add it to "
            "[tool.pypeeker.import-boundaries.allow] or to the 'unconstrained' list)",
        ),
        _finding(
            "proj/other.py",
            1,
            "package 'other' is not declared in import-boundaries (add it to "
            "[tool.pypeeker.import-boundaries.allow] or to the 'unconstrained' list)",
        ),
    ]


def _module_ids(corpus) -> dict[str, list[str]]:
    """Indexed file paths grouped by the module id their MODULE symbol carries."""
    from pypeeker.models import SymbolKind

    grouped: dict[str, list[str]] = {}
    for index in corpus.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE), None
        )
        if module_id is not None:
            grouped.setdefault(module_id, []).append(index.file_path)
    return {key: sorted(value) for key, value in grouped.items()}


def test_a_dunder_prefixed_unit_is_rejected_by_the_rule_not_hidden_by_the_sweep(
    indexed_project,
):
    # `__main__` is not a layered unit. The frozen rule drops it while building
    # the census; here the census carries it and the rule's first clause rejects
    # it, so the reason it is silent is readable in the expression.
    _, store = indexed_project({
        "proj/__init__.py": '"""Root."""\n',
        "proj/__main__.py": '"""Entry point."""\n',
        "proj/real.py": '"""Real."""\n',
    })
    corpus = Corpus(store, ("proj",))
    units = [f.message.split("'")[1] for f in _part_findings(1, corpus, COLLIDING_OPTIONS)]
    assert units == ["real"]
    census = sweeps._boundary_tables(corpus, sweeps.boundary_params(COLLIDING_OPTIONS)).units
    assert sorted(entry.unit for entry in census) == ["__main__", "real"]


# ---------------------------------------------------------------------------
# part (c): the configuration quantification
# ---------------------------------------------------------------------------


def test_the_allowance_part_reports_only_unexercised_pairs_at_pyproject(boundary_corpus):
    # api -> core is exercised by handler.py:5 and stays silent; api -> ghost
    # and db -> core never happen. `core = []` contributes no rows at all.
    assert _part_findings(2, boundary_corpus) == [UNUSED_GHOST, UNUSED_DB_CORE]


def test_report_unused_allowances_off_switches_the_part_off_rather_than_filtering_it(
    boundary_corpus,
):
    options = {**OPTIONS, "report-unused-allowances": False}
    assert _part(2, options) is None
    assert _rule().findings(options, boundary_corpus) == [
        LAUNDERED,
        DIRECT,
        DYNAMIC,
        CORE_BARREL,
        CORE_ENGINE,
        UNDECLARED_UTIL,
        UNDECLARED_ZED,
    ]


# ---------------------------------------------------------------------------
# configuration boundaries and derived reach
# ---------------------------------------------------------------------------


def test_no_allow_and_no_strict_is_a_no_op(boundary_corpus):
    # The frozen rule returns [] before touching the resolver; here the sweep
    # returns empty tables and every part quantifies over nothing.
    assert _rule().findings({"root": "app"}, boundary_corpus) == []
    assert _rule().findings({}, boundary_corpus) == []


def test_an_allow_table_alone_still_polices_imports(boundary_corpus):
    options = {"root": "app", "allow": {"core": []}}
    assert _rule().findings(options, boundary_corpus) == [CORE_BARREL, CORE_ENGINE]


def test_every_part_declares_project_reach_by_derivation():
    # All three parts quantify over a fact_source, which is handed the whole
    # corpus by construction — so their reach is PROJECT before a single stage
    # runs, derived from what the code actually reads rather than asserted
    # beside it.
    assert _part(0).reach is Reach.PROJECT
    assert _part(1).reach is Reach.PROJECT
    assert _part(2).reach is Reach.PROJECT


def test_all_three_parts_read_one_sweep(boundary_corpus, monkeypatch):
    # The whole reason Corpus.memo exists: the judged-import table, the strict
    # census and the allowance census are three products of one pass over every
    # index, read through three row sources.
    calls: list[object] = []
    original = sweeps._boundary_sweep

    def counted(corpus, params):
        calls.append(params)
        return original(corpus, params)

    monkeypatch.setattr(sweeps, "_boundary_sweep", counted)
    assert len(_rule().findings(OPTIONS, boundary_corpus)) == 9
    assert len(calls) == 1


def test_option_order_does_not_split_the_memo():
    # Two spellings of one configuration must produce one memo key, or the
    # sweep runs twice for no reason.
    first = sweeps.boundary_params({
        "allow": {"api": ["core", "ghost"], "db": ["core"]},
        "unconstrained": ["cli", "web"],
    })
    second = sweeps.boundary_params({
        "allow": {"db": ["core"], "api": ["ghost", "core", "ghost"]},
        "unconstrained": ["web", "cli"],
    })
    assert first == second
    assert hash(first) == hash(second)


def test_a_stageless_selection_over_a_fact_source_is_already_project_reach():
    # The row source's own reach is a contribution, not a stage: strip every
    # stage off part (c) and the selection is still PROJECT, because producing
    # its rows needs the whole corpus. A model universe keeps reporting FILE
    # under the same rule, so nothing else moved.
    from pypeeker.dsl import Selection, symbols
    from pypeeker.dsl.sweeps import allowance_rows

    assert Selection(allowance_rows(sweeps.boundary_params(OPTIONS))).reach is Reach.PROJECT
    assert symbols().reach is Reach.FILE
