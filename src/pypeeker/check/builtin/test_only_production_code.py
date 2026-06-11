"""Builtin rule: flag production symbols referenced only from test files.

A module-level public function or class defined in production code whose
*only* project references originate from test paths is test-only API: it
either belongs next to the tests that use it, or it is production code that
quietly lost its production callers. Either way it should not look like
public production API.

Test classification is by file path: an index's ``file_path`` is matched
against the configured ``test-globs`` (fnmatch semantics, forward-slash
paths). The default globs are ``tests/**``, ``test_*.py`` and
``**/test_*.py`` — i.e. anything under a top-level ``tests/`` directory plus
``test_*.py`` modules at any depth.

Flagging requires *both* zero production references and at least one test
reference: symbols with no references at all are unused-public-symbol's
territory and are deliberately not flagged here. Symbols re-exported by a
package ``__init__`` barrel are excluded entirely — a barrel re-export is
deliberate external API surface, so in-repo reference counts say nothing
about who consumes it.

Opt-in (not enabled by default): like unused-public-symbol this rule sees
only static in-repo references, so symbols consumed dynamically or from
outside the indexed tree would be over-flagged.

Options (``[tool.pypeeker.test-only-production-code]``):
    ``test-globs`` — fnmatch patterns classifying file paths as tests.
                     Replaces the default list when given.
    ``allow``      — fnmatch patterns (matched against the ``symbol_id`` or
                     its module path) for symbols to suppress.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import _as_str_list, _matches_any, register_rule
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility

TEST_ONLY_PRODUCTION_CODE = "test-only-production-code"

DEFAULT_TEST_GLOBS: tuple[str, ...] = ("tests/**", "test_*.py", "**/test_*.py")


def _is_test_path(file_path: str, globs: list[str]) -> bool:
    """True when ``file_path`` matches any test glob (forward-slash, fnmatch)."""
    normalized = file_path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, glob) for glob in globs)


def _is_definition_site(ref: Reference, symbol: Symbol) -> bool:
    """True when ``ref`` is the symbol's own definition, not a usage."""
    if ref.kind == ReferenceKind.DEFINITION:
        return True
    return (
        ref.location.file_path == symbol.location.file_path
        and ref.location.span.start == symbol.location.span.start
    )


@register_rule(TEST_ONLY_PRODUCTION_CODE, scope="project")
def test_only_production_code(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag public production symbols whose only references come from tests.

    For each module-level public FUNCTION/CLASS defined in a non-test file,
    every project reference (resolved through import aliases, barrels, and
    attribute access by the shared resolver) is partitioned by origin path
    into production vs test references; the symbol is flagged when it has
    zero production references and at least one test reference. The
    definition site itself never counts; any other reference in the symbol's
    own (production) module counts as production use. See the module
    docstring for exclusions and options.
    """
    test_globs = _as_str_list(options.get("test-globs")) or list(DEFAULT_TEST_GLOBS)
    allow = _as_str_list(options.get("allow"))
    resolver = context.resolver

    # Canonical ids re-exported by an __init__ barrel: external API surface,
    # excluded from this rule entirely (same convention as unused-public-symbol).
    barrel_exported: set[str] = set()
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        for symbol in index.symbols:
            if symbol.kind == SymbolKind.IMPORT:
                barrel_exported.add(resolver.resolve_definition(symbol.symbol_id))

    violations: list[Violation] = []
    for index in context.indexes:
        if _is_test_path(index.file_path, test_globs):
            continue  # symbols defined in test files are out of scope
        if index.file_path.endswith("__main__.py"):
            continue
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
            None,
        )
        if module_id is None:
            continue
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.CLASS):
                continue
            if symbol.visibility is not Visibility.PUBLIC:
                continue
            if symbol.parent_scope_id != module_id:
                continue
            if symbol.name == "main" or (
                symbol.name.startswith("__") and symbol.name.endswith("__")
            ):
                continue
            if allow and _matches_any(symbol.symbol_id, allow):
                continue
            if resolver.resolve_definition(symbol.symbol_id) in barrel_exported:
                continue

            production_refs = 0
            test_refs = 0
            for ref in resolver.references_to_definition(symbol.symbol_id):
                if _is_definition_site(ref, symbol):
                    continue
                if _is_test_path(ref.location.file_path, test_globs):
                    test_refs += 1
                else:
                    production_refs += 1
            if production_refs > 0 or test_refs == 0:
                # Production use anywhere (including the symbol's own module)
                # means not test-only; zero references everywhere is
                # unused-public-symbol's job.
                continue
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=TEST_ONLY_PRODUCTION_CODE,
                    message=(
                        f"'{symbol.name}' is referenced only from tests "
                        f"({test_refs} test reference"
                        f"{'' if test_refs == 1 else 's'})"
                    ),
                )
            )
    return violations
