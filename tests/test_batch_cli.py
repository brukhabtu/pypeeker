"""End-to-end tests for the batch CLI command (TASK-89, regrammared TASK-126).

``batch`` consumes a JSON intents file, simulates the batch against a
mirror, flattens the net change into ONE transaction, and — like every
other mutating command since TASK-126 — plans AND applies it immediately
unless ``--plan`` is given. Tests drive the real CLI over tmp projects,
including the AC case where naively pre-planned sequential transactions
would go stale where the batch lands.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main

LIB = "def helper():\n    return 1\n"
APP = (
    "import os\n"
    "from lib import helper\n"
    "\n"
    "def use():\n"
    "    x = helper()\n"
    "    return x\n"
)

PYPROJECT = (
    '[project]\nname = "test"\n'
    "[tool.pypeeker]\n"
    'src = ["src"]\n'
    'rules = ["unused-imports"]\n'
)


def _project(tmp_path: Path, monkeypatch, files: dict[str, str]) -> CliRunner:
    """A cwd'd tmp project with ``files`` under src/, indexed via the CLI."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    for name, content in files.items():
        path = tmp_path / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main, ["index", str(tmp_path / "src")], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return runner


def _write_intents(tmp_path: Path, intents: list[dict]) -> str:
    """Write an intents JSON file and return its path as a string."""
    path = tmp_path / "intents.json"
    path.write_text(json.dumps(intents))
    return str(path)


def _invoke(runner: CliRunner, args: list[str]) -> tuple[int, dict | list]:
    """Invoke the CLI and parse its JSON output."""
    result = runner.invoke(main, args, catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


REWRITE_INTENTS = [
    {
        "kind": "rename",
        "id": "rename-helper",
        "symbol_id": "lib:helper",
        "new_name": "assist",
    },
    {
        "kind": "inline-variable",
        "id": "inline-x",
        "symbol_id": "app:use:x",
        # Dep on the fix ENTRY id: resolved through the sweep's expansion
        # to the generated fix intent ids.
        "deps": ["drop-unused"],
    },
    {"kind": "fix", "id": "drop-unused", "rule": "unused-imports"},
]


class TestBatchEndToEnd:
    def test_batch_applies_by_default(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})
        intents = _write_intents(tmp_path, REWRITE_INTENTS)

        code, output = _invoke(runner, ["batch", intents])
        assert code == 0, output
        assert output["dropped"] == []
        executed = {e["id"]: e["kind"] for e in output["executed"]}
        assert executed == {
            "rename-helper": "rename",
            "inline-x": "inline-variable",
            # The sweep expanded to one remedy intent, reported under the
            # kind of the repair the rule proposed (TASK-124: the opaque
            # "edit" kind of the old fix protocol is gone).
            "drop-unused-1": "remove-import",
        }
        assert output["files_affected"] == ["src/app.py", "src/lib.py"]
        assert output["edit_count"] == 2
        assert output["tx_id"]
        assert output["applied"] is True

        # Bytes actually changed on disk without a separate 'apply' call:
        # the rename landed on the post-inline call site and the unused
        # import is gone.
        assert (tmp_path / "src" / "lib.py").read_text() == (
            "def assist():\n    return 1\n"
        )
        assert (tmp_path / "src" / "app.py").read_text() == (
            "from lib import assist\n\ndef use():\n    return assist()\n"
        )

        # The transaction is recorded APPLIED.
        code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
        assert code == 0, shown
        assert shown["header"]["status"] == "applied"

    def test_batch_plan_leaves_transaction_pending_and_tree_untouched(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})
        intents = _write_intents(tmp_path, REWRITE_INTENTS)

        code, output = _invoke(runner, ["batch", intents, "--plan"])
        assert code == 0, output
        assert "applied" not in output
        assert output["dropped"] == []
        assert output["tx_id"]

        # Plan-only: the real tree is untouched...
        assert (tmp_path / "src" / "lib.py").read_text() == LIB
        assert (tmp_path / "src" / "app.py").read_text() == APP

        # ...and the persisted transaction is inspectable and PENDING.
        code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
        assert code == 0, shown
        assert shown["header"]["status"] == "pending"

        # A later manual apply lands the same edits the default path would
        # have.
        code, applied = _invoke(runner, ["apply", output["tx_id"]])
        assert code == 0, applied
        assert applied["status"] == "applied"
        assert (tmp_path / "src" / "lib.py").read_text() == (
            "def assist():\n    return 1\n"
        )

    def test_rollback_restores_originals_byte_identically(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})
        intents = _write_intents(
            tmp_path,
            [
                {"kind": "rename", "symbol_id": "lib:helper", "new_name": "assist"},
                {"kind": "fix", "rule": "unused-imports"},
            ],
        )
        code, output = _invoke(runner, ["batch", intents])
        assert code == 0, output
        assert output["applied"] is True

        code, rolled = _invoke(runner, ["rollback", output["tx_id"]])
        assert code == 0, rolled
        assert rolled["status"] == "rolled_back"
        assert (tmp_path / "src" / "lib.py").read_bytes() == LIB.encode()
        assert (tmp_path / "src" / "app.py").read_bytes() == APP.encode()

    def test_batch_lands_where_naive_sequential_transactions_go_stale(
        self, tmp_path, monkeypatch
    ):
        # The classic corruption shape: a rename changes byte offsets that a
        # SECOND pre-planned transaction's edits were anchored to. Naively
        # planning both against the original tree and applying sequentially
        # must refuse (the applier's hash guard catches the staleness —
        # without it the inline's offsets would splice the wrong bytes);
        # 'batch' lands the same pair because the inline re-plans against
        # the post-rename state inside the simulation.
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})

        # The rename to a longer name shifts every offset in app.py that the
        # pre-planned inline transaction recorded. Both are planned with
        # --plan so they can be applied out of band, one at a time.
        code, rename_tx = _invoke(
            runner,
            ["rename", "lib:helper", "much_longer_helper_name", "--plan"],
        )
        assert code == 0, rename_tx
        code, inline_tx = _invoke(
            runner, ["inline-variable", "app:use:x", "--plan"]
        )
        assert code == 0, inline_tx

        code, _ = _invoke(runner, ["apply", rename_tx["tx_id"]])
        assert code == 0
        code, refused = _invoke(runner, ["apply", inline_tx["tx_id"]])
        assert code == 1
        assert "has been modified" in refused["error"]

        # Restore the original tree, then land the same pair as a batch
        # (default grammar: plans AND applies in one call).
        code, _ = _invoke(runner, ["rollback", rename_tx["tx_id"]])
        assert code == 0
        intents = _write_intents(
            tmp_path,
            [
                {
                    "kind": "rename",
                    "symbol_id": "lib:helper",
                    "new_name": "much_longer_helper_name",
                },
                {"kind": "inline-variable", "symbol_id": "app:use:x"},
            ],
        )
        code, output = _invoke(runner, ["batch", intents])
        assert code == 0, output
        assert output["dropped"] == []
        assert output["applied"] is True
        assert (tmp_path / "src" / "app.py").read_text() == (
            "import os\n"
            "from lib import much_longer_helper_name\n"
            "\n"
            "def use():\n"
            "    return much_longer_helper_name()\n"
        )


class TestBatchReporting:
    def test_dropped_intents_are_reported_with_reasons(
        self, tmp_path, monkeypatch
    ):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})
        intents = _write_intents(
            tmp_path,
            [
                {"kind": "rename", "id": "r1", "symbol_id": "lib:helper", "new_name": "assist"},
                {"kind": "rename", "id": "r2", "symbol_id": "lib:helper", "new_name": "do_help"},
            ],
        )
        code, output = _invoke(runner, ["batch", intents, "--plan"])
        assert code == 0, output
        assert [e["id"] for e in output["executed"]] == ["r1"]
        (drop,) = output["dropped"]
        assert drop["id"] == "r2"
        assert drop["reason"] == "conflict-dropped"
        assert "lib:helper" in drop["detail"]

    def test_policy_abort_exits_one_with_error_shape(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})
        intents = _write_intents(
            tmp_path,
            [
                {"kind": "rename", "id": "r1", "symbol_id": "lib:helper", "new_name": "assist"},
                {"kind": "rename", "id": "r2", "symbol_id": "lib:helper", "new_name": "do_help"},
            ],
        )
        code, output = _invoke(
            runner, ["batch", intents, "--policy", "abort"]
        )
        assert code == 1
        assert "aborted" in output["error"]
        assert output["dropped"][0]["id"] == "r2"
        # Nothing was persisted or touched.
        assert (tmp_path / "src" / "lib.py").read_text() == LIB

    def test_all_intents_dropped_exits_one(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB, "app.py": APP})
        intents = _write_intents(
            tmp_path,
            [{"kind": "inline-variable", "symbol_id": "app:use:ghost"}],
        )
        code, output = _invoke(runner, ["batch", intents])
        assert code == 1
        assert output["error"] == "all intents were dropped"
        assert output["dropped"][0]["reason"] == "precondition-failed"


class TestBatchInputErrors:
    def test_invalid_json_is_an_error(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB})
        bad = tmp_path / "intents.json"
        bad.write_text("not json {")
        code, output = _invoke(runner, ["batch", str(bad)])
        assert code == 1
        assert "not valid JSON" in output["error"]

    def test_missing_file_is_an_error(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB})
        code, output = _invoke(runner, ["batch", str(tmp_path / "ghost.json")])
        assert code == 1
        assert "cannot read intents file" in output["error"]

    def test_unknown_kind_is_an_error(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB})
        intents = _write_intents(tmp_path, [{"kind": "teleport", "id": "t"}])
        code, output = _invoke(runner, ["batch", intents])
        assert code == 1
        assert "unknown kind" in output["error"]

    def test_missing_required_param_names_the_entry(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB})
        intents = _write_intents(tmp_path, [{"kind": "rename", "new_name": "x"}])
        code, output = _invoke(runner, ["batch", intents])
        assert code == 1
        assert "intent #1" in output["error"]
        assert "symbol_id" in output["error"]

    def test_non_list_top_level_is_an_error(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB})
        bad = tmp_path / "intents.json"
        bad.write_text('{"kind": "rename"}')
        code, output = _invoke(runner, ["batch", str(bad)])
        assert code == 1
        assert "JSON list" in output["error"]

    def test_empty_intents_list_is_an_error(self, tmp_path, monkeypatch):
        runner = _project(tmp_path, monkeypatch, {"lib.py": LIB})
        intents = _write_intents(tmp_path, [])
        code, output = _invoke(runner, ["batch", intents])
        assert code == 1
        assert "no executable intents" in output["error"]
