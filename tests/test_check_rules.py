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

    def test_flags_forbidden_relative_import(self, adapter):
        # src-layout + relative import: imported_from must be src-stripped
        # ("app.storage") so the layering rule sees it; resolving against the
        # file path yielded "src.app.storage", silently exempting relative
        # imports from boundary enforcement (TASK-58).
        from pypeeker.binder.binder import bind

        src = "from ..storage import IndexStore\n"
        source = src.encode("utf-8")
        tree = adapter.parse(source)
        file_index = bind(
            adapter,
            "src/app/binder/x.py",
            source,
            tree.root_node,
            module_path="app.binder.x",
        )
        violations = import_boundaries(file_index, self.ALLOW)
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "binder" in v.message
            and "storage" in v.message
            for v in violations
        )

    def test_allows_permitted_relative_import(self, adapter):
        from pypeeker.binder.binder import bind

        src = "from ..models import Symbol\n"
        source = src.encode("utf-8")
        tree = adapter.parse(source)
        file_index = bind(
            adapter,
            "src/app/binder/x.py",
            source,
            tree.root_node,
            module_path="app.binder.x",
        )
        assert import_boundaries(file_index, self.ALLOW) == []


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


from pypeeker.check.rules import PREFER_TUPLE, prefer_tuple  # noqa: E402


class TestPreferTuple:
    def _flagged(self, bind_source, src):
        return {v.message for v in prefer_tuple(bind_source(src), {})}

    def test_unmutated_local_list_flagged(self, bind_source):
        msgs = self._flagged(bind_source, "def f():\n    a = [1, 2]\n    return a[0]\n")
        assert any("'a'" in m and "tuple" in m for m in msgs)

    def test_append_mutated_not_flagged(self, bind_source):
        msgs = self._flagged(bind_source, "def f():\n    a = [1]\n    a.append(2)\n    return a\n")
        assert not any("'a'" in m for m in msgs)

    def test_subscript_mutated_not_flagged(self, bind_source):
        msgs = self._flagged(bind_source, "def f():\n    a = [1]\n    a[0] = 9\n    return a\n")
        assert not any("'a'" in m for m in msgs)

    def test_sort_mutated_not_flagged(self, bind_source):
        msgs = self._flagged(bind_source, "def f():\n    a = [3, 1]\n    a.sort()\n    return a\n")
        assert not any("'a'" in m for m in msgs)

    def test_module_level_list_out_of_scope(self, bind_source):
        msgs = self._flagged(bind_source, "COLORS = [1, 2, 3]\n")
        assert msgs == set()

    def test_comprehension_local_flagged(self, bind_source):
        msgs = self._flagged(bind_source, "def f():\n    a = [x for x in range(3)]\n    return a\n")
        assert any("'a'" in m for m in msgs)

    def test_not_in_default_rules(self):
        # prefer-tuple is available but opt-in.
        import tomllib
        from pathlib import Path
        data = tomllib.loads(Path("pyproject.toml").read_text())
        assert PREFER_TUPLE not in data["tool"]["pypeeker"]["rules"]


from pypeeker.check import CheckContext  # noqa: E402
from pypeeker.check.rules import (  # noqa: E402
    UNUSED_PUBLIC_SYMBOL,
    unused_public_symbol,
)


class TestUnusedPublicSymbol:
    def _flagged(self, indexed_project, files, options=None):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        context = CheckContext(store, indexes)
        return {v.message for v in unused_public_symbol(context, options or {})}

    def test_flags_unreferenced_public_function(self, indexed_project):
        msgs = self._flagged(
            indexed_project, {"pkg/lib.py": "def orphan():\n    return 1\n"}
        )
        assert any("'orphan'" in m for m in msgs)

    def test_flags_unreferenced_public_class(self, indexed_project):
        msgs = self._flagged(
            indexed_project, {"pkg/lib.py": "class Orphan:\n    pass\n"}
        )
        assert any("'Orphan'" in m for m in msgs)

    def test_cross_file_reference_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "def helper():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import helper\n\nhelper()\n",
            },
        )
        assert not any("'helper'" in m for m in msgs)

    def test_same_file_reference_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {"pkg/lib.py": "def helper():\n    return 1\n\nhelper()\n"},
        )
        assert not any("'helper'" in m for m in msgs)

    def test_aliased_import_use_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "def helper():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import helper as h\n\nh()\n",
            },
        )
        assert not any("'helper'" in m for m in msgs)

    def test_barrel_reexport_not_flagged(self, indexed_project):
        # Re-exported by the package __init__: deliberate public API surface.
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.lib import Widget\n",
            },
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_use_through_barrel_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.lib import Widget\n",
                "app.py": "from pkg import Widget\n\nw = Widget()\n",
            },
        )
        assert not any("'Widget'" in m for m in msgs)

    def test_non_public_not_flagged(self, indexed_project):
        msgs = self._flagged(
            indexed_project, {"pkg/lib.py": "def _hidden():\n    return 1\n"}
        )
        assert msgs == set()

    def test_methods_not_flagged(self, indexed_project):
        # Only module-level symbols are in scope; an unused class is flagged
        # once, its methods are not flagged individually.
        msgs = self._flagged(
            indexed_project,
            {"pkg/lib.py": "class Orphan:\n    def run(self):\n        return 1\n"},
        )
        assert any("'Orphan'" in m for m in msgs)
        assert not any("'run'" in m for m in msgs)

    def test_main_and_dunder_skipped(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {"pkg/cli.py": "def main():\n    return 0\n\ndef __getattr__(name):\n    return 1\n"},
        )
        assert msgs == set()

    def test_dunder_main_file_skipped(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {"pkg/__main__.py": "def entry():\n    return 0\n"},
        )
        assert msgs == set()

    def test_line_is_1_indexed(self, indexed_project):
        _, store = indexed_project({"pkg/lib.py": "\ndef orphan():\n    return 1\n"})
        indexes = [store.load(p) for p in store.list_indexed_files()]
        context = CheckContext(store, [i for i in indexes if i is not None])
        violations = unused_public_symbol(context, {})
        assert [v.line for v in violations] == [2]
        assert violations[0].rule == UNUSED_PUBLIC_SYMBOL

    def test_not_in_default_rules(self):
        # unused-public-symbol is available but opt-in.
        import tomllib
        from pathlib import Path

        data = tomllib.loads(Path("pyproject.toml").read_text())
        assert UNUSED_PUBLIC_SYMBOL not in data["tool"]["pypeeker"]["rules"]
