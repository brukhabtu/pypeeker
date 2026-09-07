"""Tests for the individual check rules."""

from __future__ import annotations

from pypeeker.dsl import Finding
from pypeeker.models import Confidence
from tests.conftest import run_dsl_rule_on_store

IMPORT_BOUNDARIES = "import-boundaries"
NO_UNRESOLVED_REFS = "no-unresolved-refs"
REQUIRE_DOCSTRINGS = "require-docstrings"


class TestRequireDocstrings:
    def test_flags_public_function_without_docstring(self, run_dsl_rule):
        violations = run_dsl_rule(REQUIRE_DOCSTRINGS, {"test.py": "def foo():\n    return 1\n"}, {})
        assert any(
            v.rule == REQUIRE_DOCSTRINGS and "foo" in v.message for v in violations
        )

    def test_ignores_documented_function(self, run_dsl_rule):
        violations = run_dsl_rule(REQUIRE_DOCSTRINGS, {"test.py": 'def foo():\n    """ok"""\n    return 1\n'}, {})
        assert [v for v in violations if "foo" in v.message] == []

    def test_ignores_protected_by_default(self, run_dsl_rule):
        violations = run_dsl_rule(REQUIRE_DOCSTRINGS, {"test.py": "def _hidden():\n    return 1\n"}, {})
        assert [v for v in violations if "_hidden" in v.message] == []

    def test_visibility_option_widens_scope(self, run_dsl_rule):
        violations = run_dsl_rule(REQUIRE_DOCSTRINGS, {"test.py": "def _hidden():\n    return 1\n"}, {"visibility": ["public", "protected"]})
        assert any("_hidden" in v.message for v in violations)

    def test_kinds_option_narrows_scope(self, run_dsl_rule):
        src = "class Foo:\n    pass\n\ndef bar():\n    return 1\n"
        violations = run_dsl_rule(REQUIRE_DOCSTRINGS, {"test.py": src}, {"kinds": ["class"]})
        flagged = {v.message for v in violations}
        assert any("Foo" in m for m in flagged)
        assert not any("bar" in m for m in flagged)

    def test_line_number_is_1_indexed(self, run_dsl_rule):
        violations = run_dsl_rule(REQUIRE_DOCSTRINGS, {"test.py": "\n\ndef foo():\n    return 1\n"}, {})
        foo_v = next(v for v in violations if "foo" in v.message)
        assert foo_v.line == 3


class TestNoUnresolvedRefs:
    def test_flags_genuinely_unresolved(self, run_dsl_rule):
        violations = run_dsl_rule(NO_UNRESOLVED_REFS, {"test.py": "def foo():\n    return totally_undefined\n"}, {})
        assert any(v.rule == NO_UNRESOLVED_REFS for v in violations)
        assert any("totally_undefined" in v.message for v in violations)

    def test_does_not_flag_builtins(self, run_dsl_rule):
        # After TASK-21 builtins resolve as <builtins>.X with resolved=True,
        # so no_unresolved_refs should not fire on them.
        violations = run_dsl_rule(NO_UNRESOLVED_REFS, {"test.py": "def foo(x):\n    return len(x)\n"}, {})
        assert not any("len" in v.message for v in violations)

    def test_skips_unresolved_attribute_chains(self, bind_source, store):
        from pypeeker.models import Location, Position, Span
        from pypeeker.models import Reference, ReferenceKind

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
        store.save(file_index)
        violations = run_dsl_rule_on_store(NO_UNRESOLVED_REFS, store)
        assert not any("<unresolved>" in v.message for v in violations)


class TestImportBoundaries:
    ALLOW = {"allow": {"binder": ["models"]}, "root": "app"}

    def _run(self, indexed_project, files, options):
        _, store = indexed_project(files)
        return run_dsl_rule_on_store(IMPORT_BOUNDARIES, store, options)

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
        from pypeeker.models import Confidence

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
        from pypeeker.models import Confidence

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
            v.path == "pyproject.toml"
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


class TestFindingFormat:
    def test_str_format_matches_ruff_mypy(self):
        v = Finding(
            rule="require-docstrings",
            path="src/x.py",
            line=12,
            message="public function 'foo' has no docstring",
            confidence=Confidence.DECLARED,
        )
        assert (
            str(v)
            == "src/x.py:12: [require-docstrings] public function 'foo' has no docstring"
        )


PREFER_TUPLE = "prefer-tuple"


class TestPreferTuple:
    def _flagged(self, run_dsl_rule, src):
        return {
            v.message for v in run_dsl_rule(PREFER_TUPLE, {"test.py": src}, {})
        }

    def test_unmutated_local_list_flagged(self, run_dsl_rule):
        msgs = self._flagged(run_dsl_rule, "def f():\n    a = [1, 2]\n    return a[0]\n")
        assert any("'a'" in m and "tuple" in m for m in msgs)

    def test_append_mutated_not_flagged(self, run_dsl_rule):
        msgs = self._flagged(run_dsl_rule, "def f():\n    a = [1]\n    a.append(2)\n    return a\n")
        assert not any("'a'" in m for m in msgs)

    def test_subscript_mutated_not_flagged(self, run_dsl_rule):
        msgs = self._flagged(run_dsl_rule, "def f():\n    a = [1]\n    a[0] = 9\n    return a\n")
        assert not any("'a'" in m for m in msgs)

    def test_sort_mutated_not_flagged(self, run_dsl_rule):
        msgs = self._flagged(run_dsl_rule, "def f():\n    a = [3, 1]\n    a.sort()\n    return a\n")
        assert not any("'a'" in m for m in msgs)

    def test_module_level_list_out_of_scope(self, run_dsl_rule):
        msgs = self._flagged(run_dsl_rule, "COLORS = [1, 2, 3]\n")
        assert msgs == set()

    def test_comprehension_local_flagged(self, run_dsl_rule):
        # A comprehension-bound list used only locally (iterated) is flaggable;
        # returning it would escape and is covered by the escape tests below.
        msgs = self._flagged(
            run_dsl_rule,
            "def f():\n    a = [x for x in range(3)]\n    for y in a:\n        print(y)\n",
        )
        assert any("'a'" in m for m in msgs)

    def _flags_a(self, run_dsl_rule, body):
        return any("'a'" in m for m in self._flagged(run_dsl_rule, "def f(y, other):\n" + body))

    # ── escaping uses must NOT be flagged (the fix would be unsafe) ──────────

    def test_returned_list_not_flagged(self, run_dsl_rule):
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    return a\n")

    def test_yielded_list_not_flagged(self, run_dsl_rule):
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    yield a\n")

    def test_list_passed_to_call_not_flagged(self, run_dsl_rule):
        # The callee may mutate it (e.g. heapq.heappush) or depend on list-ness.
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    other.append(a)\n")

    def test_aliased_list_not_flagged(self, run_dsl_rule):
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    b = a\n    b.append(3)\n")

    def test_concatenated_list_not_flagged(self, run_dsl_rule):
        # tuple + list raises; a list used with ``+`` must not become a tuple.
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    c = a + other\n")

    def test_compared_to_value_not_flagged(self, run_dsl_rule):
        # (1, 2) == [1, 2] is False — comparison result would change.
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    b = a == other\n")

    def test_copy_method_not_flagged(self, run_dsl_rule):
        # tuples have no .copy(); a list-only method call must exclude it.
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    b = a.copy()\n")

    def test_count_method_not_flagged(self, run_dsl_rule):
        # Even a tuple-shared method (.count) reads a at the attribute position,
        # which is conservatively treated as escaping.
        assert not self._flags_a(run_dsl_rule, "    a = [1, 2]\n    n = a.count(1)\n")

    # ── local, read-only uses SHOULD be flagged (the fix is safe) ───────────

    def test_iterated_list_flagged(self, run_dsl_rule):
        assert self._flags_a(run_dsl_rule, "    a = [1, 2]\n    for x in a:\n        print(x)\n")

    def test_membership_only_flagged(self, run_dsl_rule):
        assert self._flags_a(run_dsl_rule, "    a = [1, 2]\n    if y in a:\n        pass\n")

    def test_subscript_read_only_flagged(self, run_dsl_rule):
        assert self._flags_a(run_dsl_rule, "    a = [1, 2]\n    x = a[0]\n")

    def test_truthiness_only_flagged(self, run_dsl_rule):
        assert self._flags_a(run_dsl_rule, "    a = [1, 2]\n    if a:\n        return 1\n")

    def test_unused_local_list_flagged(self, run_dsl_rule):
        # Never read at all — trivially safe to tuplify.
        assert self._flags_a(run_dsl_rule, "    a = [1, 2]\n    return 0\n")

    def test_one_escaping_use_disqualifies(self, run_dsl_rule):
        # Local iteration AND a return: the escaping return read wins.
        assert not self._flags_a(
            run_dsl_rule, "    a = [1, 2]\n    for x in a:\n        pass\n    return a\n"
        )

    # ── explicit annotations (DECLARED, not INFERRED) must not be flagged ───
    # TASK-128 oracle: pins current behavior before the type-annotation trait
    # migration — the three-clause predicate already excludes a DECLARED
    # annotation, since only Confidence.INFERRED list literals are candidates.

    def test_explicitly_annotated_list_not_flagged(self, run_dsl_rule):
        # `a: list = [...]` records Confidence.DECLARED (binder/assignments.py),
        # so it must never be a prefer-tuple candidate even though it is never
        # mutated and never escapes.
        assert not self._flags_a(run_dsl_rule, "    a: list = [1, 2]\n    return a[0]\n")

    def test_non_list_local_not_flagged(self, run_dsl_rule):
        msgs = self._flagged(run_dsl_rule, "def f():\n    n = 5\n    return n\n")
        assert not any("'n'" in m for m in msgs)

    def test_not_in_default_rules(self):
        # prefer-tuple is available but not gated on pypeeker (advisory; its
        # autofix changes list return contracts). See architecture.md.
        import tomllib
        from pathlib import Path
        data = tomllib.loads(Path("pyproject.toml").read_text())
        assert PREFER_TUPLE not in data["tool"]["pypeeker"]["rules"]


UNUSED_PUBLIC_SYMBOL = "unused-public-symbol"


class TestUnusedPublicSymbol:
    def _flagged(self, indexed_project, files, options=None):
        _, store = indexed_project(files)
        return {
            v.message
            for v in run_dsl_rule_on_store(UNUSED_PUBLIC_SYMBOL, store, options)
        }

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
        violations = run_dsl_rule_on_store(UNUSED_PUBLIC_SYMBOL, store)
        assert [v.line for v in violations] == [2]
        assert violations[0].rule == UNUSED_PUBLIC_SYMBOL

    def test_enabled_in_default_rules(self):
        # unused-public-symbol is a zero-finding hard gate in the curated self-lint.
        import tomllib
        from pathlib import Path

        data = tomllib.loads(Path("pyproject.toml").read_text())
        assert UNUSED_PUBLIC_SYMBOL in data["tool"]["pypeeker"]["rules"]


NO_IMPURE_FUNCTIONS = "no-impure-functions"

IMPURE_SRC = "def shout(x):\n    print(x)\n    return x\n"
PURE_SRC = "def add(a, b):\n    return a + b\n"


class TestNoImpureFunctions:
    def _run(self, indexed_project, files, options):
        _, store = indexed_project(files)
        return run_dsl_rule_on_store(NO_IMPURE_FUNCTIONS, store, options)

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

    def test_enabled_in_default_rules(self):
        # no-impure-functions is a zero-finding hard gate in the self-lint suite.
        import tomllib
        from pathlib import Path

        data = tomllib.loads(Path("pyproject.toml").read_text())
        assert NO_IMPURE_FUNCTIONS in data["tool"]["pypeeker"]["rules"]


BARREL_ONLY = "barrel-only"


class TestBarrelOnly:
    ROOT = {"root": "app"}

    # A curated barrel: app.refactor re-exports RenamePlanner and declares
    # __all__. The internal module app.refactor.planner defines it.
    _BARREL_FILES = {
        "app/refactor/__init__.py": (
            "from app.refactor.planner import RenamePlanner\n"
            '__all__ = ["RenamePlanner"]\n'
        ),
        "app/refactor/planner.py": "class RenamePlanner:\n    pass\n",
    }

    def _load(self, indexed_project, files):
        _, store = indexed_project(files)
        return store

    def _run(self, indexed_project, files, options=None):
        store = self._load(indexed_project, files)
        return run_dsl_rule_on_store(BARREL_ONLY, store, options or self.ROOT)

    def test_cross_package_deep_import_of_barrel_symbol_is_flagged(
        self, indexed_project
    ):
        violations = self._run(
            indexed_project,
            {
                **self._BARREL_FILES,
                "app/query/engine.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                ),
            },
        )
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == BARREL_ONLY
        assert v.path == "app/query/engine.py"
        assert v.line == 1
        assert "RenamePlanner" in v.message
        assert "app.refactor" in v.message
        assert "app.refactor.planner" in v.message

    def test_equivalent_barrel_import_is_clean(self, indexed_project):
        violations = self._run(
            indexed_project,
            {
                **self._BARREL_FILES,
                "app/query/engine.py": "from app.refactor import RenamePlanner\n",
            },
        )
        assert violations == []

    def test_same_package_deep_import_is_clean(self, indexed_project):
        # A refactor module reaching its own sibling submodule is fine — the
        # barrel-only contract is about *cross-package* coupling.
        violations = self._run(
            indexed_project,
            {
                **self._BARREL_FILES,
                "app/refactor/other.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                ),
            },
        )
        assert violations == []

    def test_deep_import_of_package_without_all_barrel_is_clean(
        self, indexed_project
    ):
        # The __init__ re-exports the name but declares no __all__, so it is
        # not a curated barrel: demanding one is not this rule's job.
        violations = self._run(
            indexed_project,
            {
                "app/refactor/__init__.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                ),
                "app/refactor/planner.py": "class RenamePlanner:\n    pass\n",
                "app/query/engine.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                ),
            },
        )
        assert violations == []

    def test_deep_import_of_name_barrel_does_not_export_is_clean(
        self, indexed_project
    ):
        # The barrel curates RenamePlanner only; deep-importing a different
        # name from the same internal module has no valid barrel path.
        violations = self._run(
            indexed_project,
            {
                "app/refactor/__init__.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                    '__all__ = ["RenamePlanner"]\n'
                ),
                "app/refactor/planner.py": (
                    "class RenamePlanner:\n    pass\n\n\n"
                    "class InternalOnly:\n    pass\n"
                ),
                "app/query/engine.py": (
                    "from app.refactor.planner import InternalOnly\n"
                ),
            },
        )
        assert violations == []

    def test_dynamic_synthetic_import_is_ignored(self, indexed_project):
        # A deep import the binder recovered dynamically carries
        # import_confidence; those are out of scope even when they would
        # otherwise match a barrel re-export.
        from pypeeker.models import Confidence
        from pypeeker.models import SymbolKind

        store = self._load(
            indexed_project,
            {
                **self._BARREL_FILES,
                "app/query/engine.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                ),
            },
        )
        # Sanity: this is exactly the flagged case before the guard applies.
        assert run_dsl_rule_on_store(BARREL_ONLY, store, self.ROOT)
        # Mark the deep import as dynamically recovered and re-run. The DSL
        # corpus reads indexes from the store, so the edit is written back
        # rather than made to an in-memory list the rule never sees.
        for path in store.list_indexed_files():
            index = store.load(path)
            if index is None:
                continue
            for symbol in index.symbols:
                if (
                    symbol.kind is SymbolKind.IMPORT
                    and symbol.imported_from == "app.refactor.planner.RenamePlanner"
                ):
                    symbol.import_confidence = Confidence.HEURISTIC
            store.save(index)
        assert run_dsl_rule_on_store(BARREL_ONLY, store, self.ROOT) == []

    def test_root_inferred_when_omitted(self, indexed_project):
        # With no root option each file falls back to its own top segment,
        # so the cross-package deep import is still policed.
        violations = self._run(
            indexed_project,
            {
                **self._BARREL_FILES,
                "app/query/engine.py": (
                    "from app.refactor.planner import RenamePlanner\n"
                ),
            },
            options={},
        )
        assert len(violations) == 1
        assert "RenamePlanner" in violations[0].message

    def test_enabled_in_repo_default_rules(self):
        # Unlike the advisory rules, barrel-only is enabled repo-wide.
        # Anchor to the repo root (not cwd) so a chdir'd sibling test can't
        # make this read some tmp project's pyproject.toml.
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        assert BARREL_ONLY in data["tool"]["pypeeker"]["rules"]
