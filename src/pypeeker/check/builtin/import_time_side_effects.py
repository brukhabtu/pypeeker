"""Builtin check rule: imports must be free of side effects.

Importing a module executes its top level, so any module-scope call with a
side effect (I/O, network, time, subprocess, ...) turns ``import mod`` into
an action. Class bodies execute at import time too, so calls there count.

The rule flags three call shapes at import time:

1. bare/builtin calls matching the impure-builtin purity policy
   (``open(...)``, ``print(...)``);
2. module-qualified calls matching the module-impure purity policy
   (``subprocess.run(...)``, ``time.time()``, ``logging.basicConfig(...)``);
3. calls to project-internal functions whose purity analysis
   (:func:`pypeeker.analysis.purity.impurities`) finds impurities.

Options (``[tool.pypeeker.import-time-side-effects]``):
    ``allow``        — fnmatch patterns; a call is suppressed when a pattern
                       matches the called name (bare name, dotted qualified
                       name, or project symbol_id) **or** the calling module
                       path (so whole modules can be exempted, e.g.
                       ``"myapp.settings"``). Merged with the default
                       allowlist (:data:`DEFAULT_ALLOW`): ``logging.getLogger``,
                       ``warnings.filterwarnings``, ``warnings.simplefilter``
                       are conventional, benign import-time calls.
    ``extra-impure`` — extra names treated as impure: dotted names join the
                       module denylist (``"mypkg.db.commit"``), bare names
                       join the builtin denylist (``"log"``).

Opt-in (not enabled by default): purity analysis is heuristic and many
codebases deliberately do import-time work (CLI registration, settings).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.analysis.observations import Observations
from pypeeker.analysis.purity import DEFAULT_POLICY, PurityPolicy, impurities
from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import _impurity_confidence, register_rule
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference, ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbol_id import (
    builtin_name,
    is_builtin,
    is_unresolved_attr,
    unresolved_attr_name,
)
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.query.engine import SemanticQueryEngine

IMPORT_TIME_SIDE_EFFECTS = "import-time-side-effects"

DEFAULT_ALLOW: tuple[str, ...] = (
    "logging.getLogger",
    "warnings.filterwarnings",
    "warnings.simplefilter",
)
"""Conventional, benign import-time calls that are never flagged.

These are the standard idioms for module-level setup (``logger =
logging.getLogger(__name__)``, filter configuration) and stay allowed even
when ``extra-impure`` would otherwise match them. User ``allow`` patterns
extend (never replace) this list.
"""

# Scope kinds whose bodies execute when their *parent* executes. A class body
# or a comprehension at module scope (or nested in such a class) runs at
# import time; function and lambda bodies only run when called.
_DEFERRED_FREE_KINDS = (ScopeKind.CLASS, ScopeKind.COMPREHENSION)


@register_rule(IMPORT_TIME_SIDE_EFFECTS, scope="project")
def _import_time_side_effects(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag import-time calls with side effects ("imports must be free").

    See the module docstring for the three call shapes and the options.
    Emits one violation per offending call with a 1-indexed line.
    """
    policy = _configured_policy(options)
    allow = list(DEFAULT_ALLOW) + _as_str_list(options.get("allow"))

    project_functions = _project_functions(context)
    engine = SemanticQueryEngine(context.store)
    impurity_cache: dict[str, Observations | None] = {}

    violations: list[Violation] = []
    for index in context.indexes:
        module_path = _module_path(index)
        symbols_by_id = {s.symbol_id: s for s in index.symbols}
        import_time_scopes = _import_time_scope_ids(index)
        for ref in index.references:
            if ref.kind != ReferenceKind.CALL:
                continue
            if ref.in_scope_id not in import_time_scopes:
                continue
            described = _describe_call(
                ref, symbols_by_id, policy, context, project_functions,
                engine, impurity_cache,
            )
            if described is None:
                continue
            name, why, confidence = described
            if _allowed(name, module_path, allow):
                continue
            violations.append(
                Violation(
                    file_path=ref.location.file_path,
                    line=ref.location.span.start.line + 1,
                    rule=IMPORT_TIME_SIDE_EFFECTS,
                    message=f"import-time call to '{name}' {why}",
                    confidence=confidence,
                )
            )
    return violations


def _describe_call(
    ref: Reference,
    symbols_by_id: dict[str, Symbol],
    policy: PurityPolicy,
    context: CheckContext,
    project_functions: dict[str, Symbol],
    engine: SemanticQueryEngine,
    impurity_cache: dict[str, Observations | None],
) -> tuple[str, str, Confidence] | None:
    """Classify one import-time CALL reference; None when it looks free.

    Returns ``(name, why, confidence)`` for the violation: the called name
    (bare name, dotted qualified name, or project symbol_id — also what
    ``allow`` patterns match), the reason it is considered a side effect,
    and a confidence tier. Builtin-resolved and import-rooted matches are
    ``DECLARED``; a *bare unresolved* name matching the policy is
    ``HEURISTIC`` (a star-import or free name we merely matched by name);
    project-function impurity inherits the verdict's own confidence.
    """
    # Shape 1: bare/builtin call matching the impure-builtin policy.
    bare = _bare_call_name(ref)
    if bare is not None:
        if bare in policy.impure_builtins:
            confidence = (
                Confidence.DECLARED if is_builtin(ref.symbol_id)
                else Confidence.HEURISTIC
            )
            return bare, "matches the impure-builtin policy", confidence
        return None
    # Shape 2: module-qualified call matching the module-impure policy.
    qualified = _qualified_call_name(ref, symbols_by_id)
    if qualified is not None and qualified in policy.module_impure_names:
        return qualified, "matches the impure-call policy", Confidence.DECLARED
    # Shape 3: call to a project-internal function that is impure.
    canonical = context.resolver.resolve_reference(ref)
    target = project_functions.get(canonical)
    if target is None:
        return None
    if canonical not in impurity_cache:
        impurity_cache[canonical] = impurities(
            context.store, canonical, engine=engine, policy=policy
        )
    found = impurity_cache[canonical]
    if found:
        return (
            canonical,
            f"resolves to an impure project {target.kind.value}",
            _impurity_confidence(found),
        )
    return None


def _bare_call_name(ref: Reference) -> str | None:
    """The bare name of a builtin/unresolved-bare call, else None.

    Mirrors :func:`pypeeker.analysis.calls.bare_calls`: builtins resolve to
    ``<builtins>.X``; star-import / free names stay unresolved without a
    ``:``. Anything resolved to a project symbol is a different shape.
    """
    sid = ref.symbol_id
    if is_builtin(sid):
        return builtin_name(sid)
    if not ref.resolved and ":" not in sid and not is_unresolved_attr(sid):
        return sid
    return None


def _qualified_call_name(
    ref: Reference, symbols_by_id: dict[str, Symbol]
) -> str | None:
    """Dotted qualified name of an IMPORT-rooted attribute call, else None.

    Mirrors :func:`pypeeker.analysis.calls.module_calls` (which needs a
    function-scoped AnalysisContext, so the assembly is replicated here for
    module scope): ``imported_from + chain[1:] + leaf``, catching aliased
    imports like ``import os as o``.
    """
    if ref.receiver_root_symbol_id is None or ref.receiver_chain is None:
        return None
    root = symbols_by_id.get(ref.receiver_root_symbol_id)
    if root is None or root.kind != SymbolKind.IMPORT or not root.imported_from:
        return None
    leaf = _call_leaf(ref)
    if leaf is None:
        return None
    return ".".join([root.imported_from, *ref.receiver_chain[1:], leaf])


def _call_leaf(ref: Reference) -> str | None:
    """Leaf method name of an attribute call, or None for non-attribute refs.

    Same shape handling as ``pypeeker.analysis.calls._leaf_method``.
    """
    sid = ref.symbol_id
    if is_unresolved_attr(sid):
        return unresolved_attr_name(sid)
    if ref.is_attribute_access:
        if "." in sid:
            return sid.rsplit(".", 1)[-1]
        if ":" in sid:
            return sid.rsplit(":", 1)[-1]
    return None


def _import_time_scope_ids(file_index: FileIndex) -> frozenset[str]:
    """Scope ids whose statements execute when the module is imported.

    The module scope itself, plus CLASS and COMPREHENSION scopes all of whose
    enclosing scopes also execute at import time. Function and lambda bodies
    break the chain — they only run when called.
    """
    by_id = {scope.scope_id: scope for scope in file_index.scopes}
    cache: dict[str, bool] = {}

    def runs_at_import(scope_id: str) -> bool:
        """True if every ancestor of the scope executes at import time."""
        if scope_id in cache:
            return cache[scope_id]
        scope = by_id.get(scope_id)
        if scope is None:
            result = False
        elif scope.kind == ScopeKind.MODULE:
            result = True
        elif scope.kind in _DEFERRED_FREE_KINDS:
            result = scope.parent_scope_id is not None and runs_at_import(
                scope.parent_scope_id
            )
        else:
            result = False
        cache[scope_id] = result
        return result

    return frozenset(sid for sid in by_id if runs_at_import(sid))


def _project_functions(context: CheckContext) -> dict[str, Symbol]:
    """Every FUNCTION/METHOD symbol in the project, keyed by symbol_id."""
    return {
        symbol.symbol_id: symbol
        for index in context.indexes
        for symbol in index.symbols
        if symbol.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
    }


def _module_path(file_index: FileIndex) -> str:
    """Dotted module path of a file index (its module scope id), or ''."""
    for scope in file_index.scopes:
        if scope.kind == ScopeKind.MODULE:
            return scope.scope_id
    return ""


def _allowed(name: str, module_path: str, patterns: list[str]) -> bool:
    """True when any allow pattern matches the call name or calling module."""
    return any(
        fnmatch.fnmatchcase(name, pattern)
        or fnmatch.fnmatchcase(module_path, pattern)
        for pattern in patterns
    )


def _configured_policy(options: Mapping[str, Any]) -> PurityPolicy:
    """Extend the default purity policy with ``extra-impure`` names.

    Dotted names join the module denylist, bare names the builtin denylist.
    ``allow`` is *not* folded into the policy: allow patterns are applied
    rule-side (fnmatch over call names and module paths), which subsumes the
    exact-name policy removal and additionally supports wildcards.
    """
    extra = _as_str_list(options.get("extra-impure"))
    if not extra:
        return DEFAULT_POLICY
    return DEFAULT_POLICY.extended(
        extra_impure_builtins=[name for name in extra if "." not in name],
        extra_module_impure=[name for name in extra if "." in name],
    )


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]
