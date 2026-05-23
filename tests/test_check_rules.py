"""Tests for the individual check rules."""

from __future__ import annotations

from pypeeker.check.models import Violation
from pypeeker.check.rules import (
    IMPORT_BOUNDARIES,
    NO_UNRESOLVED_REFS,
    REQUIRE_DOCSTRINGS,
    import_boundaries,
    no_unresolved_refs,
    require_docstrings,
)


class TestRequireDocstrings:
    def test_flags_public_function_without_docstring(self, bind_source):
        file_index = bind_source("def foo():\n    return 1\n")
        violations = require_docstrings(file_index, {})
        assert any(
            v.rule == REQUIRE_DOCSTRINGS and "foo" in v.message for v in violations
        )

    def test_ignores_documented_function(self, bind_source):
        file_index = bind_source('def foo():\n    """ok"""\n    return 1\n')
        violations = require_docstrings(file_index, {})
        assert [v for v in violations if "foo" in v.message] == []

    def test_ignores_protected_by_default(self, bind_source):
        file_index = bind_source("def _hidden():\n    return 1\n")
        violations = require_docstrings(file_index, {})
        assert [v for v in violations if "_hidden" in v.message] == []

    def test_visibility_option_widens_scope(self, bind_source):
        file_index = bind_source("def _hidden():\n    return 1\n")
        violations = require_docstrings(
            file_index, {"visibility": ["public", "protected"]}
        )
        assert any("_hidden" in v.message for v in violations)

    def test_kinds_option_narrows_scope(self, bind_source):
        src = "class Foo:\n    pass\n\ndef bar():\n    return 1\n"
        file_index = bind_source(src)
        violations = require_docstrings(file_index, {"kinds": ["class"]})
        flagged = {v.message for v in violations}
        assert any("Foo" in m for m in flagged)
        assert not any("bar" in m for m in flagged)

    def test_line_number_is_1_indexed(self, bind_source):
        file_index = bind_source("\n\ndef foo():\n    return 1\n")
        violations = require_docstrings(file_index, {})
        foo_v = next(v for v in violations if "foo" in v.message)
        assert foo_v.line == 3


class TestNoUnresolvedRefs:
    def test_flags_genuinely_unresolved(self, bind_source):
        file_index = bind_source("def foo():\n    return totally_undefined\n")
        violations = no_unresolved_refs(file_index, {})
        assert any(v.rule == NO_UNRESOLVED_REFS for v in violations)
        assert any("totally_undefined" in v.message for v in violations)

    def test_does_not_flag_builtins(self, bind_source):
        # After TASK-21 builtins resolve as <builtins>.X with resolved=True,
        # so no_unresolved_refs should not fire on them.
        file_index = bind_source("def foo(x):\n    return len(x)\n")
        violations = no_unresolved_refs(file_index, {})
        assert not any("len" in v.message for v in violations)

    def test_skips_unresolved_attribute_chains(self, bind_source):
        from pypeeker.models.location import Location, Position, Span
        from pypeeker.models.references import Reference, ReferenceKind

        file_index = bind_source("x = 1\n")
        file_index.references.append(
            Reference(
                symbol_id="<unresolved>.something",
                kind=ReferenceKind.READ,
                location=Location(
                    file_path="test.py",
                    span=Span(
                        start=Position(line=0, column=0),
                        end=Position(line=0, column=1),
                    ),
                ),
                in_scope_id="test:<module>",
                resolved=False,
            )
        )
        violations = no_unresolved_refs(file_index, {})
        assert not any("<unresolved>" in v.message for v in violations)


class TestImportBoundaries:
    ALLOW = {"allow": {"binder": ["models"]}, "root": "app"}

    def test_flags_forbidden_cross_package_import(self, bind_source):
        src = "from app.storage import IndexStore\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        violations = import_boundaries(file_index, self.ALLOW)
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "binder" in v.message
            and "storage" in v.message
            for v in violations
        )

    def test_allows_permitted_import(self, bind_source):
        src = "from app.models import Symbol\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        assert import_boundaries(file_index, self.ALLOW) == []

    def test_same_package_import_never_flagged(self, bind_source):
        src = "from app.binder.helpers import thing\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        assert import_boundaries(file_index, self.ALLOW) == []

    def test_external_import_ignored(self, bind_source):
        src = "import os\nfrom collections import defaultdict\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        assert import_boundaries(file_index, self.ALLOW) == []

    def test_unlisted_package_is_unconstrained(self, bind_source):
        # "weird" is not in the allow map, so it may import anything.
        src = "from app.storage import IndexStore\n"
        file_index = bind_source(src, file_path="app/weird/x.py")
        assert import_boundaries(file_index, self.ALLOW) == []

    def test_root_inferred_when_omitted(self, bind_source):
        src = "from app.storage import IndexStore\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        violations = import_boundaries(file_index, {"allow": {"binder": ["models"]}})
        assert any("storage" in v.message for v in violations)

    def test_no_allow_config_is_noop(self, bind_source):
        src = "from app.storage import IndexStore\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        assert import_boundaries(file_index, {}) == []

    def test_line_is_1_indexed(self, bind_source):
        src = "\nfrom app.storage import IndexStore\n"
        file_index = bind_source(src, file_path="app/binder/x.py")
        violations = import_boundaries(file_index, self.ALLOW)
        assert violations[0].line == 2


class TestViolationFormat:
    def test_str_format_matches_ruff_mypy(self):
        v = Violation(
            file_path="src/x.py",
            line=12,
            rule="require-docstrings",
            message="public function 'foo' has no docstring",
        )
        assert (
            str(v)
            == "src/x.py:12: [require-docstrings] public function 'foo' has no docstring"
        )
