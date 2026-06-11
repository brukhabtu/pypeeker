"""Builtin rule: unused-return-value (advisory, opt-in).

A function with a **declared** non-None return annotation whose result is
discarded at every project call site is a procedure pretending to be a
function — or every caller is buggy. Either way the declaration and the
usage disagree, and that's worth a look.

The signal is the binder's ``result_used`` fact on CALL references
(:class:`pypeeker.models.references.Reference`): ``False`` when the call (or
``await <call>``) is itself a bare expression statement, ``True`` everywhere
else. Call sites are attributed to definitions through the shared
:class:`~pypeeker.resolve.CrossModuleResolver`, so calls via import aliases,
barrel re-exports, ``self.method()``, and typed-receiver chains all count.

Conservative exclusions:

* functions/methods without a declared return annotation, or annotated
  ``-> None`` — nothing claims a value exists;
* dunder-named symbols — their results flow through protocols, not call
  expressions;
* functions with **zero** resolved calls — dead-code rules own that case;
* functions that are also used as *values* (READ / DECORATOR references):
  calls through the alias or the decoration result are invisible to us, so
  we don't claim every call discards the result.

Options (``[tool.pypeeker.unused-return-value]``):
    ``allow`` — fnmatch patterns matched against the function's
                ``symbol_id`` (``"pkg.mod:func"``) or its module path
                (``"pkg.mod"``); matching functions are never flagged.

Advisory and **opt-in** (not enabled by default): some APIs legitimately
return a value only for chaining/convenience (``dict.setdefault``-style),
and a small project may simply not use a helper's result *yet*.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models.capabilities import Confidence
from pypeeker.models.references import ReferenceKind
from pypeeker.models.symbols import Symbol, SymbolKind

UNUSED_RETURN_VALUE = "unused-return-value"

_MAX_CALL_SITES_IN_MESSAGE = 3

# Reference kinds meaning "the function escapes as a value": calls through
# the resulting alias (or the decoration result) are invisible, so a
# candidate with any of these is skipped.
_VALUE_ESCAPE_KINDS = (ReferenceKind.READ, ReferenceKind.DECORATOR)


@register_rule(UNUSED_RETURN_VALUE, scope="project")
def _unused_return_value(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag declared-non-None functions whose every call site discards the result."""
    allow = _as_str_list(options.get("allow"))
    resolver = context.resolver

    # One pass over all references: canonical-definition-id -> call sites,
    # plus the set of definitions that escape as values.
    call_sites: dict[str, list[tuple[str, int, bool]]] = {}
    escapes: set[str] = set()
    for index in context.indexes:
        for ref in index.references:
            if ref.kind == ReferenceKind.CALL:
                canonical = resolver.resolve_reference(ref)
                call_sites.setdefault(canonical, []).append(
                    (
                        ref.location.file_path,
                        ref.location.span.start.line + 1,
                        ref.result_used,
                    )
                )
            elif ref.kind in _VALUE_ESCAPE_KINDS:
                escapes.add(resolver.resolve_reference(ref))

    violations: list[Violation] = []
    for index in context.indexes:
        for symbol in index.symbols:
            if not _is_candidate(symbol):
                continue
            if _matches_any(symbol.symbol_id, allow):
                continue
            canonical = resolver.resolve_definition(symbol.symbol_id)
            if canonical in escapes:
                continue
            sites = call_sites.get(canonical, [])
            if not sites:  # zero calls: dead-code territory, not ours
                continue
            if any(used for _, _, used in sites):
                continue
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=UNUSED_RETURN_VALUE,
                    message=(
                        f"{symbol.kind.value} '{symbol.symbol_id}' declares "
                        f"return type '{symbol.type_annotation.raw}' but all "
                        f"{len(sites)} call site(s) discard the result "
                        f"({_summarize_sites(sites)})"
                    ),
                )
            )
    return sorted(violations)


def _is_candidate(symbol: Symbol) -> bool:
    """FUNCTION/METHOD with a declared non-None return annotation, not a dunder."""
    if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
        return False
    ann = symbol.type_annotation
    if ann is None or ann.confidence is not Confidence.DECLARED:
        return False
    if _is_none_annotation(ann.raw):
        return False
    name = symbol.name
    return not (name.startswith("__") and name.endswith("__"))


def _is_none_annotation(raw: str) -> bool:
    """True for ``-> None`` including the string-annotation spellings."""
    return raw.strip() in ("None", '"None"', "'None'")


def _summarize_sites(sites: list[tuple[str, int, bool]]) -> str:
    """First few call sites as ``file:line``, plus a ``+N more`` tail."""
    shown = sites[:_MAX_CALL_SITES_IN_MESSAGE]
    parts = [f"{file_path}:{line}" for file_path, line, _ in shown]
    remaining = len(sites) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return ", ".join(parts)


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
