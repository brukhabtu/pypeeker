"""The symbol-name match predicate behind ``SemanticQueryEngine.find_symbol``.

One place for the four-way rule a user-supplied name is matched against an
indexed symbol with, so consumers that need to reason about *why* a lookup
did or did not hit (anchor resolution, candidate suggestions) can ask the
same question the engine asks rather than re-deriving it.
"""

from __future__ import annotations

from collections.abc import Callable

from pypeeker.models import Symbol


def symbol_matcher(name: str) -> Callable[[Symbol], bool]:
    """Build the ``symbol_matches(..., name)`` predicate with its suffixes precomputed.

    :meth:`~pypeeker.query.SemanticQueryEngine.find_symbol` asks the same
    question of every symbol in the corpus, so the two anchored suffixes are
    built once here instead of once per symbol. Same result as
    :func:`symbol_matches` for every symbol.
    """
    colon_tail = f":{name}"
    dot_tail = f".{name}"

    def matches(symbol: Symbol) -> bool:
        symbol_id = symbol.symbol_id
        return (
            symbol.name == name
            or symbol_id == name
            or symbol_id.endswith(colon_tail)
            or symbol_id.endswith(dot_tail)
        )

    return matches


def symbol_matches(symbol: Symbol, name: str) -> bool:
    """Return True when ``name`` designates ``symbol`` under ``find_symbol``'s rules.

    ``name`` matches when it is:

    * the symbol's bare name (``"validate"``);
    * the full symbol id (``"src/auth/service.py:AuthService.validate"``);
    * a ``:``-anchored tail of the id (``"AuthService.validate"`` against
      ``"pkg.mod:AuthService.validate"``); or
    * a ``.``-anchored tail of the id (``"validate"`` against
      ``"pkg.mod:AuthService.validate"``, or ``"b.mod:f"`` against
      ``"a.b.mod:f"`` — the dotted-suffix match is deliberately permissive).
    """
    return symbol_matcher(name)(symbol)
