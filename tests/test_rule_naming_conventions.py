"""Tests for the builtin naming-conventions rule (check/builtin/naming_conventions).

Per-file, resolution-free, opt-in: symbol kind + name decide everything.
Covers per-kind detection with suggested conforming names (including
HTTPServer-style acronym splits), underscore/dunder tolerance, the kinds /
conventions / allow options, and the suggested names the messages carry.
"""

from __future__ import annotations

import pytest

from pypeeker.dsl import RULES, Finding
from pypeeker.project import ConfigOptionError

NAMING_CONVENTIONS = "naming-conventions"

SRC = """\
class bad_class:
    def goodMethod(self):
        return 1

    def ok_method(self):
        return 2


class GoodClass:
    pass


def BadFunction(x, BadParam=1):
    BadLocal = x
    return BadLocal


def fine_function():
    pass
"""


def _run(run_dsl_rule, src: str, options: dict | None = None) -> list[Finding]:
    """Index ``src`` as ``test.py`` and run the rule over it.

    The file name is load-bearing: the frozen rule ran on a bare ``FileIndex``
    from ``bind_source``, whose default path is ``test.py``, so keeping that
    name keeps every ``'test:...'`` symbol id in the assertions below exact.
    """
    return run_dsl_rule(NAMING_CONVENTIONS, {"test.py": src}, options)


def _messages(violations: list[Finding]) -> str:
    return "\n".join(v.message for v in violations)


# ── registration / opt-in ───────────────────────────────────────────────────


def test_resolvable_by_id_in_the_rule_table():
    assert NAMING_CONVENTIONS in RULES


def test_not_gated_covered_by_ruff():
    # Not gated on pypeeker: ruff's pep8-naming (N8xx) covers PEP 8 naming and
    # already runs in CI + the pre-commit hook, so gating here is duplication.
    # The rule stays available for consumer projects that don't run ruff.
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert NAMING_CONVENTIONS not in data["tool"]["pypeeker"]["rules"]


# ── per-kind detection with suggestions ─────────────────────────────────────


class TestDefaults:
    def test_flags_function_method_and_class_by_default(self, run_dsl_rule):
        found = _run(run_dsl_rule, SRC)
        messages = _messages(found)
        assert "'test:bad_class'" in messages
        assert "'test:bad_class.goodMethod'" in messages
        assert "'test:BadFunction'" in messages
        assert len(found) == 3

    def test_conforming_names_are_silent(self, run_dsl_rule):
        messages = _messages(_run(run_dsl_rule, SRC))
        assert "GoodClass" not in messages
        assert "ok_method" not in messages
        assert "fine_function" not in messages

    def test_parameters_and_locals_are_off_by_default(self, run_dsl_rule):
        messages = _messages(_run(run_dsl_rule, SRC))
        assert "BadParam" not in messages
        assert "BadLocal" not in messages

    def test_messages_carry_suggested_conforming_names(self, run_dsl_rule):
        messages = _messages(_run(run_dsl_rule, SRC))
        assert "suggested name: 'BadClass'" in messages
        assert "suggested name: 'good_method'" in messages
        assert "suggested name: 'bad_function'" in messages

    def test_lines_are_one_indexed(self, run_dsl_rule):
        found = _run(run_dsl_rule, "def BadName():\n    pass\n")
        assert [v.line for v in found] == [1]
        assert all(v.rule == NAMING_CONVENTIONS for v in found)

    def test_acronym_function_gets_a_whole_acronym_suggestion(self, run_dsl_rule):
        found = _run(run_dsl_rule, "def getHTTPResponse():\n    pass\n")
        assert "suggested name: 'get_http_response'" in _messages(found)


class TestUnderscoreAndDunderTolerance:
    def test_leading_underscore_is_stripped_then_preserved(self, run_dsl_rule):
        found = _run(run_dsl_rule, "def _helperName():\n    pass\n")
        assert "suggested name: '_helper_name'" in _messages(found)

    def test_mangled_prefix_is_preserved_too(self, run_dsl_rule):
        found = _run(run_dsl_rule, "def __mangledName():\n    pass\n")
        assert "suggested name: '__mangled_name'" in _messages(found)

    def test_underscored_conforming_names_are_silent(self, run_dsl_rule):
        assert _run(run_dsl_rule, "def _ok_name():\n    pass\n") == []

    def test_dunders_are_skipped(self, run_dsl_rule):
        src = "class Widget:\n    def __init__(self):\n        pass\n"
        assert _run(run_dsl_rule, src) == []

    def test_underscore_only_names_are_skipped(self, run_dsl_rule):
        src = "def f():\n    _ = 1\n    __ = 2\n"
        assert _run(run_dsl_rule, src, {"kinds": ["variable"]}) == []


# ── options ─────────────────────────────────────────────────────────────────


class TestKindsOption:
    def test_opting_into_parameters_and_variables(self, run_dsl_rule):
        found = _run(run_dsl_rule, SRC, {"kinds": ["variable", "parameter"]})
        messages = _messages(found)
        assert "'test:BadFunction:BadParam'" in messages
        assert "'test:BadFunction:BadLocal'" in messages
        # ...and only the selected kinds are checked.
        assert "bad_class" not in messages
        assert "BadFunction'" not in messages

    def test_upper_snake_module_variable_is_tolerated(self, run_dsl_rule):
        # Constants can't be told apart from variables in v1, so the default
        # variable convention accepts UPPER_SNAKE (module docstring).
        src = "MAX_SIZE = 10\ncamelVar = 2\n"
        found = _run(run_dsl_rule, src, {"kinds": ["variable"]})
        messages = _messages(found)
        assert "MAX_SIZE" not in messages
        assert "'test:camelVar'" in messages

    def test_unknown_kind_values_refuse(self, run_dsl_rule):
        # Was ..._are_ignored: `spaceship` used to be dropped silently, so the
        # rule quietly checked only `class` and the typo never surfaced. It now
        # refuses, naming the option and the accepted kinds (TASK-163).
        with pytest.raises(ConfigOptionError) as exc:
            _run(run_dsl_rule, SRC, {"kinds": ["class", "spaceship"]})
        assert "'kinds'" in str(exc.value)
        assert "'spaceship'" in str(exc.value)

        # The surviving half of the old scenario: a well-spelled `class`
        # narrows the rule to the one bad class.
        found = _run(run_dsl_rule, SRC, {"kinds": ["class"]})
        assert len(found) == 1
        assert "'test:bad_class'" in found[0].message


class TestConventionsOption:
    def test_custom_regex_overrides_a_kind(self, run_dsl_rule):
        # Functions must be single lowercase words: snake_case now violates.
        options = {"conventions": {"function": "^[a-z]+$"}}
        found = _run(run_dsl_rule, "def do_thing():\n    pass\n", options)
        assert len(found) == 1
        assert "pattern '^[a-z]+$'" in found[0].message

    def test_no_suggestion_when_converter_cannot_improve(self, run_dsl_rule):
        # 'do_thing' is already snake_case; the suggester has nothing better,
        # so the message carries no suggestion and rename_pair returns None.
        options = {"conventions": {"function": "^[a-z]+$"}}
        (violation,) = _run(run_dsl_rule, "def do_thing():\n    pass\n", options)
        assert "suggested name" not in violation.message

    def test_other_kinds_keep_their_defaults(self, run_dsl_rule):
        options = {"conventions": {"function": "^[a-z]+$"}}
        found = _run(run_dsl_rule, "class bad_class:\n    pass\n", options)
        assert "suggested name: 'BadClass'" in _messages(found)

    def test_invalid_regex_is_ignored(self, run_dsl_rule):
        options = {"conventions": {"function": "(["}}
        found = _run(run_dsl_rule, "def BadName():\n    pass\n", options)
        assert "snake_case naming convention" in _messages(found)


class TestAllowOption:
    def test_allow_by_bare_name(self, run_dsl_rule):
        src = "def setUp():\n    pass\n\ndef tearDownNow():\n    pass\n"
        found = _run(run_dsl_rule, src, {"allow": ["setUp", "tearDown*"]})
        assert found == []

    def test_allow_by_symbol_id_or_module(self, run_dsl_rule):
        src = "def BadName():\n    pass\n"
        assert _run(run_dsl_rule, src, {"allow": ["test:BadName"]}) == []
        assert _run(run_dsl_rule, src, {"allow": ["test"]}) == []
        assert _run(run_dsl_rule, src, {"allow": ["other"]}) != []


# ── converters ──────────────────────────────────────────────────────────────


class TestSnakeCaseSuggestions:
    """The snake_case suggester, observed through the message it words.

    The frozen rule exposed ``_to_snake_case`` as a module-level helper and
    these cases called it directly. The DSL's converter is private to
    ``pypeeker.dsl.sweeps``; its output reaches a reader only as the
    ``— suggested name: '...'`` tail, so every edge case is asserted there.
    """

    def _suggestion(self, run_dsl_rule, name: str) -> str:
        (violation,) = _run(run_dsl_rule, f"def {name}():\n    pass\n")
        head, _, suggested = violation.message.partition("suggested name: '")
        assert suggested, violation.message
        return suggested.rstrip("'")

    def test_consecutive_caps_split_before_the_last(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "HTTPServer") == "http_server"
        assert self._suggestion(run_dsl_rule, "getHTTPResponse") == (
            "get_http_response"
        )

    def test_simple_camel_case(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "getValue") == "get_value"
        assert self._suggestion(run_dsl_rule, "BadName") == "bad_name"

    def test_digits_stick_to_the_preceding_word(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "parseHTML2Text") == (
            "parse_html2_text"
        )
        assert self._suggestion(run_dsl_rule, "getHTTP2") == "get_http2"

    def test_underscore_runs_collapse(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "get_Value") == "get_value"

    def test_already_snake_yields_no_suggestion(self, run_dsl_rule):
        # ``already_snake`` conforms, so the rule never fires on it at all —
        # the converter's identity case, observed one level out.
        assert _run(run_dsl_rule, "def already_snake():\n    pass\n") == []

    def test_underscore_prefix_is_preserved(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "_helperName") == "_helper_name"


class TestPascalCaseSuggestions:
    """The PascalCase suggester, observed through the class message."""

    def _suggestion(self, run_dsl_rule, name: str) -> str:
        (violation,) = _run(run_dsl_rule, f"class {name}:\n    pass\n")
        _, _, suggested = violation.message.partition("suggested name: '")
        assert suggested, violation.message
        return suggested.rstrip("'")

    def test_snake_parts_capitalize(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "bad_class") == "BadClass"
        assert self._suggestion(run_dsl_rule, "http_server") == "HttpServer"

    def test_acronym_parts_survive(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "HTTP_server") == "HTTPServer"

    def test_camel_case_keeps_its_humps(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "badClass") == "BadClass"

    def test_digit_leading_parts_are_kept(self, run_dsl_rule):
        assert self._suggestion(run_dsl_rule, "foo_2d") == "Foo2d"


# ── suggested names on the reported rows ────────────────────────────────────


class TestSuggestedNames:
    def test_every_flagged_row_carries_its_suggestion(self, run_dsl_rule):
        messages = _messages(_run(run_dsl_rule, SRC))
        assert "'test:bad_class' does not match the PascalCase naming convention" \
            " — suggested name: 'BadClass'" in messages
        assert "'test:bad_class.goodMethod' does not match the snake_case" \
            " naming convention — suggested name: 'good_method'" in messages
        assert "'test:BadFunction' does not match the snake_case naming" \
            " convention — suggested name: 'bad_function'" in messages
