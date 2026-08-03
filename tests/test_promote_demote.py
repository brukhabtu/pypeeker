"""Tests for the promote/demote visibility operations (CLI + planner)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from pypeeker.cli import main
from pypeeker.refactor.visibility_ops import (
    _DemoteError as DemoteError,
    _PromoteError as PromoteError,
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
    (tmp_path / ".pypeeker" / "index").mkdir(parents=True, exist_ok=True)
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


def test_demote_applies_by_default_renames_references_and_barrel(
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
    assert output["applied"] is True
    # The applier's own result is merged into the default path's payload, so
    # a post-apply re-index failure stays visible instead of being swallowed.
    assert output["files_reindex_failed"] == []
    assert sorted(output["files_modified"]) == sorted(BARREL_FILES)

    # Bytes actually changed on disk without a separate 'apply' call.
    assert (project / "pkg/mod.py").read_text() == "def _helper():\n    return 1\n"
    assert (
        project / "pkg/__init__.py"
    ).read_text() == "from pkg.mod import _helper\n"
    assert (project / "app.py").read_text() == "from pkg import _helper\n\n_helper()\n"

    # The transaction is recorded APPLIED.
    header = TransactionStore(project).load(output["tx_id"]).header
    assert header.status.value == "applied"

    # Round-trip: rollback restores every file byte-for-byte.
    rollback_out = _invoke_ok(runner, ["rollback", output["tx_id"]])
    assert rollback_out["status"] == "rolled_back"
    for name, content in originals.items():
        assert (project / name).read_text() == content


def test_demote_plan_leaves_transaction_pending_and_tree_untouched(
    tmp_path, monkeypatch
):
    project, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES)
    originals = {name: (project / name).read_text() for name in BARREL_FILES}

    output = _invoke_ok(runner, ["demote", "pkg.mod:helper", "--plan"])
    assert output["operation"] == "demote"
    assert output["old_name"] == "helper"
    assert output["new_name"] == "_helper"
    assert sorted(output["files_affected"]) == sorted(BARREL_FILES)
    assert any("barrel-exported" in w for w in output["warnings"])
    assert "applied" not in output

    # Plan-only: the real tree is untouched...
    for name, content in originals.items():
        assert (project / name).read_text() == content

    # ...and the persisted transaction is inspectable and PENDING.
    header = TransactionStore(project).load(output["tx_id"]).header
    assert header.operation == "demote"
    assert header.status.value == "pending"

    # A later manual apply lands the same edits the default path would have.
    apply_out = _invoke_ok(runner, ["apply", output["tx_id"]])
    assert apply_out["status"] == "applied"
    assert apply_out["files_reindex_failed"] == []
    assert (project / "pkg/mod.py").read_text() == "def _helper():\n    return 1\n"
    assert (
        project / "pkg/__init__.py"
    ).read_text() == "from pkg.mod import _helper\n"
    assert (project / "app.py").read_text() == "from pkg import _helper\n\n_helper()\n"


def test_demote_transaction_header_records_operation(tmp_path, monkeypatch):
    project, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES)
    output = _invoke_ok(runner, ["demote", "pkg.mod:helper", "--plan"])

    header = TransactionStore(project).load(output["tx_id"]).header
    assert header.operation == "demote"
    assert header.status.value == "pending"

    listed = _invoke_ok(runner, ["transactions", "list"])
    assert [tx["operation"] for tx in listed] == ["demote"]


def test_demote_keep_export_aliases_the_reexport(tmp_path, monkeypatch):
    project, runner = _cli_project(tmp_path, monkeypatch, BARREL_FILES)
    output = _invoke_ok(runner, ["demote", "pkg.mod:helper", "--keep-export"])
    assert output["operation"] == "demote"
    assert "warnings" not in output  # public surface preserved: nothing to warn
    assert output["applied"] is True

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
# Demote evidence advisories (TASK-149)
# ---------------------------------------------------------------------------


def test_demote_warns_on_dynamic_access_module(tmp_path, monkeypatch):
    files = {
        "mod.py": (
            "def helper():\n    return 1\n\n"
            "def dispatch(name):\n"
            "    return getattr(helper, name)\n"
        ),
    }
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["demote", "mod:helper"])
    assert output["applied"] is True
    advisory = next(
        (w for w in output["warnings"] if "HEURISTIC" in w),
        None,
    )
    assert advisory is not None, output["warnings"]
    assert "dynamic access" in advisory
    assert "privatize" in advisory


def test_demote_warns_when_references_live_outside_the_indexed_roots(
    tmp_path, monkeypatch
):
    # The DropReason regression, end to end: only src/ is indexed, so the
    # reference search never sees tests/ and the demote silently breaks it.
    (tmp_path / "pyproject.toml").write_text(APP_PYPROJECT)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "mod.py").write_text(
        "def helper():\n    return 1\n\n\ndef use():\n    return helper()\n"
    )
    (tmp_path / "tests").mkdir()
    test_source = "from pkg.mod import helper\n\n\ndef test_helper():\n    assert helper() == 1\n"
    (tmp_path / "tests" / "test_mod.py").write_text(test_source)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["index", str(tmp_path / "src")], catch_exceptions=False
        ).exit_code
        == 0
    )

    output = _invoke_ok(runner, ["demote", "pkg.mod:helper"])
    assert output["applied"] is True
    # The breakage the advisory is about is real: tests/ was never rewritten.
    assert (tmp_path / "tests" / "test_mod.py").read_text() == test_source
    assert "def _helper()" in (tmp_path / "src" / "pkg" / "mod.py").read_text()

    advisory = next(
        (w for w in output["warnings"] if "unindexed" in w),
        None,
    )
    assert advisory is not None, output["warnings"]
    assert "1 of 1 unindexed Python file(s) mention 'helper' (under tests)" in advisory
    assert "any use there will break" in advisory


def test_demote_advisory_skips_unindexed_files_that_never_mention_the_name(
    tmp_path, monkeypatch
):
    # A project-constant "N files not indexed" banner would be emitted on
    # every demote regardless of symbol and would train the caller to ignore
    # it. The advisory is evidence about THIS symbol: an unindexed file that
    # never mentions the name produces silence.
    (tmp_path / "pyproject.toml").write_text(APP_PYPROJECT)
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "__init__.py").write_text("")
    (tmp_path / "src" / "pkg" / "mod.py").write_text(
        "def helper():\n    return 1\n\n\ndef use():\n    return helper()\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text(
        "def test_unrelated():\n    assert True\n"
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert (
        runner.invoke(
            main, ["index", str(tmp_path / "src")], catch_exceptions=False
        ).exit_code
        == 0
    )

    output = _invoke_ok(runner, ["demote", "pkg.mod:helper"])
    assert output["applied"] is True
    assert "warnings" not in output


def test_demote_stays_silent_when_fully_indexed_and_no_dynamic_access(
    tmp_path, monkeypatch
):
    # Silence is the honest answer when the index covers the whole project
    # and nothing dynamic is in the way — no constant-banner advisories.
    files = {"mod.py": "def helper():\n    return 1\n\n\ndef use():\n    return helper()\n"}
    _, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["demote", "mod:helper"])
    assert output["applied"] is True
    assert "warnings" not in output


# ---------------------------------------------------------------------------
# Promote
# ---------------------------------------------------------------------------


def test_promote_applies_by_default_strips_underscore_and_renames_references(
    tmp_path, monkeypatch
):
    files = {"mod.py": "def _solo():\n    pass\n\n\n_solo()\n"}
    project, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["promote", "mod:_solo"])
    assert output["operation"] == "promote"
    assert output["old_name"] == "_solo"
    assert output["new_name"] == "solo"
    assert output["applied"] is True

    # Bytes actually changed on disk without a separate 'apply' call.
    assert (project / "mod.py").read_text() == "def solo():\n    pass\n\n\nsolo()\n"

    header = TransactionStore(project).load(output["tx_id"]).header
    assert header.status.value == "applied"

    # Rollback restores the original bytes.
    rollback_out = _invoke_ok(runner, ["rollback", output["tx_id"]])
    assert rollback_out["status"] == "rolled_back"
    assert (project / "mod.py").read_text() == files["mod.py"]


def test_promote_plan_leaves_transaction_pending_and_tree_untouched(
    tmp_path, monkeypatch
):
    files = {"mod.py": "def _solo():\n    pass\n\n\n_solo()\n"}
    project, runner = _cli_project(tmp_path, monkeypatch, files)
    output = _invoke_ok(runner, ["promote", "mod:_solo", "--plan"])
    assert output["operation"] == "promote"
    assert "applied" not in output

    # Plan-only: the real tree is untouched...
    assert (project / "mod.py").read_text() == files["mod.py"]

    # ...and the persisted transaction is inspectable and PENDING.
    header = TransactionStore(project).load(output["tx_id"]).header
    assert header.operation == "promote"
    assert header.status.value == "pending"

    # A later manual apply lands the same edits the default path would have.
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
    assert output["applied"] is True
    # Restored from the pre-TASK-126 two-step (plan + apply) workflow: the
    # default path reports the apply's re-index result too.
    assert output["files_reindex_failed"] == []

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
    _invoke_ok(runner, ["promote", "pkg.mod:_helper", "--add-export", "pkg"])
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
    loaded = transaction_store.load(result.summary.tx_id)
    header, edits, file_rename = loaded.header, loaded.edits, loaded.file_rename
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
