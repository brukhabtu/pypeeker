"""CLI-surface tests for extract-variable, extract-method, and
inline-variable (TASK-126 grammar).

These commands apply immediately by default and take --plan to write the
PENDING transaction without applying — see cli.py's shared
``_submit_and_finish``/``_finish_mutation`` helpers. The direct-planner
tests (byte-precise edit shape) live in test_extract_variable.py,
test_extract_method.py, and test_inline_variable.py; this file covers the
CLI's plan/apply grammar end to end instead.
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


def _invoke(runner: CliRunner, args: list[str]) -> tuple[int, dict]:
    result = runner.invoke(main, args, catch_exceptions=False)
    return result.exit_code, json.loads(result.output)


# ---------------------------------------------------------------------------
# extract-variable
# ---------------------------------------------------------------------------


def test_extract_variable_applies_by_default(tmp_path):
    src = "def f():\n    return foo(bar) + 2\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    code, output = _invoke(
        runner, ["extract-variable", "m.py", "1:11", "1:19", "value"]
    )
    assert code == 0, output
    assert output["applied"] is True

    assert (project / "m.py").read_text() == (
        "def f():\n    value = foo(bar)\n    return value + 2\n"
    )

    code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
    assert code == 0, shown
    assert shown["header"]["status"] == "applied"

    code, rolled = _invoke(runner, ["rollback", output["tx_id"]])
    assert code == 0, rolled
    assert rolled["status"] == "rolled_back"
    assert (project / "m.py").read_text() == src


def test_extract_variable_plan_leaves_transaction_pending(tmp_path):
    src = "def f():\n    return foo(bar) + 2\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    code, output = _invoke(
        runner, ["extract-variable", "m.py", "1:11", "1:19", "value", "--plan"]
    )
    assert code == 0, output
    assert "applied" not in output
    assert output["new_name"] == "value"

    # Plan-only: the real tree is untouched...
    assert (project / "m.py").read_text() == src

    # ...and the persisted transaction is PENDING.
    code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
    assert shown["header"]["status"] == "pending"

    code, applied = _invoke(runner, ["apply", output["tx_id"]])
    assert code == 0, applied
    assert (project / "m.py").read_text() == (
        "def f():\n    value = foo(bar)\n    return value + 2\n"
    )


# ---------------------------------------------------------------------------
# extract-method
# ---------------------------------------------------------------------------


def test_extract_method_applies_by_default(tmp_path):
    src = "def f(a, b):\n    c = a + b\n    return c\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    code, output = _invoke(runner, ["extract-method", "m.py", "1", "1", "add"])
    assert code == 0, output
    assert output["applied"] is True

    out = (project / "m.py").read_text()
    assert "def add(a, b):" in out
    assert "    c = add(a, b)\n" in out

    code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
    assert shown["header"]["status"] == "applied"

    code, rolled = _invoke(runner, ["rollback", output["tx_id"]])
    assert code == 0, rolled
    assert (project / "m.py").read_text() == src


def test_extract_method_plan_leaves_transaction_pending(tmp_path):
    src = "def f(a, b):\n    c = a + b\n    return c\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    code, output = _invoke(
        runner, ["extract-method", "m.py", "1", "1", "add", "--plan"]
    )
    assert code == 0, output
    assert "applied" not in output

    # Plan-only: the real tree is untouched...
    assert (project / "m.py").read_text() == src

    code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
    assert shown["header"]["status"] == "pending"

    code, applied = _invoke(runner, ["apply", output["tx_id"]])
    assert code == 0, applied
    assert "def add(a, b):" in (project / "m.py").read_text()


def test_extract_method_refuses_non_utf8_file_with_the_standard_envelope(tmp_path):
    """A non-UTF-8 file is refused through the standard plan-refused envelope
    instead of crashing the CLI with an uncaught UnicodeDecodeError."""
    src = "def f(a):\n    c = a + 1\n    return c\n"
    project = _make_project(tmp_path, {"m.py": src})
    latin1 = b'def f(a):\n    c = a + 1\n    s = "caf\xe9"\n    return c\n'
    (project / "m.py").write_bytes(latin1)
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    result = runner.invoke(
        main, ["extract-method", "m.py", "1", "1", "add"], catch_exceptions=False
    )
    output = json.loads(result.output)
    assert result.exit_code == 1
    assert output["code"] == "plan-refused"
    assert output["error"].startswith("File is not valid UTF-8: m.py")
    assert (project / "m.py").read_bytes() == latin1


# ---------------------------------------------------------------------------
# inline-variable
# ---------------------------------------------------------------------------


def test_inline_variable_applies_by_default(tmp_path):
    src = "def f(a):\n    x = a + 1\n    return x\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    code, output = _invoke(runner, ["inline-variable", "m:f:x"])
    assert code == 0, output
    assert output["applied"] is True

    assert (project / "m.py").read_text() == "def f(a):\n    return (a + 1)\n"

    code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
    assert shown["header"]["status"] == "applied"

    code, rolled = _invoke(runner, ["rollback", output["tx_id"]])
    assert code == 0, rolled
    assert (project / "m.py").read_text() == src


def test_inline_variable_plan_leaves_transaction_pending(tmp_path):
    src = "def f(a):\n    x = a + 1\n    return x\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    code, output = _invoke(runner, ["inline-variable", "m:f:x", "--plan"])
    assert code == 0, output
    assert "applied" not in output
    assert output["operation"] == "inline_variable"

    # Plan-only: the real tree is untouched...
    assert (project / "m.py").read_text() == src

    code, shown = _invoke(runner, ["transactions", "show", output["tx_id"]])
    assert shown["header"]["status"] == "pending"

    code, applied = _invoke(runner, ["apply", output["tx_id"]])
    assert code == 0, applied
    assert (project / "m.py").read_text() == "def f(a):\n    return (a + 1)\n"


def test_inline_variable_refusal_never_touches_disk(tmp_path):
    src = "def f(a):\n    x = 1\n    x = 2\n    return x\n"
    project = _make_project(tmp_path, {"m.py": src})
    runner = CliRunner()
    os.chdir(project)
    runner.invoke(main, ["index", "m.py"], catch_exceptions=False)

    result = runner.invoke(main, ["inline-variable", "m:f:x"])
    assert result.exit_code == 1
    output = json.loads(result.output)
    assert "error" in output
    assert "reassigned" in output["error"]
    assert (project / "m.py").read_text() == src
