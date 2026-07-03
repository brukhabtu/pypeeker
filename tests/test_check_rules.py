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

    def _run(self, indexed_project, files, options):
        from pypeeker.check import CheckContext

        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        return import_boundaries(CheckContext(store, indexes), options)

    def test_flags_forbidden_cross_package_import(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from app.storage import IndexStore\n",
                "app/storage/__init__.py": "class IndexStore:\n    pass\n",
            },
            self.ALLOW,
        )
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "binder" in v.message
            and "storage" in v.message
            for v in violations
        )

    def test_allows_permitted_import(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from app.models import Symbol\n",
                "app/models/__init__.py": "class Symbol:\n    pass\n",
            },
            self.ALLOW,
        )
        assert violations == []

    def test_same_package_import_never_flagged(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from app.binder.helpers import thing\n",
                "app/binder/helpers.py": "def thing():\n    return 1\n",
            },
            self.ALLOW,
        )
        assert violations == []

    def test_external_import_ignored(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"app/binder/x.py": "import os\nfrom collections import defaultdict\n"},
            self.ALLOW,
        )
        assert violations == []

    def test_unlisted_package_is_unconstrained(self, indexed_project):
        # "weird" is not in the allow map, so it may import anything.
        violations = self._run(
            indexed_project,
            {
                "app/weird/x.py": "from app.storage import IndexStore\n",
                "app/storage/__init__.py": "class IndexStore:\n    pass\n",
            },
            self.ALLOW,
        )
        assert violations == []

    def test_root_inferred_when_omitted(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from app.storage import IndexStore\n",
                "app/storage/__init__.py": "class IndexStore:\n    pass\n",
            },
            {"allow": {"binder": ["models"]}},
        )
        assert any("storage" in v.message for v in violations)

    def test_multi_root_minority_package_still_policed(self, indexed_project):
        # With `root` omitted each file falls back to its own top-level
        # segment: a source tree with several roots (monorepo, vendored
        # package) must not exempt the smaller trees just because another
        # root has more files.
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from app.storage import IndexStore\n",
                "app/storage/__init__.py": "class IndexStore:\n    pass\n",
                "other/a.py": "x = 1\n",
                "other/b.py": "x = 1\n",
                "other/c.py": "x = 1\n",
                "other/d.py": "x = 1\n",
            },
            {"allow": {"binder": ["models"]}},
        )
        assert any(
            "binder" in v.message and "storage" in v.message for v in violations
        )

    def test_no_allow_config_is_noop(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"app/binder/x.py": "from app.storage import IndexStore\n"},
            {},
        )
        assert violations == []

    def test_line_is_1_indexed(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "\nfrom app.storage import IndexStore\n",
                "app/storage/__init__.py": "class IndexStore:\n    pass\n",
            },
            self.ALLOW,
        )
        assert violations[0].line == 2

    def test_flags_forbidden_relative_import(self, indexed_project):
        # Relative import: imported_from must be resolved to "app.storage" so the
        # layering rule sees it (TASK-58). indexed_project derives the module
        # path from the file path, so ``from ..storage import`` resolves the
        # same way it does in a real src layout.
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from ..storage import IndexStore\n",
                "app/storage/__init__.py": "class IndexStore:\n    pass\n",
            },
            self.ALLOW,
        )
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "binder" in v.message
            and "storage" in v.message
            for v in violations
        )

    def test_allows_permitted_relative_import(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "from ..models import Symbol\n",
                "app/models/__init__.py": "class Symbol:\n    pass\n",
            },
            self.ALLOW,
        )
        assert violations == []

    # ── re-export laundering & origin attribution (gaps 1 & 2) ──────────────

    def test_reexport_laundering_charged_to_origin(self, indexed_project):
        # query may import storage but NOT refactor. storage re-exports a
        # refactor symbol; importing it through the storage barrel must be
        # charged to refactor, not the literal storage package.
        violations = self._run(
            indexed_project,
            {
                "app/refactor/mod.py": "class Thing:\n    pass\n",
                "app/storage/__init__.py": "from app.refactor.mod import Thing\n",
                "app/query/engine.py": "from app.storage import Thing\n",
            },
            {"allow": {"query": ["storage"]}, "root": "app"},
        )
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "query" in v.message
            and "refactor" in v.message
            and "via re-export" in v.message
            and "app.storage.Thing" in v.message
            for v in violations
        )

    def test_direct_import_of_barrel_symbol_stays_clean(self, indexed_project):
        # storage defines Thing itself; query is allowed storage — a direct
        # import whose origin equals its literal package must not be flagged.
        violations = self._run(
            indexed_project,
            {
                "app/storage/__init__.py": "class Thing:\n    pass\n",
                "app/query/engine.py": "from app.storage import Thing\n",
            },
            {"allow": {"query": ["storage"]}, "root": "app"},
        )
        assert violations == []

    def test_symbol_imported_from_root_charged_to_origin_package(
        self, indexed_project
    ):
        # `from app import Sym` names a symbol re-exported by the ROOT __init__.
        # It must be charged to Sym's origin package (models), never reported as
        # a package literally named "Sym".
        violations = self._run(
            indexed_project,
            {
                "app/__init__.py": "from app.models.core import Sym\n",
                "app/models/core.py": "class Sym:\n    pass\n",
                "app/refactor/user.py": "from app import Sym\n",
            },
            {"allow": {"refactor": ["adapters"]}, "root": "app"},
        )
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "refactor" in v.message
            and "models" in v.message
            for v in violations
        )
        # Charged to the origin package, never to the bare symbol name "Sym".
        assert not any("may not import 'Sym'" in v.message for v in violations)

    def test_bare_root_import_is_skipped(self, indexed_project):
        # `import app` names the root package itself (no segment beneath root):
        # it maps to no package and is never flagged.
        violations = self._run(
            indexed_project,
            {
                "app/__init__.py": "x = 1\n",
                "app/refactor/user.py": "import app\n",
            },
            {"allow": {"refactor": []}, "root": "app"},
        )
        assert violations == []

    # ── strict mode for undeclared packages (gap 3) ─────────────────────────

    def test_strict_flags_undeclared_package(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "x = 1\n",
                "app/extra/y.py": "x = 1\n",
            },
            {"allow": {"binder": []}, "root": "app", "strict": True},
        )
        assert any(
            v.rule == IMPORT_BOUNDARIES
            and "extra" in v.message
            and "not declared" in v.message
            for v in violations
        )

    def test_strict_honors_unconstrained_list(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "x = 1\n",
                "app/cli/main.py": "x = 1\n",
            },
            {
                "allow": {"binder": []},
                "root": "app",
                "strict": True,
                "unconstrained": ["cli"],
            },
        )
        assert not any("not declared" in v.message for v in violations)

    def test_strict_off_ignores_undeclared_package(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/binder/x.py": "x = 1\n",
                "app/extra/y.py": "x = 1\n",
            },
            {"allow": {"binder": []}, "root": "app"},
        )
        assert violations == []

    # ── dynamic imports (gap 4) ─────────────────────────────────────────────

    def test_dynamic_string_literal_import_flagged_heuristic(self, indexed_project):
        from pypeeker.models.capabilities import Confidence

        violations = self._run(
            indexed_project,
            {
                "app/refactor/__init__.py": "x = 1\n",
                "app/query/engine.py": (
                    "import importlib\n"
                    "importlib.import_module('app.refactor')\n"
                ),
            },
            {"allow": {"query": ["storage"]}, "root": "app"},
        )
        flagged = [
            v
            for v in violations
            if "query" in v.message and "refactor" in v.message
        ]
        assert flagged
        assert all(v.confidence is Confidence.HEURISTIC for v in flagged)

    def test_dunder_import_builtin_flagged(self, indexed_project):
        from pypeeker.models.capabilities import Confidence

        violations = self._run(
            indexed_project,
            {
                "app/refactor/__init__.py": "x = 1\n",
                "app/query/engine.py": "__import__('app.refactor')\n",
            },
            {"allow": {"query": ["storage"]}, "root": "app"},
        )
        assert any(
            v.confidence is Confidence.HEURISTIC
            and "query" in v.message
            and "refactor" in v.message
            for v in violations
        )

    def test_import_module_on_other_receiver_ignored(self, indexed_project):
        # Only importlib's own import_module is an import; an unrelated
        # method that happens to share the name must not fabricate an edge.
        violations = self._run(
            indexed_project,
            {
                "app/refactor/__init__.py": "x = 1\n",
                "app/query/engine.py": (
                    "def load(registry):\n"
                    "    return registry.import_module('app.refactor')\n"
                ),
            },
            {"allow": {"query": ["storage"]}, "root": "app"},
        )
        assert violations == []

    def test_dynamic_non_literal_import_ignored(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/refactor/__init__.py": "x = 1\n",
                "app/query/engine.py": (
                    "import importlib\n"
                    "mod = 'app.refactor'\n"
                    "importlib.import_module(mod)\n"
                    "importlib.import_module(f'app.{mod}')\n"
                ),
            },
            {"allow": {"query": ["storage"]}, "root": "app"},
        )
        assert violations == []

    # ── unused-allowance reporting (gap 5) ──────────────────────────────────

    def test_unused_allowance_reported(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/storage/__init__.py": "class Thing:\n    pass\n",
                "app/query/engine.py": "from app.storage import Thing\n",
            },
            {
                "allow": {"query": ["storage", "treebuild"]},
                "root": "app",
                "report-unused-allowances": True,
            },
        )
        assert any(
            "unused import-boundaries allowance" in v.message
            and "query" in v.message
            and "treebuild" in v.message
            for v in violations
        )
        # The exercised storage allowance is not reported.
        assert not any(
            "unused" in v.message and "storage" in v.message for v in violations
        )
        # Anchored to the config, not a source file: a source-file anchor
        # would churn baseline identities whenever package files change.
        assert all(
            v.file_path == "pyproject.toml"
            for v in violations
            if "unused import-boundaries allowance" in v.message
        )

    def test_unused_allowance_off_by_default(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                "app/storage/__init__.py": "class Thing:\n    pass\n",
                "app/query/engine.py": "from app.storage import Thing\n",
            },
            {"allow": {"query": ["storage", "treebuild"]}, "root": "app"},
        )
        assert not any("unused" in v.message for v in violations)

    def test_function_level_import_exercises_allowance(self, indexed_project):
        # A function-local import is still an IMPORT symbol, so it counts as
        # exercising the allowance — mirrors query/engine.py:188 on this repo.
        violations = self._run(
            indexed_project,
            {
                "app/treebuild/__init__.py": "class T:\n    pass\n",
                "app/query/engine.py": (
                    "def build():\n    from app.treebuild import T\n    return T\n"
                ),
            },
            {
                "allow": {"query": ["treebuild"]},
                "root": "app",
                "report-unused-allowances": True,
            },
        )
        assert not any("unused" in v.message for v in violations)


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
        assert any(":orphan'" in m for m in msgs)

    def test_flags_unreferenced_public_class(self, indexed_project):
        msgs = self._flagged(
            indexed_project, {"pkg/lib.py": "class Orphan:\n    pass\n"}
        )
        assert any(":Orphan'" in m for m in msgs)

    def test_cross_file_reference_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "def helper():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import helper\n\nhelper()\n",
            },
        )
        assert not any(":helper'" in m for m in msgs)

    def test_same_file_reference_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {"pkg/lib.py": "def helper():\n    return 1\n\nhelper()\n"},
        )
        assert not any(":helper'" in m for m in msgs)

    def test_aliased_import_use_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "def helper():\n    return 1\n",
                "pkg/app.py": "from pkg.lib import helper as h\n\nh()\n",
            },
        )
        assert not any(":helper'" in m for m in msgs)

    def test_barrel_reexport_not_flagged(self, indexed_project):
        # Re-exported by the package __init__: deliberate public API surface.
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.lib import Widget\n",
            },
        )
        assert not any(":Widget'" in m for m in msgs)

    def test_use_through_barrel_counts_as_used(self, indexed_project):
        msgs = self._flagged(
            indexed_project,
            {
                "pkg/lib.py": "class Widget:\n    pass\n",
                "pkg/__init__.py": "from pkg.lib import Widget\n",
                "app.py": "from pkg import Widget\n\nw = Widget()\n",
            },
        )
        assert not any(":Widget'" in m for m in msgs)

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
        assert any(":Orphan'" in m for m in msgs)
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


from pypeeker.check.rules import (  # noqa: E402
    NO_IMPURE_FUNCTIONS,
    no_impure_functions,
)

IMPURE_SRC = "def shout(x):\n    print(x)\n    return x\n"
PURE_SRC = "def add(a, b):\n    return a + b\n"


class TestNoImpureFunctions:
    def _run(self, indexed_project, files, options):
        _, store = indexed_project(files)
        indexes = [
            idx
            for idx in (store.load(p) for p in store.list_indexed_files())
            if idx is not None
        ]
        context = CheckContext(store, indexes)
        return no_impure_functions(context, options)

    def test_impure_function_under_include_is_flagged(self, indexed_project):
        violations = self._run(
            indexed_project, {"pkg/io_stuff.py": IMPURE_SRC}, {"include": ["pkg.*"]}
        )
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == NO_IMPURE_FUNCTIONS
        assert "'pkg.io_stuff:shout' is impure" in v.message
        assert "print" in v.message
        assert v.line == 1  # def line, 1-indexed

    def test_pure_function_not_flagged(self, indexed_project):
        violations = self._run(
            indexed_project, {"pkg/math.py": PURE_SRC}, {"include": ["pkg.*"]}
        )
        assert violations == []

    def test_no_include_is_a_noop(self, indexed_project):
        # Enabling the rule without scoping it flags nothing by design.
        violations = self._run(indexed_project, {"pkg/io_stuff.py": IMPURE_SRC}, {})
        assert violations == []
        violations = self._run(
            indexed_project, {"pkg/io_stuff.py": IMPURE_SRC}, {"include": []}
        )
        assert violations == []

    def test_exclude_wins_over_include(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"pkg/io_stuff.py": IMPURE_SRC},
            {"include": ["pkg.*"], "exclude": ["pkg.io_stuff:*"]},
        )
        assert violations == []

    def test_include_matches_full_symbol_id(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"pkg/io_stuff.py": IMPURE_SRC + "\ndef other(y):\n    print(y)\n"},
            {"include": ["pkg.io_stuff:shout"]},
        )
        assert ["pkg.io_stuff:shout" in v.message for v in violations] == [True]

    def test_extra_impure_flags_custom_bare_name(self, indexed_project):
        src = "def f(x):\n    log(x)\n    return x\n"
        # Without extra-impure 'log' is just an unresolved bare name: pure.
        assert self._run(
            indexed_project, {"pkg/mod.py": src}, {"include": ["pkg.*"]}
        ) == []
        violations = self._run(
            indexed_project,
            {"pkg/mod.py": src},
            {"include": ["pkg.*"], "extra-impure": ["log"]},
        )
        assert len(violations) == 1
        assert "log" in violations[0].message

    def test_extra_impure_dotted_flags_module_call(self, indexed_project):
        src = "import mypkg\n\ndef f():\n    mypkg.db.commit()\n"
        violations = self._run(
            indexed_project,
            {"pkg/mod.py": src},
            {"include": ["pkg.*"], "extra-impure": ["mypkg.db.commit"]},
        )
        assert len(violations) == 1
        assert "mypkg.db.commit" in violations[0].message

    def test_allow_unflags_default_impure_name(self, indexed_project):
        violations = self._run(
            indexed_project,
            {"pkg/io_stuff.py": IMPURE_SRC},
            {"include": ["pkg.*"], "allow": ["print"]},
        )
        assert violations == []

    def test_transitive_impurity_flagged(self, indexed_project):
        src = (
            "def helper(x):\n    print(x)\n\n"
            "def caller(x):\n    return helper(x)\n"
        )
        violations = self._run(
            indexed_project, {"pkg/mod.py": src}, {"include": ["pkg.*"]}
        )
        flagged = {v.message.split("'")[1] for v in violations}
        assert flagged == {"pkg.mod:helper", "pkg.mod:caller"}

    def test_message_is_one_line_and_truncated(self, indexed_project):
        src = (
            "def noisy(x):\n"
            "    print(x)\n"
            "    print(x)\n"
            "    print(x)\n"
            "    print(x)\n"
            "    print(x)\n"
        )
        violations = self._run(
            indexed_project, {"pkg/mod.py": src}, {"include": ["pkg.*"]}
        )
        assert len(violations) == 1
        msg = violations[0].message
        assert "\n" not in msg
        assert "+2 more" in msg
        assert "(line 2)" in msg  # observation lines are 1-indexed

    def test_methods_in_scope(self, indexed_project):
        src = "class C:\n    def run(self, x):\n        print(x)\n"
        violations = self._run(
            indexed_project, {"pkg/mod.py": src}, {"include": ["pkg.*"]}
        )
        assert any("run" in v.message for v in violations)

    def test_not_in_default_rules(self):
        # no-impure-functions is available but opt-in.
        import tomllib
        from pathlib import Path

        data = tomllib.loads(Path("pyproject.toml").read_text())
        assert NO_IMPURE_FUNCTIONS not in data["tool"]["pypeeker"]["rules"]
