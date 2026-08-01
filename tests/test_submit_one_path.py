"""Proof tests for TASK-129 PR3: submit has exactly ONE execution path.

Before this change ``app/submit.py`` had two ways to reach a planner: a
single-intent *fast path* that looked the materializer up itself
(``get_materializer(intent.kind)``) and called it against the real store, and
:func:`~pypeeker.refactor.batch.run_batch` for everything else. The fast path
is gone. Both entry points now go through ``run_batch``, and the two additive
carry-out fields it grew — :attr:`~pypeeker.refactor.batch.ExecutedIntent.
summary`/``warnings`` and :attr:`~pypeeker.refactor.batch.DroppedIntent.code`
— are what let a batch of one still return the planner's own
:class:`~pypeeker.models.transaction.TransactionSummary` and raise the
planner's own refusal code.

What each class here proves:

* :class:`TestNoFastPath` — the collapse actually happened. Not a source
  grep alone: a registry-override materializer records the store and the
  transaction store it was handed, which distinguishes "planned through the
  batch engine's overlay" from "planned directly against the caller's store".
* :class:`TestSingleIntentThroughEngineParity` — the engine round trip is
  invisible. The summary is the planner's own, field for field, the
  transaction lands in the caller's store, and ``apply``/``rollback`` work on
  it unchanged.
* :class:`TestRefusalCodeReachability` — the matrix. Every refusal code that
  was reachable before the collapse is reachable after it, end to end through
  the CLI, with the same JSON ``code``.
* :class:`TestNoStrayTransactionOnRefusal` — a refusal writes nothing. The
  whole ``.pypeeker/`` tree is byte-identical across every refusal class.
* :class:`TestCheckFixInteraction` — ``check --fix`` is the heaviest
  ``submit_intent`` consumer (one call per remedy, against the real store,
  with its own scratch transaction store). Its "every remedy planned against
  ONE pre-fix state" property and its byte-range conflict model survive the
  per-call overlay.
* :class:`TestNoWastedRebind` — the collapse charges a submit only for work
  someone reads. A batch of one skips the simulation loop's final re-bind
  (``rebind_final=False``), which is what keeps ``check --fix``'s per-remedy
  loop from re-parsing the whole file once per fix; a real batch still
  re-binds every intermediate state, and the byte-level contracts — overlay
  content, the flattened transaction, the splice verification that drops —
  are identical either way.

The frozen oracles for the same contracts live elsewhere and are *not*
duplicated here: ``test_app_submit.py::TestSingleIntentParity`` compares
summaries field-for-field against direct planner calls,
``test_refusal_vocabulary.py`` pins every remedy planner's
code/precondition/detail triple through ``submit_intent``, and
``test_check_fix.py`` owns the ``check --fix`` report shape.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import ClassVar

import pytest
from click.testing import CliRunner

from pypeeker.app import SubmitError, submit_intent, submit_intents
from pypeeker.cli import main
from pypeeker.intents import (
    EMPTY_EFFECT,
    EMPTY_FOOTPRINT,
    ChangeVisibilityIntent,
    Effect,
    Footprint,
    Intent,
    RenameIntent,
)
from pypeeker.models import to_dict
from pypeeker.refactor import RenamePlanner
from pypeeker.refactor import registry
from pypeeker.refactor.registry import Materialized, register_planner
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore

LIB = "def helper():\n    return 1\n"
APP_CALL = "from lib import helper\n\ndef use():\n    x = helper()\n    return x\n"

APP_PYPROJECT = '[project]\nname = "test"\n'
LIBRARY_PYPROJECT = (
    '[project]\nname = "test"\n\n[tool.pypeeker.visibility]\nmode = "library"\n'
)
BARREL_FILES = {
    "pkg/__init__.py": "from pkg.mod import helper\n",
    "pkg/mod.py": "def helper():\n    return 1\n",
    "app.py": "from pkg import helper\n\nhelper()\n",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cli_project(
    tmp_path: Path,
    monkeypatch,
    files: dict[str, str],
    pyproject: str = APP_PYPROJECT,
) -> tuple[Path, CliRunner]:
    """Create, chdir into, and index a project; return ``(project, runner)``."""
    (tmp_path / "pyproject.toml").write_text(pyproject)
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(tmp_path)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return tmp_path, runner


def _refused(runner: CliRunner, args: list[str]) -> dict:
    """Invoke a mutating command expected to refuse; return its error JSON."""
    result = runner.invoke(main, args)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "error" in payload, payload
    return payload


def _fix_project(
    tmp_path: Path, runner: CliRunner, files: dict[str, str], rules: str, extra: str = ""
) -> Path:
    """A chdir'd, indexed project with ``rules`` enabled under ``src/``."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n[tool.pypeeker]\nsrc = ["src"]\n'
        f"rules = {rules}\n{extra}"
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


def _snapshot(root: Path) -> dict[str, str]:
    """SHA-256 of every file under ``root`` (including ``.pypeeker/``)."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@dataclasses.dataclass(frozen=True)
class _SpyIntent(Intent):
    """A no-op intent whose materializer records what the engine handed it."""

    kind: ClassVar[str] = "test-only:one-path-spy"

    def footprint(self, store) -> Footprint:
        return EMPTY_FOOTPRINT

    def predicted_effect(self, store) -> Effect:
        _EFFECT_STORES.append(store)
        return EMPTY_EFFECT

    def remap(self, effect) -> Intent:
        return self


_EFFECT_STORES: list[object] = []
"""Stores ``_SpyIntent.predicted_effect`` was called with, in call order."""


@pytest.fixture
def spy_kind():
    """Register ``_SpyIntent``'s materializer; yield the call recorder."""
    calls: list[dict] = []
    _EFFECT_STORES.clear()

    @register_planner(_SpyIntent.kind)
    def _materialize_spy(intent, store, tx_store):
        calls.append({"store": store, "tx_store": tx_store})
        return Materialized()

    try:
        yield calls
    finally:
        registry._REGISTRY.pop(_SpyIntent.kind, None)
        _EFFECT_STORES.clear()


# ---------------------------------------------------------------------------
# (1) the fast path is gone
# ---------------------------------------------------------------------------


class TestNoFastPath:
    """``submit_intent`` reaches planners only through ``run_batch``."""

    def test_module_has_no_single_intent_branch_or_materializer_lookup(self):
        """The literal acceptance criterion, asserted against the source.

        Behavioral proof lives in the rest of this class; this one exists so
        that a re-introduced fast path fails loudly at its own site rather
        than only through a downstream symptom.
        """
        from pypeeker.app import submit as submit_module

        source = Path(submit_module.__file__).read_text()
        assert "len(intents) == 1" not in source
        assert "get_materializer" not in source

    def test_single_intent_is_materialized_against_the_engine_overlay(
        self, spy_kind, indexed_project, transaction_store
    ):
        """The store the planner sees is the batch engine's simulation overlay.

        This is the discriminator the source grep cannot give: only
        ``run_batch`` layers an :class:`OverlayIndexStore` over the caller's
        store. The deleted fast path called the materializer with the caller's
        store itself.
        """
        _, store = indexed_project({"mod.py": "x = 1\n"})

        submit_intent(_SpyIntent("spy-1"), store, transaction_store)

        [call] = spy_kind
        assert isinstance(call["store"], OverlayIndexStore)
        assert not isinstance(store, OverlayIndexStore)

    def test_single_intent_runs_the_engine_loop_not_just_the_scheduler(
        self, spy_kind, indexed_project, transaction_store
    ):
        """``predicted_effect`` is evaluated twice: once pure, once in the loop.

        ``schedule`` computes predicted effects against the *caller's* store
        (it is pure); ``run_batch``'s execution loop re-computes the executed
        intent's effect against the *overlay* before folding it into the batch
        substitution. Seeing both calls proves the whole engine ran, not just
        the scheduling half the old fast path borrowed.
        """
        _, store = indexed_project({"mod.py": "x = 1\n"})

        submit_intent(_SpyIntent("spy-1"), store, transaction_store)

        assert [isinstance(s, OverlayIndexStore) for s in _EFFECT_STORES] == [
            False,
            True,
        ]

    def test_single_intent_gets_the_callers_transaction_store_verbatim(
        self, spy_kind, indexed_project, transaction_store
    ):
        """Never a scratch store: the planner's transaction is the durable one.

        The plural path swaps in a scratch store because its durable output is
        the flattened transaction; a batch of one must not, or ``apply`` /
        ``rollback`` / ``transactions show`` would find nothing.
        """
        _, store = indexed_project({"mod.py": "x = 1\n"})

        submit_intent(_SpyIntent("spy-1"), store, transaction_store)

        [call] = spy_kind
        assert call["tx_store"] is transaction_store

    def test_submit_intents_of_one_takes_the_same_engine_path(
        self, spy_kind, indexed_project, transaction_store
    ):
        """``submit_intents([one])`` is the singular contract, same engine."""
        _, store = indexed_project({"mod.py": "x = 1\n"})

        result = submit_intents([_SpyIntent("spy-1")], store, transaction_store)

        assert isinstance(result, Materialized)
        [call] = spy_kind
        assert isinstance(call["store"], OverlayIndexStore)
        assert call["tx_store"] is transaction_store

    def test_submit_intents_of_many_still_scratches_its_transactions(
        self, spy_kind, indexed_project, transaction_store
    ):
        """The plural contract is unchanged: scratch store, ``BatchResult``."""
        from pypeeker.refactor import BatchResult

        _, store = indexed_project({"mod.py": "x = 1\n"})

        result = submit_intents(
            [_SpyIntent("spy-1"), _SpyIntent("spy-2")], store, transaction_store
        )

        assert isinstance(result, BatchResult)
        assert {id(call["tx_store"]) for call in spy_kind} != {id(transaction_store)}
        assert all(call["tx_store"] is not transaction_store for call in spy_kind)


# ---------------------------------------------------------------------------
# (2) a batch of one is indistinguishable from a direct planner call
# ---------------------------------------------------------------------------


class TestSingleIntentThroughEngineParity:
    """The engine round trip does not move the planner's own output."""

    def test_summary_is_the_planners_own_field_for_field(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP_CALL})

        direct = RenamePlanner(store, transaction_store).plan("lib:helper", "assist")
        submitted = submit_intent(
            RenameIntent("r1", "lib:helper", "assist"), store, transaction_store
        )

        assert submitted.summary is not None
        direct_dict = to_dict(direct)
        submitted_dict = to_dict(submitted.summary)
        # Every field but the two plan-time-volatile ones must match exactly,
        # and the volatile ones must still be present and distinct.
        for field in direct_dict:
            if field in ("tx_id", "created_at"):
                continue
            assert submitted_dict[field] == direct_dict[field], field
        assert submitted_dict["tx_id"] != direct_dict["tx_id"]
        assert submitted.summary.operation == "rename"

    def test_operation_is_never_batch_for_a_single_intent_command(
        self, indexed_project, transaction_store
    ):
        """``flatten_batch`` is never reached: no ``"batch"`` transaction exists.

        ``flatten_batch`` is the only producer of an ``operation == "batch"``
        header, so its absence from the caller's store is the assertion that
        submit did not route through the flattening half of the engine.
        """
        _, store = indexed_project({"lib.py": LIB, "app.py": APP_CALL})

        submitted = submit_intent(
            RenameIntent("r1", "lib:helper", "assist"), store, transaction_store
        )

        tx_ids = transaction_store.list()
        assert tx_ids == [submitted.summary.tx_id]
        header, _, _ = transaction_store.load(submitted.summary.tx_id)
        assert header.operation == "rename"
        assert all(
            transaction_store.load(tx_id)[0].operation != "batch" for tx_id in tx_ids
        )

    def test_exactly_one_transaction_lands_in_the_callers_store(
        self, indexed_project, transaction_store
    ):
        _, store = indexed_project({"lib.py": LIB, "app.py": APP_CALL})

        submitted = submit_intent(
            RenameIntent("r1", "lib:helper", "assist"), store, transaction_store
        )

        assert len(transaction_store.list()) == 1
        header, edits, _ = transaction_store.load(submitted.summary.tx_id)
        assert header.symbol_id == "lib:helper"
        assert header.old_name == "helper"
        assert header.new_name == "assist"
        assert len(edits) == submitted.summary.edit_count

    def test_the_planners_transaction_still_applies_and_rolls_back(
        self, indexed_project, transaction_store
    ):
        """``apply``/``rollback`` work on it unchanged — the whole point of
        handing ``run_batch`` the caller's real transaction store."""
        from pypeeker.refactor import TransactionApplier

        project_dir, store = indexed_project({"lib.py": LIB, "app.py": APP_CALL})

        submitted = submit_intent(
            RenameIntent("r1", "lib:helper", "assist"), store, transaction_store
        )
        applier = TransactionApplier(store, transaction_store)
        assert applier.apply(submitted.summary.tx_id)["status"] == "applied"
        assert "def assist():" in (project_dir / "lib.py").read_text()

        applier.rollback(submitted.summary.tx_id)
        assert (project_dir / "lib.py").read_text() == LIB
        assert (project_dir / "app.py").read_text() == APP_CALL

    def test_warnings_survive_the_executed_intent_carry_out(
        self, indexed_project, transaction_store
    ):
        """promote/demote's warnings ride out on ``ExecutedIntent.warnings``."""
        from pypeeker.refactor.visibility_ops import VisibilityPlanner

        _, store = indexed_project(BARREL_FILES)

        direct = VisibilityPlanner(store, transaction_store).plan_demote(
            "pkg.mod:helper"
        )
        submitted = submit_intent(
            ChangeVisibilityIntent("v1", "pkg.mod:helper", "demote"),
            store,
            transaction_store,
        )

        assert direct.warnings  # sanity: this scenario warns (barrel rewrite)
        assert submitted.warnings == direct.warnings
        assert isinstance(submitted.warnings, list)


# ---------------------------------------------------------------------------
# (3) every refusal code reachable before the collapse is reachable after it
# ---------------------------------------------------------------------------


class TestRefusalCodeReachability:
    """End-to-end through the CLI: same command, same JSON ``code``.

    The codes come from three different producers, all of which now travel
    the same wire (``MaterializeError.code`` -> ``DroppedIntent.code`` ->
    ``SubmitError.code`` -> the CLI's error envelope):

    * ``plan-refused`` — the ``default_error_code`` every planner-backed kind
      falls back to when its materializer refuses with no code of its own;
    * the visibility-op codes — ``change-visibility``'s several refusal
      classes, the reason ``MaterializeError`` grew a ``code`` at all;
    * the remedy slugs — the six phase-4 remedy planners, surfaced through
      ``check --fix``'s ``declined[].reason`` rather than the error envelope.
    """

    def test_plan_refused_from_rename(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def foo(): pass\n\n\ndef bar(): pass\n"}
        )
        assert _refused(runner, ["rename", "mod:foo", "bar"])["code"] == "plan-refused"

    def test_plan_refused_from_inline_variable(self, tmp_path, monkeypatch):
        _, runner = _cli_project(tmp_path, monkeypatch, {"mod.py": "def f():\n    pass\n"})
        assert (
            _refused(runner, ["inline-variable", "mod:f:nope"])["code"] == "plan-refused"
        )

    def test_plan_refused_from_extract_variable(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def f():\n    return 1\n"}
        )
        payload = _refused(
            runner, ["extract-variable", "mod.py", "99:1", "99:5", "value"]
        )
        assert payload["code"] == "plan-refused"

    def test_plan_refused_from_extract_method(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def f():\n    return 1\n"}
        )
        payload = _refused(runner, ["extract-method", "mod.py", "99", "99", "helper"])
        assert payload["code"] == "plan-refused"

    def test_already_private_from_demote(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def _quiet():\n    pass\n"}
        )
        assert _refused(runner, ["demote", "mod:_quiet"])["code"] == "already-private"

    def test_already_public_from_promote(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def loud():\n    pass\n"}
        )
        assert _refused(runner, ["promote", "mod:loud"])["code"] == "already-public"

    def test_export_target_from_promote(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def _solo():\n    pass\n\n\n_solo()\n"}
        )
        payload = _refused(runner, ["promote", "mod:_solo", "--add-export", "nosuch"])
        assert payload["code"] == "export-target"

    def test_protected_public_api_from_demote(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path, monkeypatch, BARREL_FILES, pyproject=LIBRARY_PYPROJECT
        )
        payload = _refused(runner, ["demote", "pkg.mod:helper"])
        assert payload["code"] == "protected-public-api"

    def test_rename_refused_from_demote(self, tmp_path, monkeypatch):
        _, runner = _cli_project(
            tmp_path,
            monkeypatch,
            {"mod.py": "def helper(): pass\n\n\ndef _helper(): pass\n"},
        )
        payload = _refused(runner, ["demote", "mod:helper"])
        assert payload["code"] == "rename-refused"

    @pytest.mark.parametrize(
        "slug,files,rules,mutate,flags",
        [
            (
                "ambiguous",
                {"mod.py": 'def f(x):\n    xs = [f"{x}"]\n    return xs[0]\n'},
                '["prefer-tuple"]',
                None,
                [],
            ),
            (
                "text-mismatch",
                {
                    "mod.py": (
                        "from typing import (\n    Optional,\n)\n\n\n"
                        "def f():\n    return 1\n"
                    )
                },
                '["unused-imports"]',
                None,
                [],
            ),
            (
                "stale-index",
                {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"},
                '["prefer-tuple"]',
                "rewrite",
                ["--no-refresh"],
            ),
            (
                "file-missing",
                {"mod.py": "def f():\n    xs = [1, 2]\n    return xs[0]\n"},
                '["prefer-tuple"]',
                "unlink",
                ["--no-refresh"],
            ),
        ],
    )
    def test_remedy_slug_reaches_check_fix_declined(
        self, tmp_path, slug, files, rules, mutate, flags
    ):
        """The four legacy ``check --fix`` refusal slugs, end to end.

        Each is produced by a remedy planner's ``MaterializeError.code``,
        which after the collapse travels through ``DroppedIntent.code``
        before ``check_fixes`` reads it off ``SubmitError.code`` into
        ``declined[].reason``.
        """
        runner = CliRunner()
        project = _fix_project(tmp_path, runner, files, rules=rules)
        source = project / "src" / "mod.py"
        if mutate == "rewrite":
            source.write_text(source.read_text() + "# drift\n")
        elif mutate == "unlink":
            source.unlink()

        result = runner.invoke(main, ["check", "--fix", *flags], catch_exceptions=False)
        report = json.loads(result.output)

        assert report["fixes"] == []
        [declined] = report["declined"]
        assert declined["reason"] == slug

    def test_schedule_failed_is_still_reachable_through_submit(
        self, indexed_project, transaction_store
    ):
        """No CLI command builds a single intent with ``deps``, so this code is
        library-reachable only — ``run_batch`` must still schedule first.

        (``cli.py``'s ``batch`` command emits the same code from its own
        ``ScheduleError`` handler; that path never went through ``submit``.)
        """
        _, store = indexed_project({"mod.py": "def foo(): pass\n"})
        intent = RenameIntent("r1", "mod:foo", "bar", deps=frozenset({"no-such-id"}))

        with pytest.raises(SubmitError) as error:
            submit_intent(intent, store, transaction_store)

        assert error.value.code == "schedule-failed"
        assert transaction_store.list() == []

    def test_unknown_kind_still_falls_back_to_the_default_error_code(
        self, indexed_project, transaction_store
    ):
        """A registry miss has no ``code``; the caller's default is used.

        The engine's miss message is the historical one, so the detail is
        unchanged too.
        """
        _, store = indexed_project({"mod.py": "x = 1\n"})

        with pytest.raises(SubmitError) as error:
            submit_intent(
                _SpyIntent("ghost"),
                store,
                transaction_store,
                default_error_code="custom-default",
            )

        assert error.value.code == "custom-default"
        assert error.value.detail == (
            f"no executor for intent kind '{_SpyIntent.kind}'"
        )


# ---------------------------------------------------------------------------
# (4) a refusal writes nothing
# ---------------------------------------------------------------------------


class TestNoStrayTransactionOnRefusal:
    """Every refusal class leaves the project byte-identical."""

    @pytest.mark.parametrize(
        "files,intent_factory,pyproject",
        [
            # plan-refused (rename: the target name is already bound)
            (
                {"mod.py": "def foo(): pass\n\n\ndef bar(): pass\n"},
                lambda: RenameIntent("r1", "mod:foo", "bar"),
                APP_PYPROJECT,
            ),
            # schedule-failed (unknown explicit dependency)
            (
                {"mod.py": "def foo(): pass\n"},
                lambda: RenameIntent(
                    "r1", "mod:foo", "bar", deps=frozenset({"no-such-id"})
                ),
                APP_PYPROJECT,
            ),
            # already-private
            (
                {"mod.py": "def _quiet():\n    pass\n"},
                lambda: ChangeVisibilityIntent("v1", "mod:_quiet", "demote"),
                APP_PYPROJECT,
            ),
            # export-target
            (
                {"mod.py": "def _solo():\n    pass\n\n\n_solo()\n"},
                lambda: ChangeVisibilityIntent(
                    "v1", "mod:_solo", "promote", add_export="nosuch"
                ),
                APP_PYPROJECT,
            ),
            # protected-public-api (library mode)
            (
                BARREL_FILES,
                lambda: ChangeVisibilityIntent("v1", "pkg.mod:helper", "demote"),
                LIBRARY_PYPROJECT,
            ),
        ],
    )
    def test_refusal_leaves_the_project_untouched(
        self, tmp_path, indexed_project, files, intent_factory, pyproject
    ):
        (tmp_path / "pyproject.toml").write_text(pyproject)
        project_dir, store = indexed_project(files)
        transaction_store = TransactionStore(project_dir)
        before = _snapshot(project_dir)

        with pytest.raises(SubmitError):
            submit_intent(intent_factory(), store, transaction_store)

        assert transaction_store.list() == []
        assert _snapshot(project_dir) == before

    def test_refusal_through_the_cli_leaves_no_transaction(
        self, tmp_path, monkeypatch
    ):
        """The CLI-level statement of the same invariant."""
        project, runner = _cli_project(
            tmp_path, monkeypatch, {"mod.py": "def foo(): pass\n\n\ndef bar(): pass\n"}
        )
        before = _snapshot(project)

        assert _refused(runner, ["rename", "mod:foo", "bar"])["code"] == "plan-refused"

        assert TransactionStore(project).list() == []
        assert _snapshot(project) == before


# ---------------------------------------------------------------------------
# (5) check --fix: the heaviest submit_intent consumer
# ---------------------------------------------------------------------------


class TestCheckFixInteraction:
    """One overlay per remedy, over one unmutated store — and one transaction.

    ``check --fix`` loops ``submit_intent(remedy, store, scratch)``. After the
    collapse each of those calls builds its own fresh overlay over the same
    real store, which is exactly what preserves the property its docstring
    makes load-bearing: every surviving repair is planned against ONE state,
    the pre-fix tree. If the per-call overlay leaked between remedies, the
    second repair's offsets would describe post-first-repair bytes and the
    combined transaction would not apply.
    """

    SOURCE = (
        "import os\n"
        "\n"
        "\n"
        "def f():\n"
        "    xs = [1, 2, 3]\n"
        "    return xs[0]\n"
    )

    def test_two_remedies_in_one_file_plan_against_the_same_pre_fix_state(
        self, tmp_path
    ):
        runner = CliRunner()
        project = _fix_project(
            tmp_path,
            runner,
            {"mod.py": self.SOURCE},
            rules='["unused-imports", "prefer-tuple"]',
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert sorted(fix["fix_id"] for fix in report["fixes"]) == [
            "prefer-tuple:tuplify:mod:f:xs",
            "unused-imports:remove:mod:os",
        ]
        assert report["skipped_conflicts"] == []
        assert report["declined"] == []
        assert report["applied"] is True
        # Both repairs landed, which only works if the tuplify edit's offsets
        # were computed against the pre-fix bytes (the import removal shifts
        # every later offset by its own length).
        assert (project / "src" / "mod.py").read_text() == (
            "\n\ndef f():\n    xs = (1, 2, 3)\n    return xs[0]\n"
        )

    def test_only_the_combined_check_fix_transaction_reaches_the_project(
        self, tmp_path
    ):
        """The per-remedy planner transactions go to ``check_fixes``' scratch
        store; ``submit_intent`` passes the caller's ``tx_store`` through
        verbatim, and here that caller *is* the scratch store."""
        runner = CliRunner()
        project = _fix_project(
            tmp_path,
            runner,
            {"mod.py": self.SOURCE},
            rules='["unused-imports", "prefer-tuple"]',
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        tx_store = TransactionStore(project)
        assert tx_store.list() == [report["tx_id"]]
        header, _, _ = tx_store.load(report["tx_id"])
        assert header.operation == "check-fix"

    def test_overlapping_remedies_still_conflict_rather_than_drop(self, tmp_path):
        """The byte-range conflict model stayed in ``check_fixes``.

        The engine's model is footprint-level: handed both repairs at once it
        would serialize them and the loser would re-plan against mutated bytes
        or drop as a precondition failure. One ``fixes`` entry plus one
        ``skipped_conflicts`` entry — and an empty ``declined`` — is the proof
        that ``check --fix`` still submits them one at a time.
        """
        runner = CliRunner()
        _fix_project(
            tmp_path,
            runner,
            {"mod.py": "def _dead():\n    xs = [1, 2]\n    return xs[0]\n"},
            rules='["unused-public-symbol", "prefer-tuple"]',
            extra="[tool.pypeeker.unused-public-symbol]\nalso-private = true\n",
        )

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)
        report = json.loads(result.output)

        assert [fix["fix_id"] for fix in report["fixes"]] == [
            "unused-symbol:delete:mod:_dead"
        ]
        assert [s["fix_id"] for s in report["skipped_conflicts"]] == [
            "prefer-tuple:tuplify:mod:_dead:xs"
        ]
        assert report["declined"] == []

    def test_a_clean_project_writes_no_transaction_and_no_bytes(self, tmp_path):
        runner = CliRunner()
        project = _fix_project(
            tmp_path,
            runner,
            {"mod.py": "def f():\n    return 1\n"},
            rules='["unused-imports", "prefer-tuple"]',
        )
        before = _snapshot(project)

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)

        assert json.loads(result.output)["tx_id"] is None
        assert _snapshot(project) == before


# ---------------------------------------------------------------------------
# (6) the collapse costs a submit nothing it did not already pay
# ---------------------------------------------------------------------------


class TestNoWastedRebind:
    """A batch of one does not re-bind: nobody is left to read the result.

    ``run_batch``'s per-intent re-bind (parse + bind of every spliced file
    into the overlay) exists so the *next* intent plans against the previous
    one's output. ``submit_intent`` has no next intent and discards the
    overlay, so paying for it there is pure waste — and ``check --fix``
    submits one batch of one *per remedy* against the same store, which turned
    that waste into a re-parse of the whole (growing) file per fix. It opts
    out with ``rebind_final=False``.

    The opt-out is the *last* intent's re-bind only, and it moves nothing
    else: overlay bytes, the mutation record ``flatten_store`` diffs, the
    executed/dropped reports, and the splice verification are all unchanged.
    """

    FILES: ClassVar[dict[str, str]] = {
        "lib.py": "def helper():\n    return 1\n",
        "app.py": "from lib import helper\n\ndef use():\n    return helper()\n",
    }

    @staticmethod
    def _count_rebinds(monkeypatch) -> list[str]:
        """Record the paths ``run_batch``'s simulation loop re-binds."""
        from pypeeker.refactor import batch as batch_module

        rebound: list[str] = []
        real = batch_module.rebind_source

        def _spy(store, source_path, source, **kwargs):
            rebound.append(source_path)
            return real(store, source_path, source, **kwargs)

        monkeypatch.setattr(batch_module, "rebind_source", _spy)
        return rebound

    def test_submit_intent_rebinds_nothing(
        self, monkeypatch, indexed_project, transaction_store
    ):
        """The regression guard: zero parses of the files the splice touched."""
        _, store = indexed_project(self.FILES)
        rebound = self._count_rebinds(monkeypatch)

        result = submit_intent(
            RenameIntent("r1", "lib:helper", "assist"), store, transaction_store
        )

        assert rebound == []
        # ...and the submission itself is unaffected: the planner's own
        # summary and its edits still come back whole.
        assert result.summary is not None
        assert result.summary.new_name == "assist"
        assert {edit.file for edit in result.edits} == {"lib.py", "app.py"}

    def test_a_real_batch_still_rebinds_every_intermediate_state(
        self, monkeypatch, indexed_project, transaction_store
    ):
        """``submit_intents`` of two: the engine's default is untouched.

        Both intents' applies re-bind here — the first because the second
        plans against it, the second because ``rebind_final`` defaults to
        True for every caller that is not ``submit_intent``.
        """
        _, store = indexed_project(self.FILES)
        rebound = self._count_rebinds(monkeypatch)

        submit_intents(
            [
                RenameIntent("r1", "lib:helper", "assist"),
                RenameIntent("r2", "app:use", "consume"),
            ],
            store,
            transaction_store,
        )

        assert sorted(rebound) == ["app.py", "app.py", "lib.py"]

    def test_skipping_the_final_rebind_moves_no_simulated_byte(
        self, tmp_path, indexed_project, transaction_store
    ):
        """``rebind_final=False`` changes the index only — never the bytes.

        Two chained renames run both ways: the executed report, the overlay's
        mutation record, its final content, and the flattened transaction's
        edits must be identical, because the second rename still plans against
        the first's re-bound state either way.
        """
        from pypeeker.refactor import flatten_batch, run_batch

        def _run(*, rebind_final: bool) -> tuple[list, dict, list]:
            _, store = indexed_project(self.FILES)
            result = run_batch(
                [
                    RenameIntent("r1", "lib:helper", "assist"),
                    RenameIntent("r2", "app:use", "consume"),
                ],
                store,
                tx_store=TransactionStore(tmp_path / f"tx-{rebind_final}"),
                rebind_final=rebind_final,
            )
            content = {
                path: result.store.read_file(path)
                for path in result.store.overlaid_files()
            }
            _, edits = flatten_batch(result, store)
            return [i.intent.intent_id for i in result.executed], content, edits

        assert _run(rebind_final=True) == _run(rebind_final=False)

    def test_the_skipped_rebind_is_only_the_last_intents(
        self, monkeypatch, tmp_path, indexed_project, transaction_store
    ):
        """Under ``rebind_final=False`` a multi-intent batch still chains.

        Whatever is still pending re-binds; only the final apply is skipped.
        """
        from pypeeker.refactor import run_batch

        _, store = indexed_project(self.FILES)
        rebound = self._count_rebinds(monkeypatch)

        run_batch(
            [
                RenameIntent("r1", "lib:helper", "assist"),
                RenameIntent("r2", "app:use", "consume"),
            ],
            store,
            tx_store=TransactionStore(tmp_path / "tx"),
            rebind_final=False,
        )

        # The first rename's two files are re-bound (the second plans against
        # them); the second rename's single file is not.
        assert sorted(rebound) == ["app.py", "lib.py"]

    def test_the_splice_still_verifies_under_the_opt_out(
        self, tmp_path, indexed_project
    ):
        """Only the re-bind is skipped — the byte check that drops still runs.

        A materializer whose ``old`` text does not match the overlay bytes is
        a ``_SpliceMismatch`` drop, and that must stay true on the path
        ``submit_intent`` takes, or a refusal would silently become a success.
        """
        from pypeeker.models import EditEntry, EditOp
        from pypeeker.refactor import BatchPolicy, run_batch
        from pypeeker.refactor.batch import DropReason

        @dataclasses.dataclass(frozen=True)
        class _BadEditIntent(_SpyIntent):
            kind: ClassVar[str] = "test-only:bad-edit"

        @register_planner(_BadEditIntent.kind)
        def _materialize_bad(intent, store, tx_store):
            return Materialized(
                edits=[
                    EditEntry(
                        file="lib.py",
                        start=0,
                        end=3,
                        old="NOT THE BYTES",
                        new="xxx",
                        file_hash="deadbeef",
                        op=EditOp.REPLACE,
                    )
                ]
            )

        try:
            _, store = indexed_project(self.FILES)
            result = run_batch(
                [_BadEditIntent("bad-1")],
                store,
                tx_store=TransactionStore(tmp_path / "tx"),
                policy=BatchPolicy.SKIP_AND_REPORT,
                rebind_final=False,
            )
            assert result.executed == ()
            [drop] = result.dropped
            assert drop.reason is DropReason.PRECONDITION_FAILED
        finally:
            registry._REGISTRY.pop(_BadEditIntent.kind, None)
            _EFFECT_STORES.clear()


def test_index_store_is_not_an_overlay_sanity(tmp_path):
    """Guards the discriminator the ``TestNoFastPath`` assertions rely on."""
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True)
    assert not isinstance(IndexStore(tmp_path), OverlayIndexStore)
