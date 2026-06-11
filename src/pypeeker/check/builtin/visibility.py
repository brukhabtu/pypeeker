"""Visibility-detection rules: observed usage scope vs declared visibility.

The minimal-visibility principle, detection only: for every symbol, compute
the *observed* usage scope from resolved references (same module / same
package / wider) and compare it to the *declared* visibility (public name,
barrel re-export, underscore prefix). Three rules share one core:

* ``over-exposed-module-symbol`` — a public module-level symbol nothing
  outside its own module references could be ``_protected``.
* ``over-exposed-export`` — an ``__init__.py`` barrel re-export no consumer
  outside the package uses could be dropped from the public surface.
* ``under-exposed-access`` — a ``_protected`` / ``__private`` symbol *is*
  referenced from outside its defining module: either the access is a smell
  or the symbol's visibility undersells its real scope.

All three are best-effort and **opt-in** (not enabled by default): static
references are the only signal, so the over-exposure rules over-flag symbols
reached dynamically — decorator-registered handlers/entry points discovered
by framework name, ``getattr``/``globals()`` access, CLI entry points
declared in ``pyproject.toml``, and anything consumed only outside the
indexed tree. Use the ``allow`` option (and ``allow-decorators`` on
``over-exposed-module-symbol``) to carve those out.

The project-wide ``[tool.pypeeker.visibility]`` section (parsed by
:mod:`pypeeker.project`, injected into rule options by ``check.config`` under
the reserved ``visibility`` key) feeds the over-exposure rules too: library
mode protects barrel exports under the public roots, the global
``allow-decorators`` list merges with each rule's own option, and findings
about symbols defined in modules using ``getattr``/``globals``/``vars``/
``locals`` carry a low-confidence suffix.

Import discipline: this module imports only concrete ``pypeeker.check.*``
modules — importing ``pypeeker.check`` itself from a builtin rule module
recurses into the engine import and creates a cycle.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import (
    _DYNAMIC_ACCESS_SUFFIX,
    _dynamic_access_modules,
    _has_allowed_decorator,
    _merged_allow_decorators,
    _public_root_protected,
    register_rule,
)
from pypeeker.models.symbol_id import module_of
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility
from pypeeker.project import coerce_visibility

OVER_EXPOSED_MODULE_SYMBOL = "over-exposed-module-symbol"
OVER_EXPOSED_EXPORT = "over-exposed-export"
UNDER_EXPOSED_ACCESS = "under-exposed-access"

_DEFAULT_KINDS: tuple[str, ...] = ("function", "class")
"""Default symbol kinds checked by over-exposed-module-symbol."""

_KIND_CHOICES = (SymbolKind.FUNCTION, SymbolKind.CLASS, SymbolKind.VARIABLE)
"""Kinds the ``kinds`` option may select; anything else is ignored."""

_DEFAULT_TEST_GLOBS: tuple[str, ...] = (
    "tests/*",
    "*/tests/*",
    "test_*.py",
    "*/test_*.py",
    "*_test.py",
    "*/*_test.py",
    "conftest.py",
    "*/conftest.py",
)
"""Default fnmatch patterns classifying a file path as test code."""


# ── shared core: observed usage scope per canonical definition ──────────────


def _module_by_file(context: CheckContext) -> dict[str, str]:
    """Map each indexed file path to its dotted module path (MODULE symbol)."""
    out: dict[str, str] = {}
    for index in context.indexes:
        for symbol in index.symbols:
            if symbol.kind == SymbolKind.MODULE:
                out[index.file_path] = symbol.symbol_id
                break
    return out


def _usage_origins(context: CheckContext) -> dict[str, set[str]]:
    """Observed usage scope: canonical definition id -> origin module paths.

    One pass over every reference in the project, resolving each through the
    shared :class:`~pypeeker.resolve.CrossModuleResolver` (import aliases,
    barrel re-exports, qualified attribute access) and recording the dotted
    module path of the file the reference occurs in. This is the single
    "where is this symbol actually used from" computation the three
    visibility rules compare against declared visibility.
    """
    module_by_file = _module_by_file(context)
    resolver = context.resolver
    origins: dict[str, set[str]] = {}
    for index in context.indexes:
        origin = module_by_file.get(index.file_path)
        if origin is None:
            continue
        for ref in index.references:
            canonical = resolver.resolve_reference(ref)
            origins.setdefault(canonical, set()).add(origin)
    return origins


def _symbols_by_id(context: CheckContext) -> dict[str, Symbol]:
    """Every symbol in the project keyed by symbol_id."""
    return {
        symbol.symbol_id: symbol
        for index in context.indexes
        for symbol in index.symbols
    }


def _in_package(module_path: str, package: str) -> bool:
    """True when ``module_path`` is ``package`` itself or nested beneath it."""
    return module_path == package or module_path.startswith(package + ".")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


def _allowed(symbol_id: str, patterns: list[str]) -> bool:
    """True when any ``allow`` fnmatch pattern matches the symbol_id or its
    module path (same matching contract as no-impure-functions' include)."""
    module_path = module_of(symbol_id)
    return any(
        fnmatch.fnmatchcase(symbol_id, pattern)
        or fnmatch.fnmatchcase(module_path, pattern)
        for pattern in patterns
    )


def _module_id_of_index(index: Any) -> str | None:
    """The MODULE symbol's id for one FileIndex, or None."""
    return next(
        (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
        None,
    )


def _selected_kinds(raw: Any) -> frozenset[SymbolKind]:
    """Coerce the ``kinds`` option to SymbolKinds, limited to function /
    class / variable; unknown values are ignored."""
    out: set[SymbolKind] = set()
    for value in _as_str_list(raw) or list(_DEFAULT_KINDS):
        try:
            kind = SymbolKind(value)
        except ValueError:
            continue
        if kind in _KIND_CHOICES:
            out.add(kind)
    return frozenset(out)


# ── over-exposed-module-symbol ──────────────────────────────────────────────


@register_rule(OVER_EXPOSED_MODULE_SYMBOL, scope="project")
def over_exposed_module_symbol(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag public module-level symbols never referenced outside their module.

    A public name whose observed usage scope is a single module is wider than
    it needs to be: a ``_`` prefix would document the real scope. References
    inside the defining module (including the definition site) don't count as
    outside usage; a symbol with no references at all is also flagged (its
    observed scope is still at most its own module).

    Conservative exemptions (never flagged):

    * dunder names and ``main``, plus anything in a ``__main__.py``;
    * symbols re-exported by a package ``__init__`` barrel — deliberate
      public API surface, over-exposed-export's concern;
    * symbols carrying a decorator matching ``allow-decorators`` —
      registries / entry points reached by framework name, not references —
      merged with the global ``[tool.pypeeker.visibility]`` list;
    * in library mode, symbols re-exported by a barrel under a public root
      (subsumed by the barrel exemption today; kept explicit as the library
      contract).

    Findings for symbols defined in a module referencing ``getattr`` /
    ``globals`` / ``vars`` / ``locals`` carry a low-confidence suffix.

    Options:
        ``kinds``            — symbol kinds to check, from function / class /
                               variable (default function + class).
        ``allow``            — fnmatch patterns (symbol_id or module path)
                               exempting symbols.
        ``allow-decorators`` — fnmatch patterns matched against decorator
                               source text or its leading callable name.
        ``visibility``       — reserved key injected by ``check.config`` with
                               the ``[tool.pypeeker.visibility]`` table.

    Opt-in: see the module docstring for the dynamic-access caveats.
    """
    kinds = _selected_kinds(options.get("kinds"))
    allow = _as_str_list(options.get("allow"))
    vis = coerce_visibility(options.get("visibility"))
    allow_decorators = _merged_allow_decorators(options, vis)
    protected = _public_root_protected(context, vis)
    dynamic_modules = _dynamic_access_modules(context)
    resolver = context.resolver
    origins = _usage_origins(context)

    # Canonical ids re-exported by an __init__ barrel: public API surface.
    barrel_exported: set[str] = set()
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        for symbol in index.symbols:
            if symbol.kind == SymbolKind.IMPORT:
                barrel_exported.add(resolver.resolve_definition(symbol.symbol_id))

    violations: list[Violation] = []
    for index in context.indexes:
        if index.file_path.endswith("__main__.py"):
            continue
        module_id = _module_id_of_index(index)
        if module_id is None:
            continue
        for symbol in index.symbols:
            if symbol.kind not in kinds:
                continue
            if symbol.visibility is not Visibility.PUBLIC:
                continue
            if symbol.parent_scope_id != module_id:
                continue
            if symbol.name == "main" or _is_dunder(symbol.name):
                continue
            if _allowed(symbol.symbol_id, allow):
                continue
            if _has_allowed_decorator(symbol, allow_decorators):
                continue
            canonical = resolver.resolve_definition(symbol.symbol_id)
            if canonical in barrel_exported:
                continue
            if canonical in protected:
                continue  # library-mode public API (see docstring)
            outside = origins.get(canonical, set()) - {module_id}
            if outside:
                continue
            suffix = (
                _DYNAMIC_ACCESS_SUFFIX if module_id in dynamic_modules else ""
            )
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=OVER_EXPOSED_MODULE_SYMBOL,
                    message=(
                        f"public '{symbol.name}' is only used within its "
                        f"module — make it _{symbol.name}{suffix}"
                    ),
                )
            )
    return violations


# ── over-exposed-export ─────────────────────────────────────────────────────


@register_rule(OVER_EXPOSED_EXPORT, scope="project")
def over_exposed_export(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag ``__init__.py`` barrel re-exports no outside consumer uses.

    A barrel re-export — an IMPORT in a package ``__init__.py`` whose
    ``imported_from`` resolves to a definition inside that same package —
    widens the definition's surface to "anyone importing the package". When
    every reference to the re-exported definition originates *within* the
    package itself, nothing consumes that wider surface and the re-export
    can be dropped.

    Skipped: imports that don't resolve to an in-package definition
    (external / stdlib / cross-package imports living in an ``__init__``),
    and non-public import names (``_alias`` / dunder) — those aren't exports.

    In library mode (``[tool.pypeeker.visibility]``), exports of barrels
    under a public root are the library's published API: external consumers
    are invisible to the index, so those exports are never flagged. App mode
    (the default) is unchanged. Findings whose barrel module references
    ``getattr`` / ``globals`` / ``vars`` / ``locals`` carry a low-confidence
    suffix — such an ``__init__`` may serve its exports dynamically.

    Options:
        ``allow``      — fnmatch patterns (symbol_id or module path) exempting
                         exports; matched against both the export's own id
                         (e.g. ``pkg:Widget``) and the canonical definition's
                         id.
        ``visibility`` — reserved key injected by ``check.config`` with the
                         ``[tool.pypeeker.visibility]`` table.

    Opt-in: an export consumed only by code outside the indexed tree (the
    published API of a library) is invisible to this rule — see the module
    docstring; declare library mode to protect the public surface wholesale.
    """
    allow = _as_str_list(options.get("allow"))
    vis = coerce_visibility(options.get("visibility"))
    protected = _public_root_protected(context, vis)
    dynamic_modules = _dynamic_access_modules(context)
    resolver = context.resolver
    origins = _usage_origins(context)
    symbols = _symbols_by_id(context)

    violations: list[Violation] = []
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        package = _module_id_of_index(index)
        if package is None:
            continue
        for symbol in index.symbols:
            if symbol.kind != SymbolKind.IMPORT or not symbol.imported_from:
                continue
            if symbol.parent_scope_id != package:
                continue  # imports nested in functions aren't exports
            if symbol.visibility is not Visibility.PUBLIC:
                continue  # _protected / dunder import names aren't exports
            canonical = resolver.resolve_definition(symbol.symbol_id)
            target = symbols.get(canonical)
            if target is None or target.kind == SymbolKind.IMPORT:
                continue  # external / unindexed — not a barrel re-export
            if not _in_package(module_of(canonical), package):
                continue  # re-export of another package — out of scope
            if _allowed(symbol.symbol_id, allow) or _allowed(canonical, allow):
                continue
            if canonical in protected:
                continue  # library-mode public API surface
            outside = {
                origin
                for origin in origins.get(canonical, set())
                if not _in_package(origin, package)
            }
            if outside:
                continue
            suffix = (
                _DYNAMIC_ACCESS_SUFFIX if package in dynamic_modules else ""
            )
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=OVER_EXPOSED_EXPORT,
                    message=(
                        f"package '{package}' exports '{symbol.name}' but no "
                        f"outside consumer uses it — drop the re-export{suffix}"
                    ),
                )
            )
    return violations


# ── under-exposed-access ────────────────────────────────────────────────────


@register_rule(UNDER_EXPOSED_ACCESS, scope="project")
def under_exposed_access(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag cross-module references to ``_protected`` / ``__private`` symbols.

    Each resolved reference whose canonical target carries
    :attr:`~pypeeker.models.symbols.Visibility.PROTECTED` or ``PRIVATE``
    visibility *and* originates in a module other than the target's defining
    module is a reach-in: either the access should go through public surface,
    or the symbol's underscore undersells its real scope. One violation per
    reference site. Dunder names are never flagged.

    Test-file origins (classified by ``test-globs`` against the referencing
    file's path) are reported with distinct "accessed from tests" wording —
    tests reaching into private internals is a different, often more
    tolerated, smell than production reach-ins.

    Options:
        ``allow``      — fnmatch patterns (symbol_id or module path) matched
                         against the *target* definition; matching targets are
                         never flagged.
        ``test-globs`` — fnmatch patterns classifying referencing file paths
                         as test code (default: ``tests/`` directories,
                         ``test_*.py`` / ``*_test.py`` files, conftest.py).

    Opt-in like its siblings, though its signal is the more precise of the
    three: every finding is a concrete reference site, not an absence of one.
    """
    allow = _as_str_list(options.get("allow"))
    test_globs = _as_str_list(options.get("test-globs")) or list(
        _DEFAULT_TEST_GLOBS
    )
    module_by_file = _module_by_file(context)
    resolver = context.resolver
    symbols = _symbols_by_id(context)

    violations: list[Violation] = []
    for index in context.indexes:
        origin = module_by_file.get(index.file_path)
        if origin is None:
            continue
        path = index.file_path.replace("\\", "/")
        from_tests = any(
            fnmatch.fnmatchcase(path, pattern) for pattern in test_globs
        )
        for ref in index.references:
            canonical = resolver.resolve_reference(ref)
            target = symbols.get(canonical)
            if target is None:
                continue
            if target.visibility not in (
                Visibility.PROTECTED,
                Visibility.PRIVATE,
            ):
                continue  # PUBLIC and DUNDER excluded
            if _is_dunder(target.name):
                continue
            target_module = module_of(canonical)
            if target_module == origin:
                continue
            if _allowed(canonical, allow):
                continue
            if from_tests:
                detail = f"accessed from tests ('{origin}')"
            else:
                detail = (
                    f"accessed from '{origin}' outside its defining "
                    f"module '{target_module}'"
                )
            violations.append(
                Violation(
                    file_path=ref.location.file_path,
                    line=ref.location.span.start.line + 1,
                    rule=UNDER_EXPOSED_ACCESS,
                    message=(
                        f"{target.visibility.value} '{target.name}' {detail}"
                    ),
                )
            )
    return violations
