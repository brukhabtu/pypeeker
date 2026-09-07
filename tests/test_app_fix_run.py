"""``app/fix_run.py``, the ``check --fix`` service.

Wherever the frozen ``app/check_fixes.py`` had a counterpart, its report on the
same fixture was captured before it was deleted and is recorded here as a
literal — so these assertions still say what the old service said, not merely
what the new one says. The ``--fix`` report is JSON the CLI serializes
verbatim, so both the entries and their key order are contract.

One divergence is asserted rather than papered over: ``unused-public-symbol``'s
repair id. The frozen rule spelled it ``unused-symbol:delete:<sid>`` — naming a
rule that does not exist — and fork #5's purely-derived id spells it
``unused-public-symbol:delete:<sid>``. That is the one of the five frozen fix
ids the derivation does not reproduce byte for byte, and ``dsl-rewrite.md``'s
divergence ledger already carries it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar

import pytest

from pypeeker.app import (
    plan_check_fixes,
    plan_intent_fixes,
    run_check,
    scratch_transactions,
)
from pypeeker.app.check_run import CheckRun, _declares_mutation
from pypeeker.app.fix_run import STOP_REASONS, CheckFixApplyError, _collect, _plan_pass
from pypeeker.binder import bind
from pypeeker.dsl import Corpus, Finding, Remediation, dsl_rule, install_expressions
from pypeeker.intents import EMPTY_EFFECT, EMPTY_FOOTPRINT, Intent, ReplaceTextIntent
from pypeeker.models import Confidence, EditEntry, EditOp
from pypeeker.refactor import Materialized, registry as planner_registry
from pypeeker.refactor.registry import register_planner
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

    Not :func:`indexed_project`: the root is an explicit argument, because
    these tests apply repairs to the tree and need to say which tree.
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


FROZEN_OS_FIX = {
    "fix_id": "unused-imports:remove:src.mod:os",
    "description": "remove the unused import 'os'",
    "violation": "src/mod.py:1: [unused-imports] import 'os' is unused in this module",
}
FROZEN_SYS_CONFLICT = {
    "fix_id": "unused-imports:remove:src.mod:sys",
    "description": "remove the unused import 'sys'",
    "violation": "src/mod.py:1: [unused-imports] import 'sys' is unused in this module",
}
"""What the frozen service reported on ``CONFLICT_SOURCE``, captured before it died.

Two repairs target the same import statement, so the second loses the
footprint race and lands in ``skipped_conflicts``. Key order is part of the
record: the CLI serializes these entries and a reordered key is a
byte-different ``--fix`` report.
"""


def _new(store: IndexStore, root: Path, **kwargs) -> Any:
    """The new service's answer over ``store``."""
    return plan_check_fixes(store, TransactionStore(root), run_check(store, root), **kwargs)


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

        new = _new(store, tmp_path, plan_only=True)

        assert new.fixes == [FROZEN_OS_FIX]
        assert new.skipped_conflicts == [FROZEN_SYS_CONFLICT]
        assert new.declined == []
        # Not just equal dicts: equal *key order*, because the CLI serializes
        # these and a reordered key is a byte-different `--fix` report.
        assert _shape(new.fixes) == [["fix_id", "description", "violation"]]
        assert _shape(new.skipped_conflicts) == [
            ["fix_id", "description", "violation"]
        ]

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
        assert outcome.residual == run_check(store, tmp_path).findings

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
        run = run_check(store, tmp_path)
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

        new = _new(store, tmp_path, plan_only=True)

        assert new.fixes == [] and new.tx_id is None
        assert _shape(new.declined) == [["fix_id", "reason", "detail"]] * len(new.declined)
        # What the frozen service declined here, captured before it died.
        assert new.declined == [
            {
                "fix_id": "unused-imports:remove:src.mod:os",
                "reason": "stale-index",
                "detail": (
                    "src/mod.py changed since it was indexed; re-index and re-plan"
                ),
            },
            {
                "fix_id": "unused-imports:remove:src.mod:sys",
                "reason": "stale-index",
                "detail": (
                    "src/mod.py changed since it was indexed; re-index and re-plan"
                ),
            },
        ]


# ── (d) the apply path ──────────────────────────────────────────────────────


class TestApply:
    """The transaction is applied, files are re-indexed, residual is fresh."""

    def test_apply_writes_the_tree_and_recomputes_the_residual(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )
        run = run_check(store, tmp_path)

        outcome = plan_check_fixes(store, TransactionStore(tmp_path), run)

        assert outcome.tx_id is not None
        assert outcome.apply_result is not None
        assert (tmp_path / "src/mod.py").read_text() == "import sys\n\nX = 1\n"
        # A FRESH whole-engine run, not the input: `os` is repaired and gone.
        assert len(run.findings) == 2
        assert [str(finding) for finding in outcome.residual] == [
            "src/mod.py:1: [unused-imports] import 'sys' is unused in this module"
        ]

    def test_the_applier_result_is_kept_whole_not_collapsed_to_a_bool(
        self, tmp_path, fix_project
    ):
        """Ported from ``tests/test_app_check_fixes.py`` when it was retired.

        The apply result is retained rather than reduced to "it worked", so a
        caller can report ``files_reindex_failed`` instead of swallowing it.
        The sibling above only asserts the result is not ``None``; this one
        pins the two keys the CLI actually reads out of it.
        """
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        outcome = _new(store, tmp_path)

        assert outcome.apply_result is not None
        assert outcome.apply_result["files_modified"] == ["src/mod.py"]
        assert outcome.apply_result["files_reindex_failed"] == []

    def test_apply_matches_the_frozen_service(self, tmp_path, fix_project):
        """Same report, same residual, same bytes on disk as the frozen service."""
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        new = _new(store, tmp_path)

        assert new.fixes == [FROZEN_OS_FIX]
        assert new.skipped_conflicts == [FROZEN_SYS_CONFLICT]
        assert [str(f) for f in new.residual] == [
            "src/mod.py:1: [unused-imports] import 'sys' is unused in this module"
        ]
        assert (tmp_path / "src/mod.py").read_text() == "import sys\n\nX = 1\n"


# ── (e)/(f) the bounded fixpoint ────────────────────────────────────────────


@dataclass
class _ScriptedRule:
    """A rule whose repairs a test writes by hand.

    The DSL twin of ``tests/test_check_fix_until_clean.py``'s ``custom_rule``:
    the pathological terminations (a repair that never sticks, a pair that undo
    each other) cannot be produced by any real rule, and a fixpoint whose
    guards are untested is a loop with no contract. ``mutation`` is a non-None
    sentinel so :func:`~pypeeker.app.check_run._declares_mutation` keeps the
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


def _scripted_run(rule: _ScriptedRule) -> CheckRun:
    """A run record carrying one scripted rule and no findings of its own."""
    return CheckRun(findings=[], src=("src",), rules=(("scripted", rule),), options={})


class TestFixpoint:
    """Every ``stop_reason`` is reachable, and the loop always reports one."""

    def test_the_cascade_reaches_quiescence_and_matches_the_frozen_loop(
        self, tmp_path, fix_project
    ):
        config = _config(
            ["unused-imports", "unused-public-symbol"],
            extra="[tool.pypeeker.unused-public-symbol]\nalso-private = true\n",
        )
        store = fix_project(tmp_path, {"src/mod.py": CASCADE_SOURCE}, config)

        new = _new(store, tmp_path, max_iterations=10)

        assert new.stop_reason == "quiescent"
        assert new.quiescent is True
        assert new.iterations_run == 3
        assert new.reverted == []
        # The frozen loop's per-iteration record, captured before it died.
        assert new.iterations == [
            {
                "iteration": 1,
                "fixes": 1,
                "reverted": 0,
                "skipped_conflicts": 0,
                "declined": 0,
            },
            {
                "iteration": 2,
                "fixes": 1,
                "reverted": 0,
                "skipped_conflicts": 0,
                "declined": 0,
            },
            {
                "iteration": 3,
                "fixes": 0,
                "reverted": 0,
                "skipped_conflicts": 0,
                "declined": 0,
            },
        ]
        assert (tmp_path / "src/mod.py").read_text() == "\n\n"
        # Same repairs, same order, same wording as the frozen loop — except the
        # ONE ledgered fix id: the frozen `unused-public-symbol` remedy was
        # spelled `unused-symbol:delete:<sid>`, naming a rule that does not
        # exist, where fork #5 derives `<rule>:<mutation>:<anchor>`.
        assert new.fixes == [
            {
                "fix_id": "unused-public-symbol:delete:src.mod:_dead",
                "description": "delete the unreferenced definition of '_dead'",
                "violation": (
                    "src/mod.py:4: [unused-public-symbol] protected function "
                    "'src.mod:_dead' has no references in the project"
                ),
                "iteration": 1,
            },
            {
                "fix_id": "unused-imports:remove:mod:os",
                "description": "remove the unused import 'os'",
                "violation": (
                    "src/mod.py:1: [unused-imports] import 'os' is unused in "
                    "this module"
                ),
                "iteration": 2,
            },
        ]

    def test_the_conflict_cascade_is_report_identical_to_the_frozen_loop(
        self, tmp_path, fix_project
    ):
        store = fix_project(
            tmp_path, {"src/mod.py": CONFLICT_SOURCE}, _config(["unused-imports"])
        )

        new = _new(store, tmp_path, max_iterations=10)

        # The frozen loop's report on the same fixture, captured before it died:
        # the conflict loser of pass 1 is re-planned and applied in pass 2.
        assert new.fixes == [
            dict(FROZEN_OS_FIX, iteration=1),
            {
                "fix_id": "unused-imports:remove:mod:sys",
                "description": "remove the unused import 'sys'",
                "violation": (
                    "src/mod.py:1: [unused-imports] import 'sys' is unused in "
                    "this module"
                ),
                "iteration": 2,
            },
        ]
        assert new.skipped_conflicts == [FROZEN_SYS_CONFLICT]
        assert new.declined == []
        assert new.reverted == []
        assert new.iterations == [
            {
                "iteration": 1,
                "fixes": 1,
                "reverted": 0,
                "skipped_conflicts": 1,
                "declined": 0,
            },
            {
                "iteration": 2,
                "fixes": 1,
                "reverted": 0,
                "skipped_conflicts": 0,
                "declined": 0,
            },
            {
                "iteration": 3,
                "fixes": 0,
                "reverted": 0,
                "skipped_conflicts": 0,
                "declined": 0,
            },
        ]
        assert (tmp_path / "src/mod.py").read_text() == "\nX = 1\n"

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

        outcome = plan_check_fixes(
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

        outcome = plan_check_fixes(
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

        run = run_check(store, tmp_path)

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


# ── (h) the apply failure ───────────────────────────────────────────────────
#
# Ported from ``tests/test_app_check_fixes.py::TestApplyFailure`` when the
# frozen service was deleted. It is the only scenario that reaches
# :class:`~pypeeker.app.fix_run.CheckFixApplyError`, and it needs a
# deliberately broken planner to get there: a real hash mismatch would mean
# racing the filesystem.


@dataclass(frozen=True)
class _BadHashIntent(Intent):
    """A test-only intent whose materializer emits a deliberately wrong hash.

    Used only to force :class:`~pypeeker.refactor.ApplyError` (hash mismatch)
    without racing the real filesystem, so
    :func:`~pypeeker.app.plan_check_fixes` can be observed raising
    :class:`~pypeeker.app.CheckFixApplyError` rather than writing a half-applied
    tree.
    """

    file_path: str = ""

    kind: ClassVar[str] = "test-only:bad-hash"

    def footprint(self, store):
        """File-scoped, like every text-anchored intent."""
        return EMPTY_FOOTPRINT

    def predicted_effect(self, store):
        """Nothing predictable — the batch never gets this far."""
        return EMPTY_EFFECT

    def remap(self, effect):
        """Identity: the anchor never moves."""
        return self


@pytest.fixture
def bad_hash_materializer():
    """Register ``_BadHashIntent``'s materializer for the duration of a test."""

    @register_planner(_BadHashIntent.kind)
    def _materialize(intent, store, tx_store):
        return Materialized(
            edits=[
                EditEntry(
                    op=EditOp.REPLACE,
                    file=intent.file_path,
                    start=0,
                    end=1,
                    old="x",
                    new="y",
                    file_hash="0" * 64,
                )
            ]
        )

    yield
    planner_registry._REGISTRY.pop(_BadHashIntent.kind, None)


class TestApplyFailure:
    """A hash-mismatched plan surfaces as CheckFixApplyError, not a silent write."""

    def test_an_apply_error_is_raised_as_check_fix_apply_error(
        self, tmp_path, fix_project, bad_hash_materializer
    ):
        store = fix_project(tmp_path, {"src/mod.py": "x = 1\n"}, _config([]))
        rule = _ScriptedRule(
            build=lambda corpus: [
                Remediation(
                    finding=Finding(
                        rule="bad-hash",
                        path="src/mod.py",
                        line=1,
                        message="bad hash",
                        confidence=Confidence.DECLARED,
                    ),
                    intent=_BadHashIntent("bad-fix", file_path="src/mod.py"),
                )
            ]
        )

        with pytest.raises(CheckFixApplyError) as excinfo:
            plan_check_fixes(store, TransactionStore(tmp_path), _scripted_run(rule))

        assert excinfo.value.tx_id is not None
        # The file is untouched — the applier rolled back before raising.
        assert (tmp_path / "src/mod.py").read_text() == "x = 1\n"
