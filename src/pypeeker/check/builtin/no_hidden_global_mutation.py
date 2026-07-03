"""Builtin rule: no-hidden-global-mutation (advisory, opt-in).

pylint's ``global-statement`` warning is syntactic: it fires on the
``global`` keyword and misses every mutation that doesn't need one.
The hazards are the silent shapes, all visible in the existing
receiver/write facts:

1. **Outer-scope writes landing at module scope** — ``global x; x = 1``
   after the binder's redirect, and subscript writes like ``CACHE[k] = v``
   on a module-level container (both arrive as WRITE refs targeting a
   symbol whose parent scope is the module).
2. **Mutator calls on module-level containers** — ``ITEMS.append(x)``
   where the receiver root resolves to a module-scope VARIABLE and the
   method is in the collection-mutation table shared with the purity
   analysis (:data:`pypeeker.analysis.purity.DEFAULT_POLICY`).
3. **Attribute writes on imported modules** — ``config.value = x``
   (receiver kind IMPORT in :func:`pypeeker.analysis.writes.attribute_writes`).

Subscript stores on attribute chains (``os.environ["X"] = v``) are caught
via shape 3: the binder records them as attribute WRITEs with receiver
metadata (see TASK-101).

Options (``[tool.pypeeker.no-hidden-global-mutation]``):
    ``allow``          — fnmatch patterns matched against the function's
                         ``symbol_id`` (``"pkg.mod:func"``) or its module
                         path (``"pkg.mod"``); matching functions are
                         never flagged.
    ``extra-mutators`` — extra method names treated as in-place mutators
                         of module-level containers (joined with the
                         shared collection-mutation table).

Advisory and **opt-in** (not enabled by default): module-level state
mutated deliberately (caches, registries, memoization tables) is a
legitimate pattern; enable this rule where hidden global state is an
actual design constraint.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.analysis.calls import ReceiverKind
from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.purity import DEFAULT_POLICY
from pypeeker.analysis.writes import attribute_writes, outer_scope_writes
from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models import ReferenceKind, ScopeKind, Symbol, SymbolKind, leaf_name, strip_shadow
from pypeeker.query import SemanticQueryEngine

NO_HIDDEN_GLOBAL_MUTATION = "no-hidden-global-mutation"


@register_rule(NO_HIDDEN_GLOBAL_MUTATION, scope="project")
def _no_hidden_global_mutation(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag hidden mutations of module-level / global state inside functions."""
    allow = _as_str_list(options.get("allow"))
    mutators = DEFAULT_POLICY.collection_mutation_names | frozenset(
        _as_str_list(options.get("extra-mutators"))
    )
    engine = SemanticQueryEngine(context.store)

    violations: set[Violation] = set()
    for index in context.indexes:
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            if _matches_any(symbol.symbol_id, allow):
                continue
            ctx = AnalysisContext.for_function(
                context.store, symbol.symbol_id, engine=engine
            )
            if isinstance(ctx, ContextError):
                continue
            violations.update(_function_violations(ctx, mutators))
    return sorted(violations)


def _function_violations(
    ctx: AnalysisContext, mutators: frozenset[str]
) -> list[Violation]:
    """All hidden-global-mutation violations inside one function's body."""
    func_id = ctx.function_symbol.symbol_id
    file_path = ctx.function_symbol.location.file_path
    symbols_by_id = {s.symbol_id: s for s in ctx.file_index.symbols}

    found: list[Violation] = []

    # Shape 1a — outer-scope WRITE refs whose target lives at module scope.
    # Covers augmented assignments (`global x; x += 1`) and subscript writes
    # on module-level containers (`CACHE[k] = v`).
    for write in outer_scope_writes(ctx):
        target = symbols_by_id.get(write.target)
        if target is None or not _is_module_scope(target.parent_scope_id):
            continue
        found.append(
            Violation(
                file_path=file_path,
                line=write.line + 1,
                rule=NO_HIDDEN_GLOBAL_MUTATION,
                message=(
                    f"function '{func_id}' writes module-level "
                    f"variable '{strip_shadow(write.target)}'"
                ),
            )
        )

    # Shape 1b — plain rebinds after `global x` (`x = 1`). The binder models
    # those as a fresh (shadow) VARIABLE *definition* at module scope located
    # inside the function body, not as a WRITE reference, so we look for
    # module-scope variables whose innermost containing function scope is
    # this function.
    found.extend(_global_rebind_violations(ctx, symbols_by_id))

    # Shape 3 — attribute writes whose receiver root is an imported module.
    import_write_names = _import_attribute_write_names(ctx, symbols_by_id)
    for write in attribute_writes(ctx):
        if write.receiver_kind is not ReceiverKind.IMPORT:
            continue
        module_name = import_write_names.get(
            (write.line, write.attribute), "imported module"
        )
        found.append(
            Violation(
                file_path=file_path,
                line=write.line + 1,
                rule=NO_HIDDEN_GLOBAL_MUTATION,
                message=(
                    f"function '{func_id}' writes attribute "
                    f"'{write.attribute}' on imported module '{module_name}'"
                ),
            )
        )

    return found + _mutator_call_violations(ctx, symbols_by_id, mutators)


def _global_rebind_violations(
    ctx: AnalysisContext, symbols_by_id: dict[str, Symbol]
) -> list[Violation]:
    """Module-scope VARIABLE definitions physically inside this function.

    A ``global``-redirected plain assignment binds a (possibly shadowed)
    module-scope symbol at a location inside the function body. Attribution
    uses the innermost FUNCTION/LAMBDA scope containing the definition line,
    so nested functions report their own rebinds (not their enclosers').
    """
    function_scopes = [
        s
        for s in ctx.file_index.scopes
        if s.kind in (ScopeKind.FUNCTION, ScopeKind.LAMBDA)
    ]
    func_id = ctx.function_symbol.symbol_id
    file_path = ctx.function_symbol.location.file_path

    found: list[Violation] = []
    for symbol in symbols_by_id.values():
        if symbol.kind != SymbolKind.VARIABLE:
            continue
        if not _is_module_scope(symbol.parent_scope_id):
            continue
        line = symbol.location.span.start.line
        containing = [
            s
            for s in function_scopes
            if s.span.start.line <= line <= s.span.end.line
        ]
        if not containing:
            continue  # genuine top-level definition
        innermost = min(
            containing,
            key=lambda s: (s.span.end.line - s.span.start.line, -s.span.start.line),
        )
        if innermost.scope_id != ctx.function_scope_id:
            continue
        found.append(
            Violation(
                file_path=file_path,
                line=line + 1,
                rule=NO_HIDDEN_GLOBAL_MUTATION,
                message=(
                    f"function '{func_id}' rebinds module-level "
                    f"variable '{strip_shadow(symbol.symbol_id)}'"
                ),
            )
        )
    return found


def _mutator_call_violations(
    ctx: AnalysisContext,
    symbols_by_id: dict[str, Symbol],
    mutators: frozenset[str],
) -> list[Violation]:
    """Shape 2 — mutator method calls on module-level containers."""
    func_id = ctx.function_symbol.symbol_id
    file_path = ctx.function_symbol.location.file_path
    found: list[Violation] = []
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.CALL or not ref.is_attribute_access:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        root = symbols_by_id.get(ref.receiver_root_symbol_id or "")
        if root is None or root.kind != SymbolKind.VARIABLE:
            continue
        if not _is_module_scope(root.parent_scope_id):
            continue
        method = leaf_name(ref.symbol_id)
        if method not in mutators:
            continue
        found.append(
            Violation(
                file_path=file_path,
                line=ref.location.span.start.line + 1,
                rule=NO_HIDDEN_GLOBAL_MUTATION,
                message=(
                    f"function '{func_id}' calls '.{method}()' on "
                    f"module-level variable '{root.symbol_id}'"
                ),
            )
        )
    return found


def _import_attribute_write_names(
    ctx: AnalysisContext, symbols_by_id: dict[str, Symbol]
) -> dict[tuple[int, str], str]:
    """Map ``(line, attribute)`` of import-rooted attribute writes to the
    imported module's display name (``imported_from`` beats the local alias)."""
    names: dict[tuple[int, str], str] = {}
    for ref in ctx.file_index.references:
        if ref.kind != ReferenceKind.WRITE or not ref.is_attribute_access:
            continue
        if ref.in_scope_id not in ctx.subtree:
            continue
        root = symbols_by_id.get(ref.receiver_root_symbol_id or "")
        if root is None or root.kind != SymbolKind.IMPORT:
            continue
        key = (ref.location.span.start.line, leaf_name(ref.symbol_id))
        names[key] = root.imported_from or root.name
    return names


def _is_module_scope(scope_id: str | None) -> bool:
    """Module scope ids are bare module paths — no ``:`` segment."""
    return scope_id is not None and ":" not in scope_id


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


def _matches_any(symbol_id: str, patterns: list[str]) -> bool:
    """True when any fnmatch pattern matches the symbol_id or its module path."""
    module_path = symbol_id.split(":", 1)[0]
    return any(
        fnmatch.fnmatchcase(symbol_id, pattern)
        or fnmatch.fnmatchcase(module_path, pattern)
        for pattern in patterns
    )
