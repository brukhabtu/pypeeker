"""Tests for the rename and apply CLI commands.

TASK-126: mutating commands apply by default; --plan writes the PENDING
transaction without applying (today's old plan-only shape).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a project directory with source files and pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


def test_rename_help():
    runner = CliRunner()
    result = runner.invoke(main, ["rename", "--help"])
    assert result.exit_code == 0
    assert "Rename a symbol" in result.output
    assert "SYMBOL_ID" in result.output
    assert "NEW_NAME" in result.output
    assert "--plan" in result.output


def test_apply_help():
    runner = CliRunner()
    result = runner.invoke(main, ["apply", "--help"])
    assert result.exit_code == 0
    assert "Apply a PENDING transaction" in result.output
    assert "TX_ID" in result.output


def test_full_rename_workflow_applies_by_default(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": "def greet():\n    pass\n\ngreet()\n"
    })
    runner = CliRunner()
    os.chdir(project)

    result = runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    assert result.exit_code == 0

    result = runner.invoke(
        main, ["rename", "test:greet", "hello"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "tx_id" in output
    assert output["old_name"] == "greet"
    assert output["new_name"] == "hello"
    assert output["edit_count"] == 2
    assert output["applied"] is True
    # The applier's own result is merged in, not collapsed to the bool: the
    # default path must report what it did to the index, exactly like the
    # separate 'apply' call it replaced.
    assert output["files_modified"] == ["test.py"]
    assert output["files_reindexed"] == ["test.py"]
    assert output["files_reindex_failed"] == []

    # Bytes actually changed on disk; no separate 'apply' call was needed.
    content = (project / "test.py").read_text()
    assert "def hello(" in content
    assert "hello()" in content
    assert "greet" not in content

    # The transaction itself is recorded APPLIED.
    result = runner.invoke(
        main, ["transactions", "show", output["tx_id"]], catch_exceptions=False
    )
    assert result.exit_code == 0
    shown = json.loads(result.output)
    assert shown["header"]["status"] == "applied"

    # Rollback restores the original bytes.
    result = runner.invoke(
        main, ["rollback", output["tx_id"]], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert (project / "test.py").read_text() == "def greet():\n    pass\n\ngreet()\n"


def test_rename_plan_leaves_transaction_pending_and_tree_untouched(tmp_path):
    project = _make_project(tmp_path, {
        "test.py": "def greet():\n    pass\n\ngreet()\n"
    })
    runner = CliRunner()
    os.chdir(project)
    before = (project / "test.py").read_text()

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(
        main, ["rename", "test:greet", "hello", "--plan"], catch_exceptions=False
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "applied" not in output
    # ...and with it, every key that only an apply can produce.
    assert "files_reindex_failed" not in output
    assert output["old_name"] == "greet"
    assert output["new_name"] == "hello"
    tx_id = output["tx_id"]

    # Plan-only: the real tree is untouched...
    assert (project / "test.py").read_text() == before

    # ...and the persisted transaction is inspectable and PENDING.
    result = runner.invoke(
        main, ["transactions", "show", tx_id], catch_exceptions=False
    )
    shown = json.loads(result.output)
    assert shown["header"]["status"] == "pending"

    # A later manual apply lands the same edits the default path would have.
    result = runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
    assert result.exit_code == 0
    apply_output = json.loads(result.output)
    assert apply_output["status"] == "applied"
    assert "test.py" in apply_output["files_modified"]
    content = (project / "test.py").read_text()
    assert "def hello(" in content
    assert "greet" not in content


def test_rename_plan_json_output_matches_old_plan_only_shape(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(
        main, ["rename", "test:foo", "bar", "--plan"], catch_exceptions=False
    )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "tx_id" in output
    assert "operation" in output
    assert output["operation"] == "rename"
    assert "symbol_id" in output
    assert "old_name" in output
    assert "new_name" in output
    assert "files_affected" in output
    assert "edit_count" in output
    assert "created_at" in output
    assert "applied" not in output


def test_apply_json_output(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    plan_result = runner.invoke(
        main, ["rename", "test:foo", "bar", "--plan"], catch_exceptions=False
    )
    tx_id = json.loads(plan_result.output)["tx_id"]

    result = runner.invoke(main, ["apply", tx_id], catch_exceptions=False)
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "tx_id" in output
    assert "status" in output
    assert output["status"] == "applied"
    assert "files_modified" in output
    assert "files_reindexed" in output


def test_rename_error_not_found(tmp_path):
    project = _make_project(tmp_path, {"test.py": "x = 1\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(main, ["rename", "nonexistent", "bar"])

    assert result.exit_code != 0
    output = json.loads(result.output)
    assert "error" in output
    assert "not found" in output["error"]
    # A refusal-to-plan never reaches the apply step: no transaction left.
    assert "tx_id" not in output


def test_apply_error_not_found(tmp_path):
    project = _make_project(tmp_path, {})
    runner = CliRunner()
    os.chdir(project)

    result = runner.invoke(main, ["apply", "nonexistent_tx"])
    assert result.exit_code != 0
    output = json.loads(result.output)
    assert "error" in output
    assert "not found" in output["error"]


def test_rename_plan_with_flags(tmp_path):
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)

    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    result = runner.invoke(
        main,
        ["rename", "test:foo", "bar", "--include-file", "--include-exports", "--plan"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    # Flags are recorded but not acted on in v1
    # Just verify the plan succeeds


def test_commands_appear_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "rename" in result.output
    assert "apply" in result.output


def test_apply_failure_after_successful_plan_emits_the_apply_failed_envelope(
    tmp_path, monkeypatch
):
    # TASK-126 grammar item 3: when the apply half of a default (non-
    # --plan) mutating command fails, the command emits the same
    # "apply-failed" envelope the standalone 'apply' command would (with
    # tx_id), exits 1, and does not graft an "applied" key onto the report.
    # Every mutating command shares that envelope via cli.py's
    # _finish_mutation, so exercising it once here (through 'rename') covers
    # rename/inline-variable/extract-variable/extract-method/demote/
    # promote/batch/privatize identically.
    #
    # The transaction's resulting STATUS is NOT part of what this test
    # generalizes: the stub below raises before touching anything, which is
    # the pre-flight shape (status stays PENDING). The mid-apply shape —
    # what a real default-path failure produces, and which marks the
    # transaction FAILED — is pinned separately below.
    from pypeeker.refactor import ApplyError

    class _FailingApplier:
        def __init__(self, *args, **kwargs):
            pass

        def apply(self, tx_id):
            raise ApplyError("integrity check failed")

    monkeypatch.setattr("pypeeker.refactor.TransactionApplier", _FailingApplier)

    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)

    result = runner.invoke(main, ["rename", "test:foo", "bar"])
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output["error"] == "integrity check failed"
    assert output["code"] == "apply-failed"
    assert "tx_id" in output
    assert "applied" not in output

    from pypeeker.storage import TransactionStore

    header, _, _ = TransactionStore(project).load(output["tx_id"])
    assert header.status.value == "pending"


def test_preflight_apply_failure_leaves_transaction_pending_and_reappliable(tmp_path):
    # Pre-flight half of the documented contract: the file changed between
    # --plan and apply, so hash verification refuses BEFORE anything is
    # written. Nothing was touched, so the transaction stays PENDING.
    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)

    planned = runner.invoke(
        main, ["rename", "test:foo", "bar", "--plan"], catch_exceptions=False
    )
    tx_id = json.loads(planned.output)["tx_id"]
    (project / "test.py").write_text("def foo(): pass\n# edited behind our back\n")

    result = runner.invoke(main, ["apply", tx_id])
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output["code"] == "apply-failed"
    # The recovery instruction must not name a command that no longer exists
    # (TASK-126 deleted the plan-* grammar).
    assert "plan-rename" not in output["error"]
    assert "modified since plan was created" in output["error"]

    from pypeeker.storage import TransactionStore

    header, _, _ = TransactionStore(project).load(tx_id)
    assert header.status.value == "pending"
    # PENDING is genuinely re-appliable: undo the external edit and the very
    # same transaction applies.
    (project / "test.py").write_text("def foo(): pass\n")
    assert runner.invoke(main, ["apply", tx_id]).exit_code == 0


def test_mid_apply_failure_marks_transaction_failed_and_is_terminal(
    tmp_path, monkeypatch
):
    # The other half, and the one the DEFAULT path actually produces: plan
    # and apply happen microseconds apart in one process, so a pre-flight
    # hash conflict is near-unreachable and a mid-apply I/O error is the
    # realistic failure. TransactionApplier restores the bytes and marks the
    # transaction FAILED, which is terminal — apply/rollback/cancel all
    # refuse it. Pinned here because the docs previously claimed the
    # opposite (that the transaction stays PENDING and stays re-appliable).
    real_write_bytes = Path.write_bytes

    def _fail_on_temp_write(self, data):
        if self.suffix == ".tmp":
            raise OSError(28, "No space left on device")
        return real_write_bytes(self, data)

    project = _make_project(tmp_path, {"test.py": "def foo(): pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    monkeypatch.setattr(Path, "write_bytes", _fail_on_temp_write)

    result = runner.invoke(main, ["rename", "test:foo", "bar"])
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output["code"] == "apply-failed"
    assert "No space left on device" in output["error"]
    assert "applied" not in output

    monkeypatch.undo()
    # Files are intact — the failed apply rolled itself back.
    assert (project / "test.py").read_text() == "def foo(): pass\n"

    from pypeeker.storage import TransactionStore

    header, _, _ = TransactionStore(project).load(output["tx_id"])
    assert header.status.value == "failed"

    # Terminal: every recovery route refuses a FAILED transaction.
    for argv, refusal in (
        (["apply", output["tx_id"]], "is not pending"),
        (["rollback", output["tx_id"]], "is not applied"),
        (["transactions", "cancel", output["tx_id"]], "pending"),
    ):
        refused = runner.invoke(main, argv)
        assert refused.exit_code != 0, argv
        assert refusal in refused.output.lower(), (argv, refused.output)

    # The documented recovery is to re-run the command: nothing was written,
    # so re-planning is safe and lands a fresh transaction.
    rerun = runner.invoke(main, ["rename", "test:foo", "bar"], catch_exceptions=False)
    assert rerun.exit_code == 0
    assert json.loads(rerun.output)["applied"] is True
    assert "def bar(" in (project / "test.py").read_text()


def test_default_apply_reports_reindex_failures_instead_of_swallowing_them(
    tmp_path, monkeypatch
):
    # The edits land but the post-apply re-index of a file fails: that does
    # not raise (the refactoring itself succeeded), so the ONLY way a driver
    # can learn the on-disk index is now stale for that file is the payload.
    # Under apply-by-default the next mutating command plans off that stale
    # entry and writes immediately, so this must never be swallowed.
    def _boom(*args, **kwargs):
        raise ValueError("bind exploded")

    project = _make_project(tmp_path, {"test.py": "def greet():\n    pass\n"})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", str(project / "test.py")], catch_exceptions=False)
    monkeypatch.setattr("pypeeker.refactor.applier.bind", _boom)

    result = runner.invoke(main, ["rename", "test:greet", "hello"])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["applied"] is True
    assert output["files_reindexed"] == []
    assert output["files_reindex_failed"] == [
        {"file": "test.py", "error": "bind exploded"}
    ]
    assert "def hello(" in (project / "test.py").read_text()
