"""Tests for the builtin naming-conventions rule (check/builtin/naming_conventions).

Per-file, resolution-free, opt-in: symbol kind + name decide everything.
Covers per-kind detection with suggested conforming names (including
HTTPServer-style acronym splits), underscore/dunder tolerance, the kinds /
conventions / allow options, and the rename_pair extraction helper that
hands findings to the refactor-side converter.
"""

from __future__ import annotations

from pypeeker.check.builtin.naming_conventions import (
    NAMING_CONVENTIONS,
    naming_conventions,
    rename_pair,
    to_pascal_case,
    to_snake_case,
)
from pypeeker.check.models import Violation
from pypeeker.check.rules import get_rule

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


def _run(bind_source, src: str, options: dict | None = None) -> list[Violation]:
    """Bind ``src`` and run the rule over the resulting FileIndex."""
    return naming_conventions(bind_source(src), options or {})


def _messages(violations: list[Violation]) -> str:
    return "\n".join(v.message for v in violations)


# ── registration / opt-in ───────────────────────────────────────────────────


def test_registered_as_file_rule():
    # Importing the module above self-registers it via @register_rule.
    assert get_rule(NAMING_CONVENTIONS) is not None


def test_not_in_default_rules():
    # Available but opt-in: pypeeker's own config must not enable it.
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert NAMING_CONVENTIONS not in data["tool"]["pypeeker"]["rules"]


# ── per-kind detection with suggestions ─────────────────────────────────────


class TestDefaults:
    def test_flags_function_method_and_class_by_default(self, bind_source):
        found = _run(bind_source, SRC)
        messages = _messages(found)
        assert "'test:bad_class'" in messages
        assert "'test:bad_class.goodMethod'" in messages
        assert "'test:BadFunction'" in messages
        assert len(found) == 3

    def test_conforming_names_are_silent(self, bind_source):
        messages = _messages(_run(bind_source, SRC))
        assert "GoodClass" not in messages
        assert "ok_method" not in messages
        assert "fine_function" not in messages

    def test_parameters_and_locals_are_off_by_default(self, bind_source):
        messages = _messages(_run(bind_source, SRC))
        assert "BadParam" not in messages
        assert "BadLocal" not in messages

    def test_messages_carry_suggested_conforming_names(self, bind_source):
        messages = _messages(_run(bind_source, SRC))
        assert "suggested name: 'BadClass'" in messages
        assert "suggested name: 'good_method'" in messages
        assert "suggested name: 'bad_function'" in messages

    def test_lines_are_one_indexed(self, bind_source):
        found = _run(bind_source, "def BadName():\n    pass\n")
        assert [v.line for v in found] == [1]
        assert all(v.rule == NAMING_CONVENTIONS for v in found)

    def test_acronym_function_gets_a_whole_acronym_suggestion(self, bind_source):
        found = _run(bind_source, "def getHTTPResponse():\n    pass\n")
        assert "suggested name: 'get_http_response'" in _messages(found)


class TestUnderscoreAndDunderTolerance:
    def test_leading_underscore_is_stripped_then_preserved(self, bind_source):
        found = _run(bind_source, "def _helperName():\n    pass\n")
        assert "suggested name: '_helper_name'" in _messages(found)

    def test_mangled_prefix_is_preserved_too(self, bind_source):
        found = _run(bind_source, "def __mangledName():\n    pass\n")
        assert "suggested name: '__mangled_name'" in _messages(found)

    def test_underscored_conforming_names_are_silent(self, bind_source):
        assert _run(bind_source, "def _ok_name():\n    pass\n") == []

    def test_dunders_are_skipped(self, bind_source):
        src = "class Widget:\n    def __init__(self):\n        pass\n"
        assert _run(bind_source, src) == []

    def test_underscore_only_names_are_skipped(self, bind_source):
        src = "def f():\n    _ = 1\n    __ = 2\n"
        assert _run(bind_source, src, {"kinds": ["variable"]}) == []


# ── options ─────────────────────────────────────────────────────────────────


class TestKindsOption:
    def test_opting_into_parameters_and_variables(self, bind_source):
        found = _run(bind_source, SRC, {"kinds": ["variable", "parameter"]})
        messages = _messages(found)
        assert "'test:BadFunction:BadParam'" in messages
        assert "'test:BadFunction:BadLocal'" in messages
        # ...and only the selected kinds are checked.
        assert "bad_class" not in messages
        assert "BadFunction'" not in messages

    def test_upper_snake_module_variable_is_tolerated(self, bind_source):
        # Constants can't be told apart from variables in v1, so the default
        # variable convention accepts UPPER_SNAKE (module docstring).
        src = "MAX_SIZE = 10\ncamelVar = 2\n"
        found = _run(bind_source, src, {"kinds": ["variable"]})
        messages = _messages(found)
        assert "MAX_SIZE" not in messages
        assert "'test:camelVar'" in messages

    def test_unknown_kind_values_are_ignored(self, bind_source):
        found = _run(bind_source, SRC, {"kinds": ["class", "spaceship"]})
        assert len(found) == 1
        assert "'test:bad_class'" in found[0].message


class TestConventionsOption:
    def test_custom_regex_overrides_a_kind(self, bind_source):
        # Functions must be single lowercase words: snake_case now violates.
        options = {"conventions": {"function": "^[a-z]+$"}}
        found = _run(bind_source, "def do_thing():\n    pass\n", options)
        assert len(found) == 1
        assert "pattern '^[a-z]+$'" in found[0].message

    def test_no_suggestion_when_converter_cannot_improve(self, bind_source):
        # 'do_thing' is already snake_case; the suggester has nothing better,
        # so the message carries no suggestion and rename_pair returns None.
        options = {"conventions": {"function": "^[a-z]+$"}}
        (violation,) = _run(bind_source, "def do_thing():\n    pass\n", options)
        assert "suggested name" not in violation.message
        assert rename_pair(violation) is None

    def test_other_kinds_keep_their_defaults(self, bind_source):
        options = {"conventions": {"function": "^[a-z]+$"}}
        found = _run(bind_source, "class bad_class:\n    pass\n", options)
        assert "suggested name: 'BadClass'" in _messages(found)

    def test_invalid_regex_is_ignored(self, bind_source):
        options = {"conventions": {"function": "(["}}
        found = _run(bind_source, "def BadName():\n    pass\n", options)
        assert "snake_case naming convention" in _messages(found)


class TestAllowOption:
    def test_allow_by_bare_name(self, bind_source):
        src = "def setUp():\n    pass\n\ndef tearDownNow():\n    pass\n"
        found = _run(bind_source, src, {"allow": ["setUp", "tearDown*"]})
        assert found == []

    def test_allow_by_symbol_id_or_module(self, bind_source):
        src = "def BadName():\n    pass\n"
        assert _run(bind_source, src, {"allow": ["test:BadName"]}) == []
        assert _run(bind_source, src, {"allow": ["test"]}) == []
        assert _run(bind_source, src, {"allow": ["other"]}) != []


# ── converters ──────────────────────────────────────────────────────────────


class TestToSnakeCase:
    def test_consecutive_caps_split_before_the_last(self):
        assert to_snake_case("HTTPServer") == "http_server"
        assert to_snake_case("getHTTPResponse") == "get_http_response"

    def test_simple_camel_case(self):
        assert to_snake_case("getValue") == "get_value"
        assert to_snake_case("BadName") == "bad_name"

    def test_digits_stick_to_the_preceding_word(self):
        assert to_snake_case("parseHTML2Text") == "parse_html2_text"
        assert to_snake_case("getHTTP2") == "get_http2"

    def test_underscore_runs_collapse(self):
        assert to_snake_case("get_Value") == "get_value"

    def test_already_snake_passes_through(self):
        assert to_snake_case("already_snake") == "already_snake"


class TestToPascalCase:
    def test_snake_parts_capitalize(self):
        assert to_pascal_case("bad_class") == "BadClass"
        assert to_pascal_case("http_server") == "HttpServer"

    def test_acronym_parts_survive(self):
        assert to_pascal_case("HTTP_server") == "HTTPServer"

    def test_camel_case_keeps_its_humps(self):
        assert to_pascal_case("badClass") == "BadClass"

    def test_digit_leading_parts_are_kept(self):
        assert to_pascal_case("foo_2d") == "Foo2d"


# ── rename_pair extraction ──────────────────────────────────────────────────


class TestRenamePair:
    def test_extracts_symbol_id_and_suggestion(self, bind_source):
        found = _run(bind_source, SRC)
        pairs = [pair for pair in map(rename_pair, found) if pair is not None]
        assert ("test:bad_class", "BadClass") in pairs
        assert ("test:bad_class.goodMethod", "good_method") in pairs
        assert ("test:BadFunction", "bad_function") in pairs

    def test_preserves_underscore_prefix(self, bind_source):
        (violation,) = _run(bind_source, "def _helperName():\n    pass\n")
        assert rename_pair(violation) == ("test:_helperName", "_helper_name")

    def test_other_rules_yield_none(self):
        other = Violation(
            file_path="x.py", line=1, rule="require-docstrings", message="whatever"
        )
        assert rename_pair(other) is None
