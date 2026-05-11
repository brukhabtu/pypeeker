"""Tests for the individual check rules."""

from __future__ import annotations

from pypeeker.check.rules import (
    NO_UNRESOLVED_REFS,
    REQUIRE_DOCSTRINGS,
    no_unresolved_refs,
    require_docstrings,
)


def test_require_docstrings_flags_public_function_without_docstring(bind_source):
    file_index = bind_source("def foo():\n    return 1\n")
    violations = require_docstrings(file_index, {})
    assert any(v.rule == REQUIRE_DOCSTRINGS and "foo" in v.message for v in violations)


def test_require_docstrings_ignores_documented_function(bind_source):
    file_index = bind_source('def foo():\n    """ok"""\n    return 1\n')
    violations = require_docstrings(file_index, {})
    assert [v for v in violations if "foo" in v.message] == []


def test_require_docstrings_ignores_protected_by_default(bind_source):
    file_index = bind_source("def _hidden():\n    return 1\n")
    violations = require_docstrings(file_index, {})
    assert [v for v in violations if "_hidden" in v.message] == []


def test_require_docstrings_visibility_option_widens_scope(bind_source):
    file_index = bind_source("def _hidden():\n    return 1\n")
    violations = require_docstrings(
        file_index, {"visibility": ["public", "protected"]}
    )
    assert any("_hidden" in v.message for v in violations)


def test_require_docstrings_kinds_option_narrows_scope(bind_source):
    src = (
        "class Foo:\n"
        "    pass\n"
        "\n"
        "def bar():\n"
        "    return 1\n"
    )
    file_index = bind_source(src)
    violations = require_docstrings(file_index, {"kinds": ["class"]})
    flagged_names = {v.message for v in violations}
    assert any("Foo" in m for m in flagged_names)
    assert not any("bar" in m for m in flagged_names)


def test_require_docstrings_line_number_is_1_indexed(bind_source):
    src = "\n\ndef foo():\n    return 1\n"
    file_index = bind_source(src)
    violations = require_docstrings(file_index, {})
    foo_violation = next(v for v in violations if "foo" in v.message)
    assert foo_violation.line == 3


def test_no_unresolved_refs_flags_unresolved(bind_source):
    file_index = bind_source("def foo():\n    return undefined_name\n")
    violations = no_unresolved_refs(file_index, {})
    assert any(v.rule == NO_UNRESOLVED_REFS for v in violations)


def test_no_unresolved_refs_skips_attribute_chains(bind_source):
    """Refs like '<unresolved>.x.y' are attribute access on unresolved roots."""
    from pypeeker.models.location import Location, Position, Span
    from pypeeker.models.references import Reference, ReferenceKind

    file_index = bind_source("x = 1\n")
    file_index.references.append(
        Reference(
            symbol_id="<unresolved>.something.else",
            kind=ReferenceKind.READ,
            location=Location(
                file_path="test.py",
                span=Span(start=Position(line=0, column=0), end=Position(line=0, column=1)),
            ),
            in_scope_id="test.py:<module>",
            resolved=False,
        )
    )
    violations = no_unresolved_refs(file_index, {})
    assert not any("<unresolved>" in v.message for v in violations)


def test_violation_str_format():
    from pypeeker.check.models import Violation

    v = Violation(
        file_path="src/x.py",
        line=12,
        rule="require-docstrings",
        message="public function 'foo' has no docstring",
    )
    assert str(v) == (
        "src/x.py:12: [require-docstrings] public function 'foo' has no docstring"
    )
