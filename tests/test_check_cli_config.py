"""``check``'s configuration and refusal envelopes, driven through the CLI.

Both scenarios here are new at the flip (TASK-157), and both are about what
``check`` does when it CANNOT run rather than what it reports when it can:

* an unresolvable rule name in ``[tool.pypeeker].rules`` is a usage error
  naming every id that does resolve — the frozen engine skipped such a name in
  silence, which made a typo read as a clean run;
* two repairs deriving the same ``<rule>:<mutation>:<anchor>`` fix id leave the
  report unable to name what it applied, so ``--fix`` emits the standard error
  envelope. The derivation is supposed to make that unreachable; the test
  exists because a traceback is not one of this CLI's output shapes.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from pypeeker.cli import main
from pypeeker.dsl import Finding, Remediation
from pypeeker.dsl.rules import _REGISTERED as _DSL_REGISTERED
from pypeeker.dsl.rules import register_dsl_rule
from pypeeker.intents import ReplaceTextIntent
from pypeeker.models import Confidence


def _project(tmp_path: Path, runner: CliRunner, rules: str, source: str) -> Path:
    """A one-module tmp project with ``rules`` enabled and ``src/`` indexed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\n'
        "[tool.pypeeker]\n"
        'src = ["src"]\n'
        f"rules = {rules}\n"
    )
    module = tmp_path / "src" / "mod.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source)
    os.chdir(tmp_path)
    result = runner.invoke(
        main, ["index", str(tmp_path / "src")], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return tmp_path


class TestUnknownConfiguredRule:
    """A rule name nothing provides refuses, and says what would have worked."""

    def test_a_typo_in_rules_is_a_usage_error_naming_the_known_ids(
        self, tmp_path
    ):
        runner = CliRunner()
        _project(tmp_path, runner, '["require-docstrigs"]', "A = 1\n")

        result = runner.invoke(main, ["check"], catch_exceptions=False)

        # Exit 2 is click's usage-error code, the same shape an unusable
        # import-boundaries table already gets.
        assert result.exit_code == 2
        assert "require-docstrigs" in result.output
        # The refusal is only better than the silence it replaces if it says
        # what IS reachable.
        assert "require-docstrings" in result.output

    def test_a_typo_is_refused_even_when_the_other_rules_would_pass(
        self, tmp_path
    ):
        # Nothing is reported and nothing is run: the config is unusable, so
        # there is no partial answer to hand back.
        runner = CliRunner()
        _project(
            tmp_path, runner, '["unused-imports", "no-such-rule"]', "import os\n"
        )

        result = runner.invoke(main, ["check"], catch_exceptions=False)

        assert result.exit_code == 2
        assert "no-such-rule" in result.output
        # No finding line ("path:line: [rule] message") was printed; the name
        # still appears inside the refusal's list of reachable ids.
        assert "[unused-imports]" not in result.output

    def test_a_resolvable_rule_set_still_runs(self, tmp_path):
        # The control: the refusal above is about the name, not about the
        # resolution step being wired in at all.
        runner = CliRunner()
        _project(tmp_path, runner, '["unused-imports"]', "import os\n")

        result = runner.invoke(main, ["check"], catch_exceptions=False)

        assert result.exit_code == 1
        assert "[unused-imports]" in result.output


@dataclasses.dataclass(frozen=True)
class _TwinRepairRule:
    """A test-only ported rule proposing two repairs under ONE fix id.

    Duck-typed the way every rule is (``findings`` / ``remediations`` /
    ``mutation``): the derived-id scheme forbids this state, so it cannot be
    reached through the DSL's own mutation terminals.
    """

    rule_id: str = "twin-repair"
    mutation: object = "test-only"

    def _rows(self, options, corpus) -> list[Remediation]:
        rows: list[Remediation] = []
        for file_index in corpus.indexes:
            for old, new in (("A = 1", "A = 2"), ("B = 1", "B = 2")):
                rows.append(
                    Remediation(
                        finding=Finding(
                            rule=self.rule_id,
                            path=file_index.file_path,
                            line=1,
                            message=f"{old} -> {new}",
                            confidence=Confidence.DECLARED,
                        ),
                        intent=ReplaceTextIntent(
                            "twin-repair:replace:mod",
                            file_index.file_path,
                            0,
                            0,
                            old,
                            new,
                        ),
                    )
                )
        return rows

    def findings(self, options, corpus) -> list[Finding]:
        return [remediation.finding for remediation in self._rows(options, corpus)]

    def remediations(self, options, corpus) -> list[Remediation]:
        return self._rows(options, corpus)


@pytest.fixture
def twin_repair_rule():
    """Register the duplicate-id rule for one test, then unregister it."""
    rule = register_dsl_rule(_TwinRepairRule())
    yield rule.rule_id
    _DSL_REGISTERED.pop(rule.rule_id, None)


class TestDuplicateFixIds:
    """A repeated derived fix id refuses through the error envelope."""

    def test_check_fix_reports_a_refusal_rather_than_a_traceback(
        self, tmp_path, twin_repair_rule
    ):
        runner = CliRunner()
        _project(tmp_path, runner, f'["{twin_repair_rule}"]', "A = 1\nB = 1\n")

        result = runner.invoke(main, ["check", "--fix"], catch_exceptions=False)

        assert result.exit_code == 1
        report = json.loads(result.output)
        assert report["code"] == "duplicate-fix-id"
        assert "twin-repair:replace:mod" in report["error"]


class TestExitCodes:
    """``check``'s two ordinary exits, ported from ``tests/test_check_engine.py``.

    These say nothing about which engine is behind the command — that is the
    point. They pin the contract the shell sees: a finding is exit 1 with the
    finding printed, a clean run is exit 0 with *nothing* printed, and the
    second half of that is what stops a future change from making ``check``
    chatty on success.
    """

    def test_a_finding_exits_one_and_is_printed(self, tmp_path):
        runner = CliRunner()
        _project(tmp_path, runner, '["require-docstrings"]', "def foo():\n    return 1\n")

        result = runner.invoke(main, ["check"], catch_exceptions=False)

        assert result.exit_code == 1
        assert "src/mod.py:" in result.output
        assert "[require-docstrings]" in result.output

    def test_a_clean_run_exits_zero_and_prints_nothing(self, tmp_path):
        runner = CliRunner()
        _project(
            tmp_path,
            runner,
            '["require-docstrings"]',
            'def foo():\n    """ok"""\n    return 1\n',
        )

        result = runner.invoke(main, ["check"], catch_exceptions=False)

        assert result.exit_code == 0
        assert result.output == ""


class TestConfigOptionErrorIsAUsageError:
    """A ``[tool.pypeeker]`` option the coercers refuse renders as a usage error.

    TASK-163 made the config coercers loud: a shape the tool will not guess
    about raises ``ConfigOptionError`` instead of silently emptying a set. Loud
    is only an improvement if the shell sees ``Error: [tool.pypeeker] option
    ...``; a ``ConfigOptionError`` traceback is strictly worse than the silence
    it replaced. The escape hatch is wider than the commands that read the
    config on purpose — ``load_src_roots`` is reached from ``_refresh_index``
    (every command), from ``index_path``, and from ``query``'s own call under
    ``--no-refresh`` — so these pin the *rendering* on a command from each of
    those paths, not just on ``check``.

    ``catch_exceptions=False`` is load-bearing: it makes a leaked
    ``ConfigOptionError`` fail the test as an error rather than as an exit
    code, which is how the original hole hid.
    """

    @staticmethod
    def _bare_string_src(tmp_path: Path, *, indexed: bool) -> None:
        """A project whose ``src`` is the bare string click must refuse.

        ``indexed`` decides whether a populated index exists first: an empty
        index makes ``ensure_fresh`` a no-op ("a never-indexed project is left
        alone"), so the refresh path only reaches ``load_src_roots`` on a
        project that has been indexed once.
        """
        tmp_path.mkdir(parents=True, exist_ok=True)
        module = tmp_path / "src" / "mod.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text("def alpha():\n    return 1\n")
        pyproject = tmp_path / "pyproject.toml"
        good = (
            '[project]\nname = "test"\n'
            "[tool.pypeeker]\n"
            'src = ["src"]\n'
            'rules = ["require-docstrings"]\n'
        )
        pyproject.write_text(good)
        os.chdir(tmp_path)
        if indexed:
            result = CliRunner().invoke(
                main, ["index", str(tmp_path / "src")], catch_exceptions=False
            )
            assert result.exit_code == 0, result.output
            module.write_text("def alpha():\n    return 2\n")
        pyproject.write_text(good.replace('src = ["src"]', 'src = "src"'))

    @staticmethod
    def _assert_refused(result) -> None:
        """Click's usage-error shape, naming the key and the accepted form."""
        assert result.exit_code == 2, result.output
        assert "Traceback" not in result.output
        assert "Error: [tool.pypeeker] option 'src'" in result.output
        assert "expected a list of strings" in result.output

    def test_check_refuses_a_bare_string_src(self, tmp_path):
        self._bare_string_src(tmp_path, indexed=True)

        result = CliRunner().invoke(main, ["check"], catch_exceptions=False)

        self._assert_refused(result)

    def test_index_refuses_a_bare_string_src(self, tmp_path):
        # `index` never calls `_refresh_index`; it reaches `load_src_roots`
        # through `index_path`, and it is the FIRST command a user runs.
        self._bare_string_src(tmp_path, indexed=False)

        result = CliRunner().invoke(
            main, ["index", str(tmp_path / "src")], catch_exceptions=False
        )

        self._assert_refused(result)

    def test_a_read_only_command_refuses_a_bare_string_src(self, tmp_path):
        # `symbol` reads no config itself; it inherits the refusal from the
        # freshness refresh every command shares.
        self._bare_string_src(tmp_path, indexed=True)

        result = CliRunner().invoke(main, ["symbol", "alpha"], catch_exceptions=False)

        self._assert_refused(result)

    def test_query_refuses_a_bare_string_src_even_with_no_refresh(self, tmp_path):
        # --no-refresh skips `_refresh_index`, so this exercises `query`'s own
        # `load_src_roots` call rather than the shared refresh path.
        self._bare_string_src(tmp_path, indexed=True)

        result = CliRunner().invoke(
            main,
            ["query", "--no-refresh", "unused-public-symbol"],
            catch_exceptions=False,
        )

        self._assert_refused(result)

    def test_privatize_refuses_a_bare_string_src(self, tmp_path):
        self._bare_string_src(tmp_path, indexed=True)

        result = CliRunner().invoke(main, ["privatize", "--plan"], catch_exceptions=False)

        self._assert_refused(result)

    def test_a_bad_visibility_mode_refuses_naming_the_accepted_modes(self, tmp_path):
        # The other half of the contract: a refusal names the option key, the
        # offending value and the accepted values -- never the rule id.
        runner = CliRunner()
        _project(tmp_path, runner, '["require-docstrings"]', "A = 1\n")
        (tmp_path / "pyproject.toml").write_text(
            (tmp_path / "pyproject.toml").read_text()
            + '\n[tool.pypeeker.visibility]\nmode = "nope"\n'
        )

        result = runner.invoke(main, ["check"], catch_exceptions=False)

        assert result.exit_code == 2, result.output
        assert "Traceback" not in result.output
        assert "visibility.mode" in result.output
        assert "'nope'" in result.output
        assert "app, library" in result.output
        assert "require-docstrings" not in result.output
