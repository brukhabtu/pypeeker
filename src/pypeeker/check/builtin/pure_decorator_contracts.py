"""Builtin rule: pure-decorator-contracts.

Caching or memoizing an impure function is a bug — ``@lru_cache`` over a
function that reads the clock or hits the filesystem freezes the first
answer forever. Likewise, ``@property`` getters and comparison /
representation dunders (``__eq__``, ``__repr__``, ...) carry an implicit
purity contract: callers assume evaluating them has no side effects.

This project-scoped rule composes two existing facts — symbols carry their
raw decorator text, and :func:`pypeeker.analysis.purity.impurities` walks a
function's body (and its transitive project-internal calls) for impurity
observations — into two checks over FUNCTION / METHOD symbols:

1. **Decorator contract** — a function decorated with a configured
   pure-contract decorator (default ``cache`` / ``lru_cache`` /
   ``cached_property`` / ``property``, with or without a module prefix such
   as ``functools.`` and with or without call parentheses) whose
   ``impurities()`` result is non-empty.
2. **Dunder contract** — a method whose name is a configured dunder
   (default ``__eq__`` / ``__hash__`` / ``__repr__`` / ``__str__`` /
   ``__len__``) that is impure.

Options (``[tool.pypeeker.pure-decorator-contracts]``):
    ``decorators`` — override the pure-contract decorator list. Names match
                     the decorator's leading dotted name or its last
                     segment, so ``cache`` covers ``functools.cache`` and a
                     configured ``functools.cache`` matches only the
                     prefixed spelling.
    ``dunders``    — override the contract-dunder list.
    ``allow``      — fnmatch patterns over the symbol_id
                     (``"pkg.mod:Cls.meth"``); matching symbols are never
                     flagged.

Opt-in (not enabled by default): purity analysis is heuristic, so false
positives are possible (e.g. a property memoizing into a private attribute
on ``self``).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.analysis.observations import Observations
from pypeeker.analysis.purity import impurities
from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import _impurity_confidence, register_rule
from pypeeker.models import SymbolKind
from pypeeker.query import SemanticQueryEngine

PURE_DECORATOR_CONTRACTS = "pure-decorator-contracts"

DEFAULT_DECORATORS: tuple[str, ...] = (
    "cache",
    "lru_cache",
    "cached_property",
    "property",
)

DEFAULT_DUNDERS: tuple[str, ...] = (
    "__eq__",
    "__hash__",
    "__repr__",
    "__str__",
    "__len__",
)

_MAX_OBSERVATIONS_IN_MESSAGE = 3


@register_rule(PURE_DECORATOR_CONTRACTS, scope="project")
def pure_decorator_contracts(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag impure functions under pure-contract decorators or dunder names."""
    decorators = frozenset(
        _as_str_list(options.get("decorators")) or DEFAULT_DECORATORS
    )
    dunders = frozenset(_as_str_list(options.get("dunders")) or DEFAULT_DUNDERS)
    allow = _as_str_list(options.get("allow"))
    engine = SemanticQueryEngine(context.store)

    violations: list[Violation] = []
    for index in context.indexes:
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            contract = _contract_for(symbol, decorators, dunders)
            if contract is None:
                continue
            if any(
                fnmatch.fnmatchcase(symbol.symbol_id, pattern)
                for pattern in allow
            ):
                continue
            found = impurities(context.store, symbol.symbol_id, engine=engine)
            if not found:  # None (unanalyzable) or empty (pure)
                continue
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=PURE_DECORATOR_CONTRACTS,
                    message=(
                        f"{symbol.kind.value} '{symbol.symbol_id}' violates "
                        f"the {contract} purity contract: "
                        f"{_summarize_observations(found)}"
                    ),
                    confidence=_impurity_confidence(found),
                )
            )
    return violations


def _contract_for(
    symbol: Any, decorators: frozenset[str], dunders: frozenset[str]
) -> str | None:
    """Name the purity contract ``symbol`` is under, or None.

    Decorator contracts win over dunder contracts when both apply (e.g. an
    ``@property``-decorated ``__len__`` is reported once, as ``@property``).
    """
    for raw in symbol.decorators:
        head = raw.split("(", 1)[0].strip()
        if head in decorators or head.rsplit(".", 1)[-1] in decorators:
            return f"@{head}"
    if symbol.kind is SymbolKind.METHOD and symbol.name in dunders:
        return f"{symbol.name}"
    return None


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


def _summarize_observations(found: Observations) -> str:
    """One-line summary: first few observation kinds/names, 1-indexed lines."""
    shown = list(found)[:_MAX_OBSERVATIONS_IN_MESSAGE]
    parts = [_describe_observation(obs) for obs in shown]
    remaining = len(found) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return "; ".join(parts)


def _describe_observation(obs: Any) -> str:
    """Render one observation as ``Kind 'name' (line N)`` (line 1-indexed)."""
    name = None
    for attr in ("name", "qualified_name", "method", "target", "attribute", "callee"):
        value = getattr(obs, attr, None)
        if value is not None:
            name = value
            break
    label = type(obs).__name__
    if name is not None:
        label = f"{label} '{name}'"
    line = getattr(obs, "line", None)
    if line is not None:
        label = f"{label} (line {line + 1})"
    return label
