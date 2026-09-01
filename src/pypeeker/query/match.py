"""The symbol-name match predicate behind ``SemanticQueryEngine.find_symbol``.

One place for the four-way rule a user-supplied name is matched against an
indexed symbol with, so consumers that need to reason about *why* a lookup
did or did not hit (anchor resolution, candidate suggestions) can ask the
same question the engine asks rather than re-deriving it.
"""

from __future__ import annotations

from pypeeker.models import Symbol


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
    return (
        symbol.name == name
        or symbol.symbol_id == name
        or symbol.symbol_id.endswith(f":{name}")
        or symbol.symbol_id.endswith(f".{name}")
    )
