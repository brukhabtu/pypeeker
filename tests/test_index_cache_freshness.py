"""The caches behind a fast ``check``, and the proof that none serves a stale answer.

``check --strict`` on a large project used to spend most of its time in three
places, each of them work done again for an answer that had not changed:

* the ``no-impure-functions`` sweep called ``impurities(...)`` once per
  function and every call built the whole call graph from scratch, so a sweep
  over N functions built N graphs;
* every ``SemanticQueryEngine.find_symbol`` listed the index directory on disk,
  so N lookups globbed the same directory N times;
* the same sweep turned each function's symbol id back into its symbol with
  ``find_symbol``, which tests every symbol of every indexed file, so N
  functions cost N passes over the whole corpus.

The first class of tests pins the saving (one graph per sweep, one listing per
state of the store, no scan for a function the sweep's symbol table holds). The
second pins what the saving may never cost: an answer from before a change. A
cache here is only sound if no path that changes the files can be handed the
cached answer afterwards, so each path that holds an engine, a store, an overlay
or a corpus across a change has a test that makes the change between two uses
and asks the second time.

The tests count work (graph builds, directory listings, symbol scans) rather
than time, and both counters look for the work wherever it is imported from, so
moving the implementation does not blind them.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pypeeker.analysis import AnalysisContext, call_graph, impurities
from pypeeker.app import plan_check_fixes
from pypeeker.app.check_run import CheckRun
from pypeeker.app.batch_run import run_intent_batch
from pypeeker.app.privatize import run_privatize
from pypeeker.app.submit import submit_intent
from pypeeker.dsl import Corpus, Finding, Remediation, dsl_rule, install_expressions
from pypeeker.intents import (
    ChangeVisibilityIntent,
    MoveSymbolIntent,
    RenameIntent,
    ReplaceTextIntent,
)
from pypeeker.models import Confidence, SymbolKind
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor import BatchPolicy, TransactionApplier, run_batch
from pypeeker.storage import OverlayIndexStore, TransactionStore
from tests.conftest import run_dsl_rule_on_store

IMPURITY_OPTIONS = {"include": ["*"]}

# Six functions across two files; four of them impure (`leaf_impure` directly,
# the rest by propagation along `three -> one -> middle -> leaf_impure`).
SWEEP_FILES = {
    "lib.py": (
        "def leaf_impure():\n    print('x')\n\n\n"
        "def leaf_pure():\n    return 1\n\n\n"
        "def middle():\n    return leaf_impure()\n"
    ),
    "app.py": (
        "from lib import leaf_pure, middle\n\n\n"
        "def one():\n    return middle()\n\n\n"
        "def two():\n    return leaf_pure()\n\n\n"
        "def three():\n    return one() + two()\n"
    ),
}
SWEEP_IMPURE = {"lib:leaf_impure", "lib:middle", "app:one", "app:three"}
SWEEP_FUNCTIONS = SWEEP_IMPURE | {"lib:leaf_pure", "app:two"}


def _functions(engine: SemanticQueryEngine, name: str) -> list[str]:
    """Ids of the functions a lookup of ``name`` finds (not the modules and imports it also matches)."""
    return sorted(
        symbol.symbol_id
        for symbol in engine.find_symbol(name)
        if symbol.kind is SymbolKind.FUNCTION
    )


def _flagged(findings: list[Finding]) -> set[str]:
    """The symbol ids a ``no-impure-functions`` run reported (the quoted id)."""
    return {finding.message.split("'")[1] for finding in findings}


@pytest.fixture
def graph_builds(monkeypatch) -> list[int]:
    """One entry per call-graph build, wherever the builder was imported from."""
    builds: list[int] = []
    original = call_graph

    def counting(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    for name, module in list(sys.modules.items()):
        if name.startswith("pypeeker") and vars(module).get("call_graph") is original:
            monkeypatch.setattr(module, "call_graph", counting)
    return builds


@pytest.fixture
def index_listings(monkeypatch) -> list[Path]:
    """One entry per directory listing done with ``Path.rglob`` (the store's glob)."""
    listings: list[Path] = []
    original = Path.rglob

    def counting(self, *args, **kwargs):
        listings.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", counting)
    return listings


@pytest.fixture
def symbol_scans(monkeypatch) -> list[str]:
    """One entry per ``find_symbol`` call (a pass over every indexed symbol): the name asked."""
    asked: list[str] = []
    original = SemanticQueryEngine.find_symbol

    def counting(self, name):
        asked.append(name)
        return original(self, name)

    monkeypatch.setattr(SemanticQueryEngine, "find_symbol", counting)
    return asked


# ── the saving ──────────────────────────────────────────────────────────────


class TestTheCallGraphIsBuiltOncePerSweep:
    def test_a_sweep_over_many_functions_builds_one_graph(
        self, indexed_project, graph_builds
    ):
        _, store = indexed_project(SWEEP_FILES)

        findings = run_dsl_rule_on_store("no-impure-functions", store, IMPURITY_OPTIONS)

        assert _flagged(findings) == SWEEP_IMPURE
        assert len(graph_builds) == 1

    def test_a_shared_graph_changes_no_verdict(self, indexed_project):
        _, store = indexed_project(SWEEP_FILES)
        graph = call_graph(store)

        for symbol_id in sorted(SWEEP_FUNCTIONS):
            assert impurities(store, symbol_id, graph=graph) == impurities(
                store, symbol_id
            ), symbol_id

    def test_without_a_graph_each_call_still_builds_its_own(
        self, indexed_project, graph_builds
    ):
        _, store = indexed_project(SWEEP_FILES)

        impurities(store, "app:three")
        impurities(store, "app:one")

        assert len(graph_builds) == 2


class TestTheIndexIsListedOncePerStateOfTheStore:
    def test_many_lookups_on_one_engine_list_the_directory_once(
        self, indexed_project, index_listings
    ):
        _, store = indexed_project(SWEEP_FILES)
        engine = SemanticQueryEngine(store)

        for _ in range(25):
            assert engine.find_symbol("lib:middle")

        assert len(index_listings) == 1

    def test_the_listing_is_sorted_and_the_caller_may_change_its_copy(
        self, indexed_project
    ):
        _, store = indexed_project(SWEEP_FILES)

        first = store.list_indexed_files()
        first.append("scribbled-on.py")
        first.remove("app.py")

        assert store.list_indexed_files() == ["app.py", "lib.py"]


# A function, a method, a nested function, a class and a variable in one file, and ids bound
# more than once: `app.dup.mod:go`, a VARIABLE in `mod.py` (indexed first) and a FUNCTION in
# `mod/__init__.py`, and `app.twin:twin`, a FUNCTION in both (`twin.py` first).
RESOLVE_FILES = {
    "lib.py": (
        "VALUE = 1\n\n\n"
        "def helper():\n    def inner():\n        return VALUE\n    return inner()\n\n\n"
        "class Service:\n    def run(self):\n        return helper()\n"
    ),
    "app/__init__.py": "",
    "app/dup/__init__.py": "",
    "app/dup/mod.py": "go = 1\n",
    "app/dup/mod/__init__.py": "def go():\n    return 2\n",
    "app/twin.py": "def twin():\n    return 1\n",
    "app/twin/__init__.py": "def twin():\n    return 2\n",
    # Three parameters bound to one id (`over:twice:x`) in one file, beside functions that are not.
    "over.py": (
        "from typing import overload\n\n\n"
        "@overload\ndef twice(x: int) -> int: ...\n"
        "@overload\ndef twice(x: str) -> str: ...\n"
        "def twice(x):\n    return x\n"
    ),
}


class TestAFunctionIsResolvedFromItsIdWithoutAScan:
    def test_a_sweep_scans_for_no_function_it_walks(self, indexed_project, symbol_scans):
        _, store = indexed_project(SWEEP_FILES)

        findings = run_dsl_rule_on_store("no-impure-functions", store, IMPURITY_OPTIONS)

        assert _flagged(findings) == SWEEP_IMPURE
        assert symbol_scans == []

    def test_the_table_resolves_every_name_to_what_the_scan_resolves(
        self, indexed_project
    ):
        """Every id, bare name and suffix of the project, and one that names nothing.

        The ids that are not a function's (the module, the class, the variable) and
        the names that are not ids (``helper``, ``Service.run``) are asked too: the
        table answers none of them, and each is the scan's answer, error or not.
        """
        _, store = indexed_project(RESOLVE_FILES)
        engine = SemanticQueryEngine(store)
        table = engine.symbol_table()
        symbols = [s for index in engine.all_indexes() for s in index.symbols]
        names = {s.symbol_id for s in symbols} | {s.name for s in symbols}
        names |= {"Service.run", "lib:Service.run", "inner", "no.such:thing"}
        assert {"lib:helper", "lib:helper.inner", "lib:Service.run", "app.dup.mod:go"} <= names
        assert {"app.twin:twin", "over:twice", "over:twice$2", "over:twice:x"} <= names

        for name in sorted(names):
            assert AnalysisContext.for_function(
                store, name, engine=engine, symbols=table
            ) == AnalysisContext.for_function(store, name, engine=engine), name

    def test_a_duplicated_id_resolves_to_the_function_the_scan_prefers(
        self, indexed_project
    ):
        _, store = indexed_project(RESOLVE_FILES)
        engine = SemanticQueryEngine(store)
        table = engine.symbol_table()

        assert [s.kind for s in table["app.dup.mod:go"]] == [
            SymbolKind.VARIABLE,
            SymbolKind.FUNCTION,
        ]
        context = AnalysisContext.for_function(
            store, "app.dup.mod:go", engine=engine, symbols=table
        )
        assert isinstance(context, AnalysisContext)
        assert context.function_symbol.location.file_path == "app/dup/mod/__init__.py"

    def test_an_id_two_functions_carry_resolves_to_the_first_in_indexed_order(
        self, indexed_project
    ):
        _, store = indexed_project(RESOLVE_FILES)
        engine = SemanticQueryEngine(store)

        context = AnalysisContext.for_function(
            store, "app.twin:twin", engine=engine, symbols=engine.symbol_table()
        )

        assert isinstance(context, AnalysisContext)
        assert context.function_symbol.location.file_path == "app/twin.py"

    def test_an_id_that_is_the_tail_of_an_earlier_functions_id_names_its_own_function(
        self, indexed_project
    ):
        """``b:f`` is also the dotted suffix of ``a.b:f``, which the scan finds first.

        ``find_symbol("b:f")`` returns both (the dotted-suffix match is
        deliberate), and the scan took the first function, so the walk asked about
        ``b:f`` analysed ``a.b:f``'s body. A full id names the symbol that carries
        it.
        """
        files = {
            "a/b.py": "def f():\n    print('x')\n",
            "b.py": "def f():\n    return 1\n",
        }
        _, store = indexed_project(files)
        engine = SemanticQueryEngine(store)
        assert _functions(engine, "b:f") == ["a.b:f", "b:f"]

        context = AnalysisContext.for_function(
            store, "b:f", engine=engine, symbols=engine.symbol_table()
        )

        assert isinstance(context, AnalysisContext)
        assert context.function_symbol.symbol_id == "b:f"
        findings = run_dsl_rule_on_store("no-impure-functions", store, IMPURITY_OPTIONS)
        assert _flagged(findings) == {"a.b:f"}

    def test_a_table_gives_every_symbol_under_its_id_in_the_order_find_symbol_does(
        self, indexed_project
    ):
        _, store = indexed_project(RESOLVE_FILES)
        engine = SemanticQueryEngine(store)

        table = engine.symbol_table()

        for symbol_id, symbols in table.items():
            assert list(symbols) == [
                s for s in engine.find_symbol(symbol_id) if s.symbol_id == symbol_id
            ], symbol_id
        every = [s for index in engine.all_indexes() for s in index.symbols]
        assert sum(len(symbols) for symbols in table.values()) == len(every)


# ── what the saving may not cost: a change between two uses is seen ─────────


class TestAChangeBetweenTwoUsesIsSeen:
    """Each test changes the files after something was already asked once."""

    def test_an_engine_sees_files_saved_and_removed_after_it_was_built(
        self, indexed_project, bind_source
    ):
        _, store = indexed_project(SWEEP_FILES)
        engine = SemanticQueryEngine(store)
        assert _functions(engine, "late_fn") == []
        assert store.list_indexed_files() == ["app.py", "lib.py"]

        store.save(bind_source("def late_fn():\n    return 1\n", "late.py"))

        assert _functions(engine, "late_fn") == ["late:late_fn"]
        assert store.list_indexed_files() == ["app.py", "late.py", "lib.py"]

        store.remove("late.py")

        assert _functions(engine, "late_fn") == []
        assert store.list_indexed_files() == ["app.py", "lib.py"]

    def test_a_resave_of_a_known_file_replaces_what_the_engine_reads(
        self, indexed_project, bind_source
    ):
        _, store = indexed_project(SWEEP_FILES)
        engine = SemanticQueryEngine(store)
        assert _functions(engine, "leaf_pure") == ["lib:leaf_pure"]

        store.save(bind_source("def renamed():\n    return 1\n", "lib.py"))

        assert _functions(engine, "leaf_pure") == []
        assert _functions(engine, "renamed") == ["lib:renamed"]

    def test_a_save_after_the_symbol_table_was_taken_is_seen(
        self, indexed_project, bind_source
    ):
        """The table is a snapshot the caller owns; the engine it came from stays live."""
        _, store = indexed_project(SWEEP_FILES)
        engine = SemanticQueryEngine(store)
        table = engine.symbol_table()
        assert "late:late_fn" not in table

        store.save(bind_source("def late_fn():\n    return 1\n", "late.py"))

        assert _functions(engine, "late_fn") == ["late:late_fn"]
        assert "late:late_fn" not in table
        assert "late:late_fn" in engine.symbol_table()
        # A function the older table does not hold is asked of the engine, which sees it.
        context = AnalysisContext.for_function(
            store, "late:late_fn", engine=engine, symbols=table
        )
        assert isinstance(context, AnalysisContext)
        assert context.function_symbol.symbol_id == "late:late_fn"

    def test_a_sweep_on_a_new_corpus_sees_what_was_saved_since_the_last_one(
        self, indexed_project, bind_source
    ):
        _, store = indexed_project(SWEEP_FILES)
        assert _flagged(
            run_dsl_rule_on_store("no-impure-functions", store, IMPURITY_OPTIONS)
        ) == SWEEP_IMPURE

        store.save(bind_source("def late_fn():\n    print('x')\n", "late.py"))
        store.save(
            bind_source(
                "def leaf_impure():\n    print('x')\n\n\n"
                "def leaf_pure():\n    return 1\n\n\n"
                "def middle():\n    return 1\n",
                "lib.py",
            )
        )

        # `middle` no longer calls the impure leaf, so `one` and `three` are pure again.
        assert _flagged(
            run_dsl_rule_on_store("no-impure-functions", store, IMPURITY_OPTIONS)
        ) == {"lib:leaf_impure", "late:late_fn"}

    def test_an_engine_sees_what_the_applier_reindexes_into_the_store(
        self, indexed_project
    ):
        """The refactor planners' path: plan, apply, ask the engine held since before.

        The move creates a file, so the listing as well as a file's contents
        changes underneath the engine.
        """
        project, store = indexed_project(
            {
                "pkg/__init__.py": "",
                "pkg/lib.py": "def helper(value):\n    return value + 1\n",
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
            }
        )
        tx_store = TransactionStore(project)
        engine = SemanticQueryEngine(store)
        assert _functions(engine, "helper") == ["pkg.lib:helper"]

        planned = submit_intent(
            MoveSymbolIntent("move", "pkg.lib:helper", "pkg.util"), store, tx_store
        )
        assert planned.summary is not None
        assert _functions(engine, "helper") == ["pkg.lib:helper"]  # planned, not applied

        TransactionApplier(store, tx_store).apply(planned.summary.tx_id)

        assert "pkg/util.py" in store.list_indexed_files()
        assert _functions(engine, "helper") == ["pkg.util:helper"]

    def test_demote_then_ask_again_sees_the_new_name(self, indexed_project):
        project, store = indexed_project({"mod.py": "def helper():\n    return 1\n"})
        tx_store = TransactionStore(project)
        engine = SemanticQueryEngine(store)
        assert _functions(engine, "helper") == ["mod:helper"]

        planned = submit_intent(
            ChangeVisibilityIntent("demote", "mod:helper", "demote"), store, tx_store
        )
        assert planned.summary is not None
        TransactionApplier(store, tx_store).apply(planned.summary.tx_id)

        assert _functions(engine, "helper") == []
        assert _functions(engine, "_helper") == ["mod:_helper"]

    def test_privatize_run_twice_on_one_store_sees_its_own_first_result(
        self, indexed_project
    ):
        project, store = indexed_project({"src/dead.py": "def orphan():\n    return 1\n"})
        tx_store = TransactionStore(project)

        first = run_privatize(store, tx_store, project).outcome
        assert [(e.symbol_id, e.new_name) for e in first.executed] == [
            ("src.dead:orphan", "_orphan")
        ]
        assert first.summary is not None
        TransactionApplier(store, tx_store).apply(first.summary.tx_id)

        second = run_privatize(store, tx_store, project).outcome

        assert second.executed == []
        assert second.skipped == []
        engine = SemanticQueryEngine(store)
        assert _functions(engine, "orphan") == []
        assert len(_functions(engine, "_orphan")) == 1

    def test_an_engine_over_an_overlay_sees_the_overlay_and_a_change_to_its_base(
        self, indexed_project, bind_source
    ):
        _, store = indexed_project(SWEEP_FILES)
        overlay = OverlayIndexStore(store)
        engine = SemanticQueryEngine(overlay)
        assert _functions(engine, "added_fn") == []

        overlay.save(bind_source("def added_fn():\n    return 1\n", "added.py"))
        assert _functions(engine, "added_fn") == ["added:added_fn"]

        overlay.remove("lib.py")
        assert _functions(engine, "leaf_pure") == []
        assert overlay.list_indexed_files() == ["added.py", "app.py"]

        # The base changes underneath a live overlay (a real apply after a
        # simulation): the overlay's next listing reflects it.
        store.save(bind_source("def late_fn():\n    return 1\n", "late.py"))
        assert _functions(engine, "late_fn") == ["late:late_fn"]
        assert overlay.list_indexed_files() == ["added.py", "app.py", "late.py"]
        assert store.list_indexed_files() == ["app.py", "late.py", "lib.py"]

    def test_a_second_batch_on_one_store_sees_the_files_the_first_one_applied(
        self, indexed_project
    ):
        """The ``batch`` command's service, run twice against a store that changed between."""
        project, store = indexed_project(
            {
                "pkg/__init__.py": "",
                "pkg/lib.py": "def helper(value):\n    return value + 1\n",
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
            }
        )
        tx_store = TransactionStore(project)
        first = run_intent_batch(
            [MoveSymbolIntent("move", "pkg.lib:helper", "pkg.util")],
            store,
            tx_store,
            policy=BatchPolicy.ALL_OR_NOTHING,
        )
        assert first["files_created"] == ["pkg/util.py"]
        TransactionApplier(store, tx_store).apply(first["tx_id"])

        second = run_intent_batch(
            [RenameIntent("rename", "pkg.util:helper", "assist")],
            store,
            tx_store,
            policy=BatchPolicy.ALL_OR_NOTHING,
        )

        assert [entry["id"] for entry in second["executed"]] == ["rename"]
        assert second["dropped"] == []

    def test_a_batch_whose_second_intent_needs_the_first_intents_new_file(
        self, indexed_project, tmp_path
    ):
        """``run_batch`` re-plans on one overlay that the first intent just grew a file in.

        The move carries ``helper`` and its ``other`` import into a file that
        does not exist yet; the rename of ``other`` then has to find and
        rewrite that file's import and call, so it can only be right if the
        overlay lists the file the move created.
        """
        _, store = indexed_project(
            {
                "pkg/__init__.py": "",
                "pkg/base.py": "def other():\n    return 2\n",
                "pkg/lib.py": (
                    "from pkg.base import other\n\n\n"
                    "def helper(value):\n    return value + other()\n"
                ),
                "app.py": "from pkg.lib import helper\n\n\ndef run():\n    return helper(1)\n",
            }
        )

        result = run_batch(
            [
                MoveSymbolIntent("move", "pkg.lib:helper", "pkg.util"),
                RenameIntent("rename", "pkg.base:other", "renamed_other", deps={"move"}),
            ],
            store,
            tx_store=TransactionStore(tmp_path / "scratch"),
            policy=BatchPolicy.ALL_OR_NOTHING,
        )

        assert [e.intent.intent_id for e in result.executed] == ["move", "rename"]
        assert "pkg/util.py" not in store.list_indexed_files()
        simulated = result.store.read_file("pkg/util.py").decode("utf-8")
        assert "from pkg.base import renamed_other" in simulated
        assert "return value + renamed_other()" in simulated


# ── check --fix and --fix-until-clean ───────────────────────────────────────


CUT_FINDING = Finding(
    rule="cut-the-call",
    path="mod.py",
    line=6,
    message="middle calls leaf",
    confidence=Confidence.DECLARED,
)
"""What the scripted repair answers, and so what the run record carries.

A run that found nothing has no repair to collect, so ``plan_check_fixes``
does not look; the record has to name the finding its rule reports.
"""


@dataclass
class _CutTheCall:
    """A scripted repair that cuts the one call edge the impurity sweep follows.

    ``build`` looks at the tree *this corpus was built over* (so a fixpoint round
    sees the previous round's edit), records which functions the real
    ``no-impure-functions`` sweep flags on that same corpus, and proposes the
    repair only while the call is still there. ``mutation`` is a non-None
    sentinel so the fixpoint keeps the rule in its narrowed loop.
    """

    rounds: list[set[str]] = field(default_factory=list)
    mutation: object = field(default_factory=object)

    def _build(self, corpus: Corpus) -> list[Remediation]:
        sweep = dsl_rule("no-impure-functions").findings(IMPURITY_OPTIONS, corpus)
        self.rounds.append(_flagged(sweep))
        source = corpus.store.read_file("mod.py").decode("utf-8")
        if "return leaf()" not in source:
            return []
        return [
            Remediation(
                finding=CUT_FINDING,
                intent=ReplaceTextIntent(
                    "cut-the-call:middle", "mod.py", 0, 0, "return leaf()", "return 1"
                ),
            )
        ]

    def findings(self, options, corpus: Corpus) -> list[Finding]:
        """What the repair answers (and, as a side effect, one more sweep record)."""
        return [remediation.finding for remediation in self._build(corpus)]

    def remediations(self, options, corpus: Corpus) -> list[Remediation]:
        """The repairs themselves."""
        return self._build(corpus)


CUT_SOURCE = (
    "def leaf():\n    print('x')\n\n\n"
    "def middle():\n    return leaf()\n\n\n"
    "def top():\n    return middle()\n"
)


class TestCheckFixSeesItsOwnEdit:
    """The impurity sweep after an edit that removes a call edge.

    ``middle`` and ``top`` are impure only through the edge ``middle -> leaf``.
    The repair cuts that edge, so a call graph (or file list) kept from before
    the edit would go on reporting both. ``leaf`` keeps its ``print`` and stays
    flagged: the proof is the *difference* between the sweep before and after.
    """

    @pytest.mark.parametrize("max_iterations", [1, 5], ids=["single-pass", "until-clean"])
    def test_the_sweep_after_the_edit_does_not_see_the_cut_edge(
        self, indexed_project, max_iterations
    ):
        project, store = indexed_project({"mod.py": CUT_SOURCE})
        (project / "pyproject.toml").write_text('[project]\nname = "test"\n')
        install_expressions()
        scripted = _CutTheCall()
        run = CheckRun(
            findings=[CUT_FINDING],
            src=(),
            rules=(
                ("cut-the-call", scripted),
                ("no-impure-functions", dsl_rule("no-impure-functions")),
            ),
            options={"no-impure-functions": IMPURITY_OPTIONS},
        )
        before = {"mod:leaf", "mod:middle", "mod:top"}

        outcome = plan_check_fixes(
            store, TransactionStore(project), run, max_iterations=max_iterations
        )

        assert [entry["fix_id"] for entry in outcome.fixes] == ["cut-the-call:middle"]
        assert "return 1" in (project / "mod.py").read_text()
        assert scripted.rounds[0] == before
        # Every sweep run after the edit (the next fixpoint round, then the
        # residual run over the real store) sees `leaf` alone.
        assert scripted.rounds[1:], "no sweep ran after the edit"
        assert all(flagged == {"mod:leaf"} for flagged in scripted.rounds[1:])
        assert _flagged(outcome.residual) == {"mod:leaf"}

