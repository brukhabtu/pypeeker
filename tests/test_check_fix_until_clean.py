"""Tests for ``check --fix --fix-until-clean``: the bounded fixpoint (TASK-130).

Additive by construction. The default ``check --fix`` path is frozen —
``tests/test_check_fix.py`` and ``tests/test_app_check_fixes.py`` are its
oracles and none of them is touched here — so everything below either drives
the new flag or proves the default path did NOT move.

Three groups of proof:

* **The cascade.** Repairs reveal repairs (deleting dead private code orphans
  the import only it used) and a repair skipped as a byte conflict becomes
  plannable once the winner lands. Plain ``--fix`` half-repairs both; the flag
  finishes them in one command and one transaction.
* **Termination.** The loop has no monotonicity argument, so the guards are
  the contract: every ``stop_reason`` in
  :data:`~pypeeker.app.check_fixes.STOP_REASONS` is reached here by a real
  scenario, including two deliberately pathological test-only rules (an
  oscillator and a repair that never sticks).
* **Safety.** One transaction, rollback to pre-loop bytes, ``--plan`` parity,
  no simulated state written to the user's tree, residual computed by the
  original engine against the real store, and the fail-closed re-bind.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.app import check_fixes as check_fixes_module
from pypeeker.app.check_fixes import STOP_REASONS, apply_check_fixes
from pypeeker.app.submit import SubmitError
from pypeeker.check import CheckConfig, CheckEngine
from pypeeker.check.baseline import BASELINE_FILE
from pypeeker.check.builtin.born_private import BORN_PRIVATE
from pypeeker.check.models import Violation, with_remedy
from pypeeker.check.rules import _REGISTERED, register_rule
from pypeeker.check.simulation import SIMULATION_UNSAFE_RULES
from pypeeker.cli import main
from pypeeker.intents import ReplaceTextIntent
from pypeeker.models.symbols import SymbolKind
from pypeeker.refactor import batch as batch_module
from pypeeker.storage import OverlayIndexStore, TransactionStore

# The motivating cascade: `import os` is consumed only by the dead `_dead`,
# so removing the import only becomes possible after the deletion lands.
CASCADE_SOURCE = "import os\n\n\ndef _dead():\n    return os.getcwd()\n"
CASCADE_RULES = '["unused-imports", "unused-public-symbol"]'
CASCADE_EXTRA = "[tool.pypeeker.unused-public-symbol]\nalso-private = true\n"

# Two unused imports on ONE line: their removals overlap byte-wise, so the
# loser is a `skipped_conflicts` entry in pass 1 and legitimately plannable
# in pass 2 — progress that owes nothing to a rule cascade.
CONFLICT_SOURCE = "import os, sys\n\nX = 1\n"


def _project(
    tmp_path: Path,
    runner: CliRunner,
    files: dict[str, str],
    rules: str,
    extra: str = "",
) -> Path:
    """A tmp project with ``rules`` enabled and ``files`` indexed under src/."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        f"rules = {rules}\n"
        f"{extra}"
    )
    for name, content in files.items():
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    os.chdir(tmp_path)
    result = runner.invoke(
        main, ["index", str(tmp_path / "src")], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return tmp_path


def _run(runner: CliRunner, *args: str) -> tuple[dict, int]:
    """Invoke ``check`` with ``args`` and return its parsed report + exit code."""
    result = runner.invoke(main, ["check", *args], catch_exceptions=False)
    return json.loads(result.output), result.exit_code


def _transactions(project_dir: Path) -> list[str]:
    """The transaction ids on disk, sorted."""
    tx_dir = project_dir / ".pypeeker" / "transactions"
    return sorted(p.stem for p in tx_dir.glob("*.jsonl"))


@pytest.fixture
def custom_rule():
    """Register a per-file rule for one test and unregister it afterwards."""
    registered: list[str] = []

    def _register(name: str, rule):
        register_rule(name)(rule)
        registered.append(name)
        return name

    yield _register
    for name in registered:
        _REGISTERED.pop(name, None)


def _function_named(file_index, prefix: str):
    """The first module-level function whose name starts with ``prefix``."""
    return next(
        (
            s
            for s in file_index.symbols
            if s.kind == SymbolKind.FUNCTION and s.name.startswith(prefix)
        ),
        None,
    )


def _text_remedy(file_index, fix_id: str, old: str, new: str) -> Violation:
    """A DECLARED violation carrying a text-anchored repair."""
    return with_remedy(
        Violation(
            file_path=file_index.file_path,
            line=1,
            rule="test-only",
            message=f"{old} -> {new}",
        ),
        ReplaceTextIntent(fix_id, file_index.file_path, 0, 0, old, new),
    )


class TestTheMotivatingCascade:
    """Plain ``--fix`` half-repairs the cascade; the flag finishes it."""

    def test_plain_fix_leaves_the_revealed_repair_behind(self, tmp_path):
        # Pinned as intended behavior, not as a bug: the default path plans
        # every repair against ONE state, so a finding that only exists after
        # a repair lands is not this run's business.
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, exit_code = _run(runner, "--fix")

        assert [fix["fix_id"] for fix in report["fixes"]] == [
            "unused-symbol:delete:mod:_dead"
        ]
        assert report["residual_violations"] == 1
        assert exit_code == 1
        assert (project / "src" / "mod.py").read_text() == "import os\n\n\n"

    def test_fix_until_clean_finishes_the_cascade_in_one_command(self, tmp_path):
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, exit_code = _run(runner, "--fix", "--fix-until-clean")

        assert [(f["fix_id"], f["iteration"]) for f in report["fixes"]] == [
            ("unused-symbol:delete:mod:_dead", 1),
            ("unused-imports:remove:mod:os", 2),
        ]
        assert report["stop_reason"] == "quiescent"
        assert report["quiescent"] is True
        assert report["residual_violations"] == 0
        assert exit_code == 0
        assert "os" not in (project / "src" / "mod.py").read_text()

    def test_a_conflict_loser_is_applied_by_the_next_iteration(self, tmp_path):
        # The second source of progress: no rule cascade involved, just a
        # repair whose byte range lost pass 1 and wins pass 2. It must move
        # from `skipped_conflicts` to `fixes`, not appear in both.
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CONFLICT_SOURCE}, '["unused-imports"]'
        )

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert [(f["fix_id"], f["iteration"]) for f in report["fixes"]] == [
            ("unused-imports:remove:mod:os", 1),
            ("unused-imports:remove:mod:sys", 2),
        ]
        assert report["skipped_conflicts"] == []
        assert report["iterations"][0]["skipped_conflicts"] == 1
        assert (project / "src" / "mod.py").read_text() == "\nX = 1\n"

    def test_the_whole_run_is_one_transaction_that_rolls_back_to_pre_loop_bytes(
        self, tmp_path
    ):
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert _transactions(project) == [report["tx_id"]]
        shown = runner.invoke(
            main, ["transactions", "show", report["tx_id"]], catch_exceptions=False
        )
        assert json.loads(shown.output)["header"]["operation"] == "check-fix"

        rolled = runner.invoke(
            main, ["rollback", report["tx_id"]], catch_exceptions=False
        )
        assert json.loads(rolled.output)["status"] == "rolled_back"
        assert (project / "src" / "mod.py").read_text() == CASCADE_SOURCE

    def test_plan_writes_pending_touches_nothing_and_applies_the_same_bytes(
        self, tmp_path
    ):
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        planned, _ = _run(runner, "--fix", "--fix-until-clean", "--plan")

        assert "applied" not in planned
        assert planned["stop_reason"] == "quiescent"
        # Nothing touched, and `residual` is the unmodified input set.
        assert (project / "src" / "mod.py").read_text() == CASCADE_SOURCE
        assert planned["residual_violations"] == 1
        pending = json.loads(
            runner.invoke(
                main,
                ["transactions", "show", planned["tx_id"]],
                catch_exceptions=False,
            ).output
        )
        assert pending["header"]["status"] == "pending"

        applied = runner.invoke(
            main, ["apply", planned["tx_id"]], catch_exceptions=False
        )
        assert json.loads(applied.output)["status"] == "applied"
        planned_bytes = (project / "src" / "mod.py").read_text()

        # The same project taken through the applied path lands identically.
        other = CliRunner()
        elsewhere = _project(
            tmp_path / "twin", other, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )
        _run(other, "--fix", "--fix-until-clean")
        assert (elsewhere / "src" / "mod.py").read_text() == planned_bytes

    def test_two_runs_over_identical_input_agree_modulo_tx_id(self, tmp_path):
        reports = []
        for name in ("a", "b"):
            runner = CliRunner()
            _project(
                tmp_path / name, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
                CASCADE_EXTRA,
            )
            report, _ = _run(runner, "--fix", "--fix-until-clean")
            report["tx_id"] = "<tx>"
            reports.append(report)
        assert reports[0] == reports[1]

    def test_no_report_string_leaks_an_absolute_or_temporary_path(self, tmp_path):
        runner = CliRunner()
        _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        blob = json.dumps(report)
        assert "pypeeker-check-fix-" not in blob
        assert str(tmp_path) not in blob
        for fix in report["fixes"]:
            assert fix["violation"].startswith("src/mod.py:")


class TestTerminationGuards:
    """Every ``stop_reason`` is reachable, and the loop always reports one."""

    def test_the_vocabulary_is_exactly_the_four_documented_reasons(self):
        assert set(STOP_REASONS) == {
            "quiescent",
            "max-iterations",
            "repeated-fix",
            "cycle",
        }

    def test_the_cap_stops_the_cascade_honestly(self, tmp_path):
        runner = CliRunner()
        _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, exit_code = _run(
            runner, "--fix", "--fix-until-clean", "--fix-max-iterations", "2"
        )

        assert report["stop_reason"] == "max-iterations"
        assert report["quiescent"] is False
        assert report["iterations_run"] == 2
        # Exactly the repairs the two completed iterations produced.
        assert [f["iteration"] for f in report["fixes"]] == [1, 2]
        assert exit_code == 0

    def test_an_oscillating_rule_stops_with_stop_reason_cycle(
        self, tmp_path, custom_rule
    ):
        # Two repairs that undo each other: A -> B, then B -> A. No finding
        # count and no byte count decreases, so only the tree-hash guard can
        # bound this.
        def _oscillate(file_index, options):
            names = {s.name for s in file_index.symbols}
            if "A" in names:
                return [_text_remedy(file_index, "oscillate:a", "A = 1", "B = 1")]
            if "B" in names:
                return [_text_remedy(file_index, "oscillate:b", "B = 1", "A = 1")]
            return []

        custom_rule("oscillate", _oscillate)
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": "A = 1\n"}, '["oscillate"]'
        )

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert report["stop_reason"] == "cycle"
        assert report["quiescent"] is False
        # A→B→A nets to nothing, so there is nothing to write to the tree —
        # and therefore nothing to report as a fix. Both repairs ran inside
        # the simulation and both were undone there; `reverted` is where the
        # loop's work stays visible without claiming a change on disk.
        assert report["fixes"] == []
        assert [f["fix_id"] for f in report["reverted"]] == [
            "oscillate:a",
            "oscillate:b",
        ]
        assert [f["iteration"] for f in report["reverted"]] == [1, 2]
        assert report["tx_id"] is None
        assert "applied" not in report
        assert (project / "src" / "mod.py").read_text() == "A = 1\n"

    def test_a_net_no_op_run_reports_no_fixes_and_no_transaction(
        self, tmp_path, custom_rule
    ):
        # The invariant a driver branches on, in the one shape that used to
        # break it: `fixes` non-empty <=> `tx_id` non-null. A repair that
        # rewrites bytes with themselves is "applied" by every step of the
        # loop and lands in no transaction at all, so it cannot be a fix.
        def _noop(file_index, options):
            return [_text_remedy(file_index, "noop:same", "A = 1", "A = 1")]

        custom_rule("noop", _noop)
        runner = CliRunner()
        project = _project(tmp_path, runner, {"mod.py": "A = 1\n"}, '["noop"]')

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert report["fixes"] == []
        assert report["tx_id"] is None
        assert "applied" not in report
        assert [f["fix_id"] for f in report["reverted"]] == ["noop:same"]
        assert report["iterations"][0] == {
            "iteration": 1,
            "fixes": 0,
            "reverted": 1,
            "skipped_conflicts": 0,
            "declined": 0,
        }
        assert (project / "src" / "mod.py").read_text() == "A = 1\n"

    def test_a_repair_rewritten_by_a_later_one_still_counts_when_its_file_changed(
        self, tmp_path, custom_rule
    ):
        # Pins the documented BOUNDARY of the `fixes` guarantee: it is per
        # FILE, not per repair. Here iteration 2 rewrites the very bytes
        # iteration 1 produced, restoring the original text and appending to
        # it, so the transaction expresses only the append — yet `swap:a` is
        # still reported. That is deliberate and not fixable at this
        # granularity: the flattened diff is ONE line-trimmed splice per
        # file, and the identical byte-overlap shape is what the *legitimate*
        # conflict-loser cascade produces (iteration 1 deletes 'os, ' from
        # `import os, sys`, iteration 2 deletes the whole surviving line —
        # its splice covers iteration 1's footprint too). Dropping one would
        # mean dropping the other, and the conflict loser genuinely landed.
        # `reverted` covers the case that IS decidable: nothing changed at
        # all, asserted in the two tests above.
        def _rewrite(file_index, options):
            names = {s.name for s in file_index.symbols}
            if "A" in names and "stop" not in names:
                return [_text_remedy(file_index, "swap:a", "A = 1", "B = 1")]
            if "B" in names:
                return [
                    _text_remedy(
                        file_index, "swap:b", "B = 1", "A = 1\n\n\ndef stop():\n    pass"
                    )
                ]
            return []

        custom_rule("swap", _rewrite)
        runner = CliRunner()
        project = _project(tmp_path, runner, {"mod.py": "A = 1\n"}, '["swap"]')

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert report["stop_reason"] == "quiescent"
        assert [f["fix_id"] for f in report["fixes"]] == ["swap:a", "swap:b"]
        assert report["reverted"] == []
        assert report["applied"] is True
        # The invariant that IS promised holds: there is a transaction, and
        # rolling it back restores every byte the run changed.
        assert (project / "src" / "mod.py").read_text() == (
            "A = 1\n\n\ndef stop():\n    pass\n"
        )
        rolled = runner.invoke(
            main, ["rollback", report["tx_id"]], catch_exceptions=False
        )
        assert json.loads(rolled.output)["status"] == "rolled_back"
        assert (project / "src" / "mod.py").read_text() == "A = 1\n"

    @pytest.mark.parametrize(
        ("files", "rules", "extra", "flags"),
        [
            ({"mod.py": CASCADE_SOURCE}, CASCADE_RULES, CASCADE_EXTRA, []),
            ({"mod.py": CONFLICT_SOURCE}, '["unused-imports"]', "", []),
            (
                {"mod.py": CASCADE_SOURCE},
                CASCADE_RULES,
                CASCADE_EXTRA,
                ["--fix-max-iterations", "2"],
            ),
            (
                {"mod.py": "import os\n\n\ndef f():\n    return os.getcwd()\n"},
                '["unused-imports"]',
                "",
                [],
            ),
        ],
    )
    def test_fixes_is_non_empty_exactly_when_a_transaction_exists(
        self, tmp_path, files, rules, extra, flags
    ):
        runner = CliRunner()
        _project(tmp_path, runner, files, rules, extra)

        report, _ = _run(runner, "--fix", "--fix-until-clean", *flags)

        assert bool(report["fixes"]) is (report["tx_id"] is not None)
        assert bool(report["fixes"]) is ("applied" in report)
        assert len(report["fixes"]) == sum(
            row["fixes"] for row in report["iterations"]
        )
        assert len(report["reverted"]) == sum(
            row["reverted"] for row in report["iterations"]
        )

    def test_a_repair_that_never_sticks_stops_with_stop_reason_repeated_fix(
        self, tmp_path, custom_rule
    ):
        # One stable fix_id, a moving anchor: every iteration renames the
        # function one step further, so the tree never repeats and the cap is
        # not what stops it — the repeated fix_id is.
        def _bump(file_index, options):
            symbol = _function_named(file_index, "a")
            if symbol is None:
                return []
            index = int(symbol.name[1:])
            return [
                _text_remedy(
                    file_index,
                    "bump:always",
                    f"def {symbol.name}(",
                    f"def a{index + 1}(",
                )
            ]

        custom_rule("bump", _bump)
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": "def a0():\n    return 1\n"}, '["bump"]'
        )

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert report["stop_reason"] == "repeated-fix"
        assert report["quiescent"] is False
        assert report["iterations_run"] == 2
        # The re-proposal is abandoned, not applied a second time.
        assert [(f["fix_id"], f["iteration"]) for f in report["fixes"]] == [
            ("bump:always", 1)
        ]
        assert report["iterations"][1]["fixes"] == 0
        assert (project / "src" / "mod.py").read_text() == "def a1():\n    return 1\n"

    def test_an_unbounded_rule_is_stopped_by_the_cap(self, tmp_path, custom_rule):
        # Distinct fix ids and a never-repeating tree: the cap is the backstop
        # for exactly this case.
        def _walk(file_index, options):
            symbol = _function_named(file_index, "a")
            if symbol is None:
                return []
            index = int(symbol.name[1:])
            return [
                _text_remedy(
                    file_index,
                    f"walk:{index}",
                    f"def {symbol.name}(",
                    f"def a{index + 1}(",
                )
            ]

        custom_rule("walk", _walk)
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": "def a0():\n    return 1\n"}, '["walk"]'
        )

        report, _ = _run(
            runner, "--fix", "--fix-until-clean", "--fix-max-iterations", "3"
        )

        assert report["stop_reason"] == "max-iterations"
        assert report["iterations_run"] == 3
        assert [f["fix_id"] for f in report["fixes"]] == ["walk:0", "walk:1", "walk:2"]
        assert (project / "src" / "mod.py").read_text() == "def a3():\n    return 1\n"

    def test_stop_reason_is_reported_even_when_there_was_nothing_to_fix(
        self, tmp_path
    ):
        runner = CliRunner()
        _project(
            tmp_path,
            runner,
            {"mod.py": "import os\n\n\ndef f():\n    return os.getcwd()\n"},
            '["unused-imports"]',
        )

        report, exit_code = _run(runner, "--fix", "--fix-until-clean")

        assert report["stop_reason"] == "quiescent"
        assert report["iterations_run"] == 1
        assert report["fixes"] == []
        assert report["tx_id"] is None
        assert exit_code == 0


class TestCrossIterationReadThrough:
    """A remedy planned in iteration 2 must see iteration 1's bytes."""

    def test_iteration_two_plans_against_iteration_one_output(
        self, tmp_path, monkeypatch
    ):
        # submit_intent builds its OWN overlay per call, over the loop's
        # simulation overlay. If that nesting fell through to disk, the
        # iteration-2 planner would anchor its edits in pre-loop bytes: the
        # hash it records would be the real file's, and the splice into the
        # loop overlay would either mismatch or land at stale offsets.
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )
        real = check_fixes_module.submit_intent
        seen: list[tuple[str, bytes, tuple[str, ...]]] = []

        def _spy(intent, store, tx_store, **kwargs):
            simulated = store.read_file("src/mod.py")
            materialized = real(intent, store, tx_store, **kwargs)
            seen.append(
                (
                    intent.intent_id,
                    simulated,
                    tuple(edit.file_hash for edit in materialized.edits),
                )
            )
            return materialized

        monkeypatch.setattr(check_fixes_module, "submit_intent", _spy)
        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert [f["fix_id"] for f in report["fixes"]] == [
            "unused-symbol:delete:mod:_dead",
            "unused-imports:remove:mod:os",
        ]
        second = next(s for s in seen if s[0] == "unused-imports:remove:mod:os")
        _, simulated, hashes = second
        pre_loop = (project / "src" / "mod.py").read_bytes()
        assert simulated != pre_loop  # iteration 1 already deleted _dead
        assert b"_dead" not in simulated
        # The planner hashed what the loop overlay holds, not what disk holds.
        assert set(hashes) == {hashlib.sha256(simulated).hexdigest()}

    def test_the_per_iteration_rebind_is_fail_closed(self, tmp_path, monkeypatch):
        # Remove the re-bind and the simulated index goes stale: the next
        # iteration's planners must refuse on AnchorIndexFresh rather than
        # compute edits against offsets the previous splice invalidated.
        def _no_rebind(overlay, materialized, **kwargs):
            batch_module._apply_to_overlay(
                overlay,
                materialized,
                adapter=batch_module.PythonAdapter(),
                src_roots=("src",),
                rebind=False,
            )

        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )
        monkeypatch.setattr(check_fixes_module, "apply_to_overlay", _no_rebind)
        codes: list[str] = []
        real = check_fixes_module.submit_intent

        def _spy(intent, store, tx_store, **kwargs):
            try:
                return real(intent, store, tx_store, **kwargs)
            except SubmitError as error:
                codes.append(error.code)
                raise

        monkeypatch.setattr(check_fixes_module, "submit_intent", _spy)

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert [f["fix_id"] for f in report["fixes"]] == [
            "unused-symbol:delete:mod:_dead"
        ]
        # Iteration 2 planned against a stale simulated index and was refused
        # by AnchorIndexFresh. The refusal names a fix_id that already landed,
        # so the dedupe drops it from `declined` — the per-iteration breakdown
        # is where it stays visible.
        assert "stale-index" in codes
        assert report["iterations"][1] == {
            "iteration": 2,
            "fixes": 0,
            "reverted": 0,
            "skipped_conflicts": 0,
            "declined": 1,
        }
        # And the file still holds exactly iteration 1's work — no
        # stale-offset edit reached it.
        assert (project / "src" / "mod.py").read_text() == "import os\n\n\n"


class TestExternalEditsDuringTheLoop:
    """The tree the loop simulated against must still be there at the apply.

    The loop's window is the whole multi-iteration run — the widest of any
    simulation in the project — and its transaction is one line-trimmed
    whole-region splice per file, anchored to the real bytes read at flatten
    time. Without a pre-image check the applier's hash pre-flight would
    verify against bytes the simulation never saw and the splice would
    silently destroy whatever landed in between. Plain ``--fix`` fails closed
    on the same injection; the flag must not be weaker.
    """

    @staticmethod
    def _writing_after_the_first_iteration(project: Path, monkeypatch) -> None:
        """Make an external write land right after iteration 1's splice."""
        real = check_fixes_module.apply_to_overlay
        calls: list[int] = []

        def _splice_then_someone_else_saves(overlay, materialized, **kwargs):
            real(overlay, materialized, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                path = project / "src" / "mod.py"
                path.write_text(path.read_text() + "HUMAN = 1\n")

        monkeypatch.setattr(
            check_fixes_module, "apply_to_overlay", _splice_then_someone_else_saves
        )

    def test_the_loop_refuses_rather_than_overwrite_an_external_edit(
        self, tmp_path, monkeypatch
    ):
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )
        self._writing_after_the_first_iteration(project, monkeypatch)

        result = runner.invoke(
            main, ["check", "--fix", "--fix-until-clean"], catch_exceptions=False
        )

        assert result.exit_code != 0
        report = json.loads(result.output)
        assert report["code"] == "tree-changed"
        assert "src/mod.py" in report["error"]
        # Refused before anything was written: the edit is intact and no
        # transaction was persisted for a later `apply` to land.
        assert "HUMAN = 1\n" in (project / "src" / "mod.py").read_text()
        assert _transactions(project) == []

    def test_plain_fix_fails_closed_on_the_same_injection(
        self, tmp_path, monkeypatch
    ):
        # The control the flag is measured against: on the default path the
        # applier's own pre-flight catches it, leaving the tree untouched.
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )
        real = check_fixes_module._plan_pass

        def _plan_then_someone_else_saves(store, violations):
            planned = real(store, violations)
            path = project / "src" / "mod.py"
            path.write_text(path.read_text() + "HUMAN = 1\n")
            return planned

        monkeypatch.setattr(
            check_fixes_module, "_plan_pass", _plan_then_someone_else_saves
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)

        assert json.loads(result.output)["code"] == "apply-failed"
        assert "HUMAN = 1\n" in (project / "src" / "mod.py").read_text()

    def test_the_overlay_records_what_the_base_held_when_it_first_looked(
        self, indexed_project
    ):
        # The seam the refusal is built on: a pre-image per path the overlay
        # read through or wrote, taken once, from the BASE — not re-read at
        # diff time, which is the whole point.
        _project_dir, store = indexed_project({"mod.py": "A = 1\n"})
        overlay = OverlayIndexStore(store)

        assert overlay.base_preimages() == {}
        original = overlay.read_file("mod.py")
        overlay.write_file("mod.py", b"B = 1\n")
        overlay.read_file("mod.py")

        assert overlay.base_preimages() == {
            "mod.py": hashlib.sha256(original).hexdigest()
        }
        assert overlay.base_preimages()["mod.py"] != hashlib.sha256(
            b"B = 1\n"
        ).hexdigest()

    def test_flatten_store_only_verifies_pre_images_when_asked(
        self, indexed_project
    ):
        # Opt-in by design: `run_batch` flattens microseconds after its own
        # reads, so it keeps today's behavior and only the wide-window caller
        # pays for the check.
        project_dir, store = indexed_project({"mod.py": "A = 1\n"})
        overlay = OverlayIndexStore(store)
        overlay.write_file("mod.py", b"B = 1\n")
        (project_dir / "mod.py").write_text("C = 1\n")

        unchecked = batch_module.flatten_store(overlay, store, operation="check-fix")
        assert [edit.file for edit in unchecked.edits] == ["mod.py"]

        with pytest.raises(batch_module.StalePreimageError) as caught:
            batch_module.flatten_store(
                overlay, store, operation="check-fix", verify_preimages=True
            )
        assert "mod.py" in str(caught.value)


class TestWriteSafety:
    """The user's tree — including ``.pypeeker/`` — is untouched by the loop."""

    def test_simulation_unsafe_rules_names_the_rule_that_writes(self):
        assert SIMULATION_UNSAFE_RULES == frozenset({BORN_PRIVATE})

    def test_the_loop_never_lets_born_private_seed_a_baseline(
        self, indexed_project, tmp_path
    ):
        # born_private writes its symbol baseline through
        # `baseline_path(context.store.project_root)`, and an overlay's
        # project_root is the REAL root — so an unfiltered per-iteration run
        # would write into the user's .pypeeker/ mid-loop.
        project_dir, store = indexed_project({"mod.py": "def f():\n    return 1\n"})
        config = CheckConfig(src=(), rules=(BORN_PRIVATE,))
        engine = CheckEngine(store, config)

        outcome = apply_check_fixes(
            store,
            TransactionStore(project_dir),
            engine,
            [],
            plan_only=True,
            max_iterations=5,
        )

        assert outcome.stop_reason == "quiescent"
        assert outcome.iterations_run == 1
        assert not (project_dir / ".pypeeker" / BASELINE_FILE).exists()

    def test_a_planned_run_writes_nothing_but_the_pending_transaction(
        self, tmp_path
    ):
        # The overlay substrate means there is no mirror directory to clean up
        # and nothing to write mid-loop: a whole-directory byte snapshot,
        # INCLUDING .pypeeker/, must be unchanged apart from the one PENDING
        # transaction the run deliberately persists.
        runner = CliRunner()
        project = _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        def _snapshot() -> dict[str, str]:
            return {
                str(path.relative_to(project)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(project.rglob("*"))
                if path.is_file()
            }

        before = _snapshot()
        report, _ = _run(runner, "--fix", "--fix-until-clean", "--plan")
        after = _snapshot()

        assert set(after) - set(before) == {
            f".pypeeker/transactions/{report['tx_id']}.jsonl"
        }
        assert not set(before) - set(after)
        assert all(after[path] == digest for path, digest in before.items())

    def test_residual_is_computed_by_the_original_engine_on_the_real_store(
        self, tmp_path
    ):
        # `unused-public-symbol` on a PUBLIC symbol has no remedy, so it can
        # only be counted — and only by a run against the real post-apply
        # tree, with the full configured rule set.
        runner = CliRunner()
        _project(
            tmp_path,
            runner,
            {"mod.py": "import os\n\n\ndef public():\n    return 1\n"},
            CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, exit_code = _run(runner, "--fix", "--fix-until-clean")

        assert [f["fix_id"] for f in report["fixes"]] == ["unused-imports:remove:mod:os"]
        assert report["residual_violations"] == 1
        assert exit_code == 1


class TestDefaultPathIsFrozen:
    """``--fix`` without the flag cannot reach — or report — the loop."""

    def test_the_default_report_carries_none_of_the_loop_keys(self, tmp_path):
        runner = CliRunner()
        _project(
            tmp_path, runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )

        report, _ = _run(runner, "--fix")

        assert set(report) == {
            "fixes",
            "skipped_conflicts",
            "declined",
            "residual_violations",
            "tx_id",
            "applied",
            "files_modified",
            "files_reindexed",
            "files_reindex_failed",
        }
        assert all("iteration" not in fix for fix in report["fixes"])

    def test_the_default_report_matches_a_golden_capture(self, tmp_path):
        # The golden was captured from the unflagged path on this exact
        # project before the fixpoint existed, and is asserted whole: a key
        # added, renamed, retyped, or reordered out of `fixes` fails here even
        # if every other assertion in the suite still passes.
        runner = CliRunner()
        _project(
            tmp_path, runner, {"mod.py": CONFLICT_SOURCE}, '["unused-imports"]'
        )

        report, exit_code = _run(runner, "--fix")

        tx_id = report.pop("tx_id")
        assert isinstance(tx_id, str) and len(tx_id) == 12
        assert report == {
            "fixes": [
                {
                    "fix_id": "unused-imports:remove:mod:os",
                    "description": "remove the unused import 'os'",
                    "violation": (
                        "src/mod.py:1: [unused-imports] import 'os' is unused "
                        "in this module"
                    ),
                }
            ],
            "skipped_conflicts": [
                {
                    "fix_id": "unused-imports:remove:mod:sys",
                    "description": "remove the unused import 'sys'",
                    "violation": (
                        "src/mod.py:1: [unused-imports] import 'sys' is unused "
                        "in this module"
                    ),
                }
            ],
            "declined": [],
            "residual_violations": 1,
            "applied": True,
            "files_modified": ["src/mod.py"],
            "files_reindexed": ["src/mod.py"],
            "files_reindex_failed": [],
        }
        assert exit_code == 1

    def test_the_default_path_never_calls_the_fixpoint(self, tmp_path, monkeypatch):
        # Structural, not statistical: the loop is patched to explode, and the
        # default path still completes — so it cannot be reaching it.
        def _explode(*args, **kwargs):
            raise AssertionError("the default --fix path entered the fixpoint loop")

        monkeypatch.setattr(check_fixes_module, "_run_fixpoint", _explode)
        runner = CliRunner()
        for flags in (["--fix"], ["--fix", "--plan"]):
            project = _project(
                tmp_path / "".join(flags), runner, {"mod.py": CASCADE_SOURCE},
                CASCADE_RULES, CASCADE_EXTRA,
            )
            report, _ = _run(runner, *flags)
            assert report["fixes"], project

    def test_the_outcome_carries_no_loop_fields_on_the_default_path(
        self, indexed_project
    ):
        project_dir, store = indexed_project({"mod.py": "X = 1\n"})
        engine = CheckEngine(store, CheckConfig(src=(), rules=()))

        outcome = apply_check_fixes(
            store, TransactionStore(project_dir), engine, []
        )

        assert (
            outcome.iterations,
            outcome.iterations_run,
            outcome.quiescent,
            outcome.stop_reason,
        ) == (None, None, None, None)

    def test_the_flagged_report_is_a_strict_superset(self, tmp_path):
        runner = CliRunner()
        _project(
            tmp_path / "plain", runner, {"mod.py": CASCADE_SOURCE}, CASCADE_RULES,
            CASCADE_EXTRA,
        )
        plain, _ = _run(runner, "--fix")

        flagged_runner = CliRunner()
        _project(
            tmp_path / "loop", flagged_runner, {"mod.py": CASCADE_SOURCE},
            CASCADE_RULES, CASCADE_EXTRA,
        )
        flagged, _ = _run(flagged_runner, "--fix", "--fix-until-clean")

        assert set(plain) < set(flagged)
        assert set(flagged) - set(plain) == {
            "reverted",
            "iterations",
            "iterations_run",
            "quiescent",
            "stop_reason",
        }
        for key in plain:
            assert type(flagged[key]) is type(plain[key])
        assert flagged["applied"] is True
        assert isinstance(flagged["tx_id"], str)

    def test_the_loop_config_narrowing_does_not_touch_the_engine(
        self, indexed_project
    ):
        # The narrowed config is a copy: the caller's engine keeps the full
        # rule set, which is what makes the residual run honest.
        project_dir, store = indexed_project({"mod.py": "X = 1\n"})
        config = CheckConfig(src=(), rules=(BORN_PRIVATE, "unused-imports"))
        engine = CheckEngine(store, config)

        apply_check_fixes(
            store,
            TransactionStore(project_dir),
            engine,
            [],
            plan_only=True,
            max_iterations=3,
        )

        assert engine.config is config
        assert engine.config.rules == (BORN_PRIVATE, "unused-imports")
        assert dataclasses.replace(config, rules=()).rules == ()


class TestFlagUsageErrors:
    """The new flags follow ``--plan``'s existing guard convention."""

    @pytest.mark.parametrize(
        "flags",
        [
            ["--fix-until-clean"],
            ["--fix-max-iterations", "3"],
            ["--fix", "--fix-max-iterations", "3"],
            ["--fix", "--fix-until-clean", "--fix-max-iterations", "1"],
        ],
    )
    def test_misused_flags_are_usage_errors(self, tmp_path, flags):
        runner = CliRunner()
        _project(tmp_path, runner, {"mod.py": "X = 1\n"}, '["unused-imports"]')

        result = runner.invoke(main, ["check", *flags])

        assert result.exit_code != 0
        assert "Error" in result.output


class TestUnbindableSimulatedState:
    """TASK-141: a splice the binder cannot read back refuses structurally.

    The last member of the raw-decode family, and the one no planner guard
    could have caught. ``mod.py`` below indexes cleanly: its latin-1 literal
    is an expression statement, which the binder never decodes. Deleting the
    dead ``def`` above it — a repair ``unused-public-symbol`` proposes, over
    a span that is pure ASCII — promotes the literal into module-docstring
    position, which the binder *does* decode. So the loop's re-bind of the
    simulated file raised ``UnicodeDecodeError`` straight out of
    ``apply_to_overlay``: empty stdout, a traceback on stderr, no report.

    It now travels the fixpoint's existing failure channel instead. Plain
    ``--fix`` is the control: it never re-binds a simulation (its batch of
    one opts out with ``rebind_final=False``), so it keeps repairing and
    reporting the failure through the applier's ``files_reindex_failed`` —
    the divergence is deliberate, and pinning both halves is what keeps it so.
    """

    SOURCE = b'def _dead():\n    return 1\n\n\n"caf\xe9"\n'
    ASCII_STANDIN = 'def _dead():\n    return 1\n\n\n"cafe"\n'

    def _project_with_raw_bytes(self, tmp_path, runner) -> Path:
        project = _project(
            tmp_path,
            runner,
            {"mod.py": self.ASCII_STANDIN},
            '["unused-public-symbol"]',
            CASCADE_EXTRA,
        )
        (project / "src" / "mod.py").write_bytes(self.SOURCE)
        result = runner.invoke(
            main, ["index", str(project / "src")], catch_exceptions=False
        )
        # The premise: the tool accepts these bytes today, without an error.
        assert result.exit_code == 0
        assert json.loads(result.output)["errors"] == []
        return project

    def test_the_loop_reports_a_structured_error_not_a_traceback(self, tmp_path):
        runner = CliRunner()
        project = self._project_with_raw_bytes(tmp_path, runner)

        result = runner.invoke(
            main, ["check", "--fix", "--fix-until-clean"], catch_exceptions=False
        )

        report = json.loads(result.output)
        assert report["code"] == "simulation-failed"
        assert "src/mod.py" in report["error"]
        assert "not valid UTF-8" in report["error"]
        assert result.exit_code != 0
        # Refused before anything was written: no transaction to `apply`
        # later, and the undecodable byte survives untouched.
        assert _transactions(project) == []
        assert (project / "src" / "mod.py").read_bytes() == self.SOURCE

    def test_plain_fix_still_repairs_and_reports_the_reindex_failure(
        self, tmp_path
    ):
        """The control: the default path is unmoved by the fixpoint's guard."""
        runner = CliRunner()
        project = self._project_with_raw_bytes(tmp_path, runner)

        report, exit_code = _run(runner, "--fix")

        assert [fix["fix_id"] for fix in report["fixes"]] == [
            "unused-symbol:delete:mod:_dead"
        ]
        assert report["applied"] is True
        assert [entry["file"] for entry in report["files_reindex_failed"]] == [
            "src/mod.py"
        ]
        assert exit_code == 1
        assert (project / "src" / "mod.py").read_bytes() == b'"caf\xe9"\n'

    def test_an_undecodable_comment_does_not_stop_the_loop(self, tmp_path):
        """Span-scoping's counterpart at the bind: only promotion refuses.

        Same dead definition, same deletion, but the undecodable bytes sit in
        a comment no binder node covers — so the post-splice file binds and
        the cascade runs to quiescence as usual. A whole-file UTF-8 guard on
        the simulation would have refused this one too.
        """
        runner = CliRunner()
        project = _project(
            tmp_path,
            runner,
            {"mod.py": "import os\n\n\ndef _dead():\n    return os.getcwd()\n"},
            CASCADE_RULES,
            CASCADE_EXTRA,
        )
        raw = b"import os\n\n\ndef _dead():\n    return os.getcwd()\n# caf\xe9\n"
        (project / "src" / "mod.py").write_bytes(raw)
        assert (
            runner.invoke(
                main, ["index", str(project / "src")], catch_exceptions=False
            ).exit_code
            == 0
        )

        report, _ = _run(runner, "--fix", "--fix-until-clean")

        assert report["stop_reason"] == "quiescent"
        assert sorted(fix["fix_id"] for fix in report["fixes"]) == [
            "unused-imports:remove:mod:os",
            "unused-symbol:delete:mod:_dead",
        ]
        # Both repairs landed; the undecodable comment survives byte-for-byte.
        assert (project / "src" / "mod.py").read_bytes() == b"\n\n# caf\xe9\n"
