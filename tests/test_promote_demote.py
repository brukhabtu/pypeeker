"""Tests for the promote/demote visibility operations (CLI + planner)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main
from pypeeker.refactor.visibility_ops import (
    DemoteError,
    PromoteError,
    VisibilityPlanner,
)
from pypeeker.storage import TransactionStore

APP_PYPROJECT = '[project]\nname = "test"\n'
LIBRARY_PYPROJECT = (
    '[project]\nname = "test"\n\n[tool.pypeeker.visibility]\nmode = "library"\n'
)

BARREL_FILES = {
    "pkg/__init__.py": "from pkg.mod import helper\n",
    "pkg/mod.py": "def helper():\n    return 1\n",
    "app.py": "from pkg import helper\n\nhelper()\n",
}


def _cli_project(
    tmp_path: Path,
    monkeypatch,
    files: dict[str, str],
    pyproject: str = APP_PYPROJECT,
) -> tuple[Path, CliRunner]:
    """Create, chdir into, and index a project; return (project, runner)."""
    (tmp_path / "pyproject.toml").write_text(pyproject)
    (tmp_path / ".semantic-tool" / "index").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["index", str(tmp_path)], catch_exceptions=False)
    assert result.exit_code == 0
    return tmp_path, runner


def _invoke_ok(runner: CliRunner, args: list[str]) -> dict:
    result = runner.invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _invoke_refused(runner: CliRunner, args: list[str]) -> dict:
    result = runner.invoke(main, args)
    assert result.exit_code == 1, result.output
    output = json.loads(result.output)
    assert "error" in output
    return output


# ---------------------------------------------------------------------------
# Demote
# ---------------------------------------------------------------------------


def test_demote_renames_references_and_barrel_with_apply_rollback(
    tmp_path, monkeypatch
):
    project, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES)
    originals = {name: (project / name).read_text() for name in BARREL_FILES}

    output = _invoke_ok(runner, ["demote", "pkg.mod:helper"])
    assert output["operation"] == "demote"
    assert output["old_name"] == "helper"
    assert output["new_name"] == "_helper"
    assert sorted(output["files_affected"]) == sorted(BARREL_FILES)
    # The barrel export was rewritten: the summary must warn about it.
    assert any("barrel-exported" in w for w in output["warnings"])

    apply_out = _invoke_ok(runner, ["apply", output["tx_id"]])
    assert apply_out["status"] == "applied"
    assert apply_out["files_reindex_failed"] == []
    assert (project / "pkg/mod.py").read_text() == "def _helper():\n    return 1\n"
    assert (
        project / "pkg/__init__.py"
    ).read_text() == "from pkg.mod import _helper\n"
    assert (project / "app.py").read_text() == "from pkg import _helper\n\n_helper()\n"

    # Round-trip: rollback restores every file byte-for-byte.
    rollback_out = _invoke_ok(runner, ["rollback", output["tx_id"]])
    assert rollback_out["status"] == "rolled_back"
    for name, content in originals.items():
        assert (project / name).read_text() == content


def test_demote_transaction_header_records_operation(tmp_path, monkeypatch):
    project, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES)
    output = _invoke_ok(runner, ["demote", "pkg.mod:helper"])

    header, _, _ = TransactionStore(project).load(output["tx_id"])
    assert header.operation == "demote"
    assert header.status.value == "pending"

    listed = _invoke_ok(runner, ["transactions", "list"])
    assert [tx["operation"] for tx in listed] == ["demote"]


def test_demote_keep_export_aliases_the_reexport(tmp_path, monkeypatch):
    project, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES)
    output = _invoke_ok(runner, ["demote", "pkg.mod:helper", "--keep-export"])
    assert output["operation"] == "demote"
    assert "warnings" not in output  # public surface preserved: nothing to warn

    _invoke_ok(runner, ["apply", output["tx_id"]])
    assert (project / "pkg/mod.py").read_text() == "def _helper():\n    return 1\n"
    assert (
        project / "pkg/__init__.py"
    ).read_text() == "from pkg.mod import _helper as helper\n"
    # Barrel consumers keep using the public name untouched.
    assert (project / "app.py").read_text() == BARREL_FILES["app.py"]


def test_demote_refused_for_override_method(tmp_path, monkeypatch):
    files = {
        "mod.py": (
            "class Base:\n"
            "    def run(self):\n"
            "        pass\n"
            "\n"
            "\n"
            "class Sub(Base):\n"
            "    def run(self):\n"
            "        pass\n"
        )
    }
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_refused(runner, ["demote", "mod:Sub.run"])
    assert output["code"] == "rename-refused"
    assert "overrides" in output["error"]


def test_demote_refused_for_library_mode_public_root(tmp_path, monkeypatch):
    _, runner = _cli_project(
        tmp_path, monkeypatch, BARREL_FILES, pyproject=LIBRARY_PYPROJECT
    )
    output = _invoke_refused(runner, ["demote", "pkg.mod:helper"])
    assert output["code"] == "protected-public-api"
    assert "protected public API (library mode)" in output["error"]


def test_demote_allowed_in_library_mode_outside_public_roots(tmp_path, monkeypatch):
    pyproject = (
        '[project]\nname = "test"\n\n[tool.pypeeker.visibility]\n'
        'mode = "library"\npublic-roots = ["other"]\n'
    )
    _, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES, pyproject=pyproject)
    output = _invoke_ok(runner, ["demote", "pkg.mod:helper"])
    assert output["operation"] == "demote"


def test_demote_refused_for_existing_underscore_name(tmp_path, monkeypatch):
    files = {"mod.py": "def helper():\n    pass\n\n\ndef _helper():\n    pass\n"}
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_refused(runner, ["demote", "mod:helper"])
    assert output["code"] == "rename-refused"
    assert "Name conflict" in output["error"]
    assert "_helper" in output["error"]


def test_demote_refused_for_already_private_name(tmp_path, monkeypatch):
    files = {"mod.py": "def _quiet():\n    pass\n"}
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_refused(runner, ["demote", "mod:_quiet"])
    assert output["code"] == "already-private"


def test_demote_refused_for_unknown_symbol(tmp_path, monkeypatch):
    _, runner = _cli_project(tmp_path, monkeypatch, {"mod.py": "x = 1\n"})
    output = _invoke_refused(runner, ["demote", "nonexistent"])
    assert output["code"] == "not-found"


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


def test_promote_strips_underscore_and_renames_references(tmp_path, monkeypatch):
    files = {"mod.py": "def _solo():\n    pass\n\n\n_solo()\n"}
    project, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["promote", "mod:_solo"])
    assert output["operation"] == "promote"
    assert output["old_name"] == "_solo"
    assert output["new_name"] == "solo"

    _invoke_ok(runner, ["apply", output["tx_id"]])
    assert (project / "mod.py").read_text() == "def solo():\n    pass\n\n\nsolo()\n"


def test_promote_add_export_writes_import_and_dunder_all(tmp_path, monkeypatch):
    files = {
        "pkg/__init__.py": 'from pkg.other import x\n\n__all__ = ["x"]\n',
        "pkg/other.py": "x = 1\n",
        "pkg/mod.py": (
            "def _helper():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def use():\n"
            "    return _helper()\n"
        ),
    }
    project, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["promote", "pkg.mod:_helper", "--add-export", "pkg"])
    assert output["operation"] == "promote"
    assert "pkg/__init__.py" in output["files_affected"]

    apply_out = _invoke_ok(runner, ["apply", output["tx_id"]])
    assert apply_out["files_reindex_failed"] == []
    assert (project / "pkg/mod.py").read_text() == (
        "def helper():\n    return 1\n\n\ndef use():\n    return helper()\n"
    )
    assert (project / "pkg/__init__.py").read_text() == (
        "from pkg.other import x\n"
        "from .mod import helper\n"
        "\n"
        '__all__ = ["helper", "x"]\n'
    )


def test_promote_add_export_without_dunder_all(tmp_path, monkeypatch):
    files = {
        "pkg/__init__.py": "from pkg.other import x\n",
        "pkg/other.py": "x = 1\n",
        "pkg/mod.py": "def _helper():\n    return 1\n",
    }
    project, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["promote", "pkg.mod:_helper", "--add-export", "pkg"])
    _invoke_ok(runner, ["apply", output["tx_id"]])
    assert (project / "pkg/__init__.py").read_text() == (
        "from pkg.other import x\nfrom .mod import helper\n"
    )


def test_promote_rewrites_existing_barrel_export_of_private_name(
    tmp_path, monkeypatch
):
    files = {
        "pkg/__init__.py": "from pkg.mod import _helper\n",
        "pkg/mod.py": "def _helper():\n    return 1\n",
    }
    project, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["promote", "pkg.mod:_helper"])
    assert any("barrel-exported" in w for w in output["warnings"])
    _invoke_ok(runner, ["apply", output["tx_id"]])
    assert (
        project / "pkg/__init__.py"
    ).read_text() == "from pkg.mod import helper\n"


def test_promote_refused_for_dunder(tmp_path, monkeypatch):
    files = {"mod.py": "class C:\n    def __call__(self):\n        pass\n"}
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_refused(runner, ["promote", "mod:C.__call__"])
    assert output["code"] == "dunder"


def test_promote_refused_for_public_name(tmp_path, monkeypatch):
    _, runner = _cli_project(
        tmp_path, monkeypatch, {"mod.py": "def loud():\n    pass\n"}
    )
    output = _invoke_refused(runner, ["promote", "mod:loud"])
    assert output["code"] == "already-public"


def test_promote_refused_for_unknown_export_package(tmp_path, monkeypatch):
    project, runner = _cli_project(
        tmp_path, monkeypatch, {"mod.py": "def _quiet():\n    pass\n"}
    )
    output = _invoke_refused(
        runner, ["promote", "mod:_quiet", "--add-export", "nosuch"]
    )
    assert output["code"] == "export-target"
    # The refusal happened before planning: no transaction was left behind.
    assert TransactionStore(project).list() == []


def test_promote_refused_when_export_name_already_bound(tmp_path, monkeypatch):
    files = {
        "pkg/__init__.py": "helper = 1\n",
        "pkg/mod.py": "def _helper():\n    return 2\n",
    }
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_refused(
        runner, ["promote", "pkg.mod:_helper", "--add-export", "pkg"]
    )
    assert output["code"] == "export-target"
    assert "already binds" in output["error"]


# ---------------------------------------------------------------------------
# Direct planner tests
# ---------------------------------------------------------------------------


def test_planner_demote_summary_and_persisted_header(
    indexed_project, transaction_store
):
    _, store = indexed_project({"mod.py": "def helper():\n    pass\n\nhelper()\n"})
    planner = VisibilityPlanner(store, transaction_store)
    result = planner.plan_demote("mod:helper")

    assert result.summary.operation == "demote"
    assert result.summary.new_name == "_helper"
    assert result.warnings == []  # not barrel-exported: nothing to warn about
    header, edits, file_rename = transaction_store.load(result.summary.tx_id)
    assert header.operation == "demote"
    assert len(edits) == result.summary.edit_count
    assert file_rename is None


def test_planner_promote_refuses_override_method(
    indexed_project, transaction_store
):
    src = (
        "class Base:\n"
        "    def _run(self):\n"
        "        pass\n"
        "\n"
        "\n"
        "class Sub(Base):\n"
        "    def _run(self):\n"
        "        pass\n"
    )
    _, store = indexed_project({"mod.py": src})
    planner = VisibilityPlanner(store, transaction_store)
    try:
        planner.plan_promote("mod:Sub._run")
    except PromoteError as e:
        assert e.code == "rename-refused"
        assert "overrides" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected PromoteError")


def test_planner_demote_refuses_underscore_prefixed(
    indexed_project, transaction_store
):
    _, store = indexed_project({"mod.py": "def _quiet():\n    pass\n"})
    planner = VisibilityPlanner(store, transaction_store)
    try:
        planner.plan_demote("mod:_quiet")
    except DemoteError as e:
        assert e.code == "already-private"
    else:  # pragma: no cover
        raise AssertionError("expected DemoteError")


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_demote_and_promote_appear_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "demote" in result.output
    assert "promote" in result.output


def test_command_help_documents_refusal_classes():
    runner = CliRunner()
    demote_help = runner.invoke(main, ["demote", "--help"]).output
    assert "already-private" in demote_help
    assert "protected-public-api" in demote_help
    assert "rename-refused" in demote_help
    promote_help = runner.invoke(main, ["promote", "--help"]).output
    assert "already-public" in promote_help
    assert "dunder" in promote_help
    assert "export-target" in promote_help
