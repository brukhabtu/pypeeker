"""The new engine's ``check --fix`` service, graded against the frozen one.

``app/fix_run.py`` is what ``check --fix`` will call after the flip;
``app/check_fixes.py`` is what it calls today. Nothing is rewired yet, so these
tests drive the new service directly — and wherever the frozen service has a
counterpart they run **both over the same fixture in the same test**. That
comparison is only possible while both engines exist, which is now: phase B
deletes the frozen half and every ``apply_check_fixes(...)`` call below with
it, leaving the assertions about the new service standing on their own.

One divergence is asserted rather than papered over: ``unused-public-symbol``'s
repair id. The frozen rule spells it ``unused-symbol:delete:<sid>`` — naming a
rule that does not exist — and fork #5's purely-derived id spells it
``unused-public-symbol:delete:<sid>``. That is the one of the five frozen fix
ids the derivation does not reproduce byte for byte, and ``dsl-rewrite.md``'s
divergence ledger already carries it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

from pypeeker.app import (
    apply_check_fixes,
    plan_dsl_fixes,
    plan_intent_fixes,
    run_check,
    run_dsl_check,
    scratch_transactions,
)
from pypeeker.app.check_fixes import STOP_REASONS
from pypeeker.app.check_run2 import DslCheckRun, _declares_mutation
from pypeeker.app.fix_run import _collect, _plan_pass
from pypeeker.binder import bind
from pypeeker.dsl import Corpus, Finding, Remediation, dsl_rule, install_expressions
from pypeeker.intents import ReplaceTextIntent
from pypeeker.models import Confidence
from pypeeker.storage import IndexStore, TransactionStore

# Two unused imports on ONE line: their removals overlap byte-wise, so the
# loser is a ``skipped_conflicts`` entry in pass 1 and legitimately plannable
# in pass 2. The fixture both engines are compared on.
CONFLICT_SOURCE = "import os, sys\n\nX = 1\n"

# `import os` is consumed only by the dead `_dead`, so removing the import only
# becomes possible after the deletion lands: the rule cascade.
CASCADE_SOURCE = "import os\n\n\ndef _dead():\n    return os.getcwd()\n"


def _config(rules: list[str], *, extra: str = "") -> str:
    listed = ", ".join(f'"{rule}"' for rule in rules)
    return (
        '[project]\nname = "test"\n'
        f"[tool.pypeeker]\nsrc = [\"src\"]\nrules = [{listed}]\n{extra}"
    )


@pytest.fixture
def fix_project(adapter):
    """Build an indexed project with a ``pyproject.toml`` under a given root.

    Not :func:`indexed_project`: several tests here need **two** roots in one
    test (the frozen service and the new one both apply, and an applied repair
    is not a comparison the two can share a tree for).
    """

    def _setup(root: Path, files: dict[str, str], config: str) -> IndexStore:
        (root / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
        store = IndexStore(root)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            source = content.encode("utf-8")
            store.save(bind(adapter, name, source, adapter.parse(source).root_node))
        (root / "pyproject.toml").write_text(config)
        return store

    return _setup


def _old(store: IndexStore, root: Path, **kwargs) -> Any:
    """The frozen service's answer over ``store``."""
    run = run_check(store, root)
    return apply_check_fixes(
        store, TransactionStore(root), run.engine, run.violations, **kwargs
    )


def _new(store: IndexStore, root: Path, **kwargs) -> Any:
    """The new service's answer over ``store``."""
    return plan_dsl_fixes(store, TransactionStore(root), run_dsl_check(store, root), **kwargs)


def _shape(entries: list[dict]) -> list[list[str]]:
    """Each entry's key order — the report is JSON, so key order is contract."""
    return [list(entry) for entry in entries]


# ── (a) the two services plan the same pass ─────────────────────────────────


class TestSinglePassParity:
    """The default path's report is the frozen one, entry for entry."""

    def test_plan_only_report_matches_the_frozen_service(self, tmp_path, fix_project):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        old = _old(store, tmp_path, plan_only=True)
        new = _new(store, tmp_path, plan_only=True)

        assert new.fixes == old.fixes
        assert new.skipped_conflicts == old.skipped_conflicts
        assert new.declined == old.declined
        # Not just equal dicts: equal *key order*, because the CLI serializes
        # these and a reordered key is a byte-different `--fix` report.
        assert _shape(new.fixes) == _shape(old.fixes) == [["fix_id", "description", "violation"]]
        assert _shape(new.skipped_conflicts) == _shape(old.skipped_conflicts)

    def test_plan_only_writes_the_transaction_and_touches_nothing(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        outcome = _new(store, tmp_path, plan_only=True)

        assert outcome.tx_id is not None
        assert TransactionStore(tmp_path).load(outcome.tx_id) is not None
        assert outcome.apply_result is None
        assert (tmp_path / "src/mod.py").read_text() == CONFLICT_SOURCE
        # Nothing was applied, so the residual is the run's own findings.
        assert outcome.residual == run_dsl_check(store, tmp_path).findings

    def test_the_single_pass_report_carries_no_fixpoint_keys(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        outcome = _new(store, tmp_path, plan_only=True)

        assert outcome.reverted is None
        assert outcome.iterations is None
        assert outcome.iterations_run is None
        assert outcome.quiescent is None
        assert outcome.stop_reason is None

    def test_a_run_with_nothing_to_repair_writes_no_transaction(
        self, tmp_path, fix_project
    ):
        store = fix_project(tmp_path, {"src/mod.py": "X = 1\n"}, _config(["unused-imports"]))

        outcome = _new(store, tmp_path)

        assert outcome.fixes == []
        assert outcome.tx_id is None
        assert outcome.apply_result is None


# ── (b) the pin on the duplicated pass ──────────────────────────────────────


class TestPassDuplicationPin:
    """``_plan_pass`` and ``plan_intent_fixes`` are the same algorithm.

    They are two copies for one segment only — the fixpoint needs the kept
    ``Materialized`` objects that ``IntentFixOutcome`` does not carry — so the
    duplication is pinned here rather than left to drift until someone
    collapses it.
    """

    def test_the_two_copies_agree_modulo_the_violation_key(self, tmp_path, fix_project):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )
        run = run_dsl_check(store, tmp_path)
        remediations = _collect(run.rules, run.options, Corpus(store, run.src))

        fixes, skipped, declined, kept = _plan_pass(store, remediations)
        with scratch_transactions() as scratch:
            outcome = plan_intent_fixes(
                store,
                scratch,
                [remediation.intent for remediation in remediations],
                plan_only=True,
            )

        def _bare(entries: list[dict]) -> list[dict]:
            return [
                {key: value for key, value in entry.items() if key != "violation"}
                for entry in entries
            ]

        assert _bare(fixes) == outcome.fixes
        assert _bare(skipped) == outcome.skipped_conflicts
        assert declined == outcome.declined
        assert len(kept) == len(fixes)


# ── (c) the two refusal buckets ─────────────────────────────────────────────


class TestBuckets:
    """A byte-range overlap is a conflict; a planner refusal is a decline."""

    def test_overlapping_repairs_put_the_loser_in_skipped_conflicts(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        outcome = _new(store, tmp_path, plan_only=True)

        assert [entry["fix_id"] for entry in outcome.fixes] == [
            "unused-imports:remove:src.mod:os"
        ]
        assert [entry["fix_id"] for entry in outcome.skipped_conflicts] == [
            "unused-imports:remove:src.mod:sys"
        ]

    def test_a_stale_anchor_is_declined_with_the_planners_code(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )
        # Rewrite the file WITHOUT re-indexing: the index still describes the
        # old bytes, so every planner's anchor precondition fails.
        (tmp_path / "src/mod.py").write_text("Y = 2\n")

        old = _old(store, tmp_path, plan_only=True)
        new = _new(store, tmp_path, plan_only=True)

        assert new.fixes == [] and new.tx_id is None
        assert _shape(new.declined) == [["fix_id", "reason", "detail"]] * len(new.declined)
        assert [(e["fix_id"], e["reason"]) for e in new.declined] == [
            (e["fix_id"], e["reason"]) for e in old.declined
        ]
        assert new.declined == old.declined


# ── (d) the apply path ──────────────────────────────────────────────────────


class TestApply:
    """The transaction is applied, files are re-indexed, residual is fresh."""

    def test_apply_writes_the_tree_and_recomputes_the_residual(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )
        run = run_dsl_check(store, tmp_path)

        outcome = plan_dsl_fixes(store, TransactionStore(tmp_path), run)

        assert outcome.tx_id is not None
        assert outcome.apply_result is not None
        assert (tmp_path / "src/mod.py").read_text() == "import sys\n\nX = 1\n"
        # A FRESH whole-engine run, not the input: `os` is repaired and gone.
        assert len(run.findings) == 2
        assert [str(finding) for finding in outcome.residual] == [
            "src/mod.py:1: [unused-imports] import 'sys' is unused in this module"
        ]

    def test_apply_matches_the_frozen_service_on_a_parallel_tree(
        self, tmp_path, fix_project
    ):
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        old_store = fix_project(
            old_root, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )
        new_store = fix_project(
            new_root, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        old = _old(old_store, old_root)
        new = _new(new_store, new_root)

        assert new.fixes == old.fixes
        assert new.skipped_conflicts == old.skipped_conflicts
        assert [str(f) for f in new.residual] == [str(v) for v in old.residual]
        assert (new_root / "src/mod.py").read_text() == (
            old_root / "src/mod.py"
        ).read_text()


# ── (e)/(f) the bounded fixpoint ────────────────────────────────────────────


@dataclass
class _ScriptedRule:
    """A rule whose repairs a test writes by hand.

    The DSL twin of ``tests/test_check_fix_until_clean.py``'s ``custom_rule``:
    the pathological terminations (a repair that never sticks, a pair that undo
    each other) cannot be produced by any real rule, and a fixpoint whose
    guards are untested is a loop with no contract. ``mutation`` is a non-None
    sentinel so :func:`~pypeeker.app.check_run2._declares_mutation` keeps the
    rule in the loop's narrowed set.
    """

    build: Callable[[Corpus], list[Remediation]]
    mutation: object = field(default_factory=object)

    def findings(self, options, corpus: Corpus) -> list[Finding]:
        """Every scripted repair's finding."""
        return [remediation.finding for remediation in self.build(corpus)]

    def remediations(self, options, corpus: Corpus) -> list[Remediation]:
        """The scripted repairs themselves."""
        return self.build(corpus)


def _repair(fix_id: str, path: str, old: str, new: str) -> Remediation:
    """One scripted repair: a text replacement, and the finding it answers."""
    return Remediation(
        finding=Finding(
            rule=fix_id.split(":")[0],
            path=path,
            line=1,
            message=f"{old} -> {new}",
            confidence=Confidence.DECLARED,
        ),
        intent=ReplaceTextIntent(fix_id, path, 0, 0, old, new),
    )


def _scripted_run(rule: _ScriptedRule) -> DslCheckRun:
    """A run record carrying one scripted rule and no findings of its own."""
    return DslCheckRun(findings=[], src=("src",), rules=(("scripted", rule),), options={})


class TestFixpoint:
    """Every ``stop_reason`` is reachable, and the loop always reports one."""

    def test_the_cascade_reaches_quiescence_and_matches_the_frozen_loop(
        self, tmp_path, fix_project
    ):
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        config = _config(
            ["unused-imports", "unused-public-symbol"],
            extra="[tool.pypeeker.unused-public-symbol]\nalso-private = true\n",
        )
        old_store = fix_project(old_root, {"src/mod.py": CASCADE_SOURCE}, config)
        new_store = fix_project(new_root, {"src/mod.py": CASCADE_SOURCE}, config)

        old = _old(old_store, old_root, max_iterations=10)
        new = _new(new_store, new_root, max_iterations=10)

        assert new.stop_reason == old.stop_reason == "quiescent"
        assert new.quiescent is True
        assert new.iterations_run == old.iterations_run == 3
        assert new.iterations == old.iterations
        assert new.reverted == old.reverted == []
        assert (new_root / "src/mod.py").read_text() == (
            old_root / "src/mod.py"
        ).read_text()
        # Same repairs, same order, same wording — except the ONE ledgered fix
        # id: the frozen `unused-public-symbol` remedy is spelled
        # `unused-symbol:delete:<sid>`, naming a rule that does not exist,
        # where fork #5 derives `<rule>:<mutation>:<anchor>`.
        assert [entry["fix_id"] for entry in old.fixes] == [
            "unused-symbol:delete:src.mod:_dead",
            "unused-imports:remove:mod:os",
        ]
        assert [entry["fix_id"] for entry in new.fixes] == [
            "unused-public-symbol:delete:src.mod:_dead",
            "unused-imports:remove:mod:os",
        ]
        for old_entry, new_entry in zip(old.fixes, new.fixes, strict=True):
            assert {k: v for k, v in old_entry.items() if k != "fix_id"} == {
                k: v for k, v in new_entry.items() if k != "fix_id"
            }

    def test_the_conflict_cascade_is_report_identical_to_the_frozen_loop(
        self, tmp_path, fix_project
    ):
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        config = _config(["unused-imports"])
        old_store = fix_project(old_root, {"src/mod.py": CONFLICT_SOURCE}, config)
        new_store = fix_project(new_root, {"src/mod.py": CONFLICT_SOURCE}, config)

        old = _old(old_store, old_root, max_iterations=10)
        new = _new(new_store, new_root, max_iterations=10)

        assert new.fixes == old.fixes
        assert new.skipped_conflicts == old.skipped_conflicts
        assert new.declined == old.declined
        assert new.reverted == old.reverted
        assert new.iterations == old.iterations
        assert (new_root / "src/mod.py").read_text() == "\nX = 1\n"

    def test_the_cap_stops_the_cascade_honestly(self, tmp_path, fix_project):
        store = fix_project(
            tmp_path,
            {"src/mod.py": CASCADE_SOURCE},
            _config(
                ["unused-imports", "unused-public-symbol"],
                extra="[tool.pypeeker.unused-public-symbol]\nalso-private = true\n",
            ),
        )

        outcome = _new(store, tmp_path, max_iterations=2)

        assert outcome.stop_reason == "max-iterations"
        assert outcome.quiescent is False
        assert outcome.iterations_run == 2
        assert [entry["iteration"] for entry in outcome.fixes] == [1, 2]

    def test_an_oscillating_rule_stops_with_stop_reason_cycle(
        self, tmp_path, fix_project
    ):
        def _oscillate(corpus: Corpus) -> list[Remediation]:
            source = corpus.store.read_file("src/mod.py").decode("utf-8")
            if "A = 1" in source:
                return [_repair("oscillate:a", "src/mod.py", "A = 1", "B = 1")]
            if "B = 1" in source:
                return [_repair("oscillate:b", "src/mod.py", "B = 1", "A = 1")]
            return []

        store = fix_project(tmp_path, {"src/mod.py": "A = 1\n"}, _config([]))
        run = _scripted_run(_ScriptedRule(_oscillate))

        outcome = plan_dsl_fixes(
            store, TransactionStore(tmp_path), run, max_iterations=10
        )

        assert outcome.stop_reason == "cycle"
        assert outcome.quiescent is False
        # A→B→A nets to nothing, so there is nothing to write and nothing to
        # report as a fix; `reverted` is where the loop's work stays visible.
        assert outcome.fixes == []
        assert outcome.tx_id is None
        assert [entry["fix_id"] for entry in outcome.reverted] == [
            "oscillate:a",
            "oscillate:b",
        ]
        assert (tmp_path / "src/mod.py").read_text() == "A = 1\n"

    def test_a_repair_that_never_sticks_stops_with_stop_reason_repeated_fix(
        self, tmp_path, fix_project
    ):
        def _bump(corpus: Corpus) -> list[Remediation]:
            source = corpus.store.read_file("src/mod.py").decode("utf-8")
            index = int(source.split("def a", 1)[1].split("(", 1)[0])
            return [
                _repair("bump:always", "src/mod.py", f"def a{index}(", f"def a{index + 1}(")
            ]

        store = fix_project(
            tmp_path, {"src/mod.py": "def a0():\n    return 1\n"}, _config([])
        )
        run = _scripted_run(_ScriptedRule(_bump))

        outcome = plan_dsl_fixes(
            store, TransactionStore(tmp_path), run, max_iterations=10
        )

        assert outcome.stop_reason == "repeated-fix"
        assert outcome.quiescent is False
        assert outcome.iterations_run == 2
        # The re-proposal is abandoned, not applied a second time.
        assert [(e["fix_id"], e["iteration"]) for e in outcome.fixes] == [
            ("bump:always", 1)
        ]
        assert outcome.iterations[1]["fixes"] == 0

    @pytest.mark.parametrize("max_iterations", [2, 10])
    def test_fixes_is_non_empty_exactly_when_a_transaction_exists(
        self, tmp_path, fix_project, max_iterations
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        outcome = _new(store, tmp_path, max_iterations=max_iterations)

        assert bool(outcome.fixes) is (outcome.tx_id is not None)
        assert len(outcome.fixes) == sum(row["fixes"] for row in outcome.iterations)
        assert len(outcome.reverted) == sum(
            row["reverted"] for row in outcome.iterations
        )
        assert outcome.stop_reason in STOP_REASONS


# ── (g) the loop's rule narrowing ───────────────────────────────────────────


class TestMutatingRuleNarrowing:
    """The loop re-runs only the rules that can produce a repair."""

    def test_only_the_mutation_declaring_rules_survive_the_narrowing(
        self, tmp_path, fix_project
    ):
        configured = [
            "docstring-drift",
            "prefer-tuple",
            "star-imports",
            "unused-imports",
            "unused-public-symbol",
            "born-private",
            "import-boundaries",
            "no-import-cycles",
        ]
        store = fix_project(tmp_path, {"src/mod.py": "X = 1\n"}, _config(configured))

        run = run_dsl_check(store, tmp_path)

        assert [name for name, _ in run.mutating_rules()] == [
            "docstring-drift",
            "prefer-tuple",
            "star-imports",
            "unused-imports",
            "unused-public-symbol",
        ]

    def test_a_multi_part_rule_is_asked_part_by_part(self):
        install_expressions()
        # `star-imports` is a MultiPartRule: a bare `rule.mutation` raises.
        assert _declares_mutation(dsl_rule("star-imports")) is True
        assert not hasattr(dsl_rule("star-imports"), "mutation")
        assert _declares_mutation(dsl_rule("born-private")) is False
