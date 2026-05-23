"""Cross-module resolution.

References to imported names bind to the *local* import symbol (the binder is
per-file and can't see other modules). This resolver closes that gap: given an
import, it follows ``imported_from`` — through any number of ``__init__.py``
barrel re-exports — to the canonical definition's symbol id, using the dotted
module paths established by the symbol tree.
"""

from __future__ import annotations

from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference
from pypeeker.models.symbols import Symbol, SymbolKind


class CrossModuleResolver:
    """Resolve import aliases and re-exports to canonical definition ids."""

    def __init__(self, indexes: list[FileIndex]) -> None:
        self._symbols: dict[str, Symbol] = {}
        self._modules: set[str] = set()
        # module_path -> {declared name -> module-level symbol}
        self._module_names: dict[str, dict[str, Symbol]] = {}
        self._references: list[Reference] = []
        self._cache: dict[str, str] = {}

        for index in indexes:
            self._references.extend(index.references)
            for symbol in index.symbols:
                self._symbols[symbol.symbol_id] = symbol
                if symbol.kind == SymbolKind.MODULE:
                    self._modules.add(symbol.symbol_id)

        for symbol in self._symbols.values():
            if symbol.parent_scope_id in self._modules:
                self._module_names.setdefault(symbol.parent_scope_id, {})[
                    symbol.name
                ] = symbol

    def resolve_definition(self, symbol_id: str) -> str:
        """Return the canonical definition id for ``symbol_id``.

        Follows IMPORT -> definition transitively (including barrel
        re-exports). Idempotent for definitions and for external/stdlib
        imports, which resolve to themselves.
        """
        if symbol_id in self._cache:
            return self._cache[symbol_id]

        origin = symbol_id
        visited: set[str] = set()
        current = symbol_id

        while True:
            if current in visited:
                break  # circular re-export — stop at the last node seen
            visited.add(current)

            symbol = self._symbols.get(current)
            if symbol is None or symbol.kind != SymbolKind.IMPORT:
                break
            imported_from = symbol.imported_from
            if not imported_from:
                break

            # ``import pkg.mod`` / ``import pkg.mod as m`` — the import names a
            # module directly.
            if imported_from in self._modules:
                current = imported_from
                break

            module_part, _, name = imported_from.rpartition(".")
            if not module_part or module_part not in self._modules:
                break  # external / stdlib / unindexed module

            target = self._module_names.get(module_part, {}).get(name)
            if target is None:
                break  # name not found in the defining module
            current = target.symbol_id

        self._cache[origin] = current
        return current

    def find_all_references(self, symbol_id: str) -> list[Reference]:
        """Every reference across the project that binds to a definition.

        Includes direct references plus those made through import aliases and
        barrel re-exports — any reference whose resolved canonical definition
        matches that of ``symbol_id``.
        """
        canonical = self.resolve_definition(symbol_id)
        return [
            ref
            for ref in self._references
            if self.resolve_definition(ref.symbol_id) == canonical
        ]
