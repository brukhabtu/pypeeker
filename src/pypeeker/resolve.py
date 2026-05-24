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
        # container scope_id -> {member name -> symbol}. Because a class's
        # scope_id equals its symbol_id and module-level symbols have
        # parent_scope_id == the module path, this one map covers both module
        # members and class members.
        self._members: dict[str, dict[str, Symbol]] = {}
        self._references: list[Reference] = []
        self._cache: dict[str, str] = {}

        for index in indexes:
            self._references.extend(index.references)
            for symbol in index.symbols:
                self._symbols[symbol.symbol_id] = symbol
                if symbol.kind == SymbolKind.MODULE:
                    self._modules.add(symbol.symbol_id)

        for symbol in self._symbols.values():
            if symbol.parent_scope_id is not None:
                self._members.setdefault(symbol.parent_scope_id, {})[
                    symbol.name
                ] = symbol

    _UNRESOLVED_PREFIX = "<unresolved>."

    def resolve_reference(self, ref: Reference) -> str:
        """Canonical definition a reference binds to, resolving qualified access.

        For a single-hop attribute access (``receiver.attr``) the binder leaves
        an ``<unresolved>.attr`` id but records the receiver root; if that root
        resolves to a known module or class/enum, the attribute is resolved to
        that container's member. All other references fall back to
        :meth:`resolve_definition` of their symbol id.
        """
        sid = ref.symbol_id
        if (
            sid.startswith(self._UNRESOLVED_PREFIX)
            and ref.receiver_root_symbol_id is not None
            and ref.receiver_chain is not None
            and len(ref.receiver_chain) == 1
        ):
            container = self.resolve_definition(ref.receiver_root_symbol_id)
            attr = sid[len(self._UNRESOLVED_PREFIX):]
            member = self._members.get(container, {}).get(attr)
            if member is not None:
                return self.resolve_definition(member.symbol_id)
        return self.resolve_definition(sid)

    def resolve_definition(self, symbol_id: str) -> str:
        """Return the canonical definition id for ``symbol_id``.

        Follows IMPORT -> definition transitively (including barrel
        re-exports). Idempotent for definitions and for external/stdlib
        imports, which resolve to themselves.
        """
        if symbol_id in self._cache:
            return self._cache[symbol_id]
        result = self._resolve_chain(symbol_id)[-1]
        self._cache[symbol_id] = result
        return result

    def crosses_barrel(self, symbol_id: str) -> bool:
        """True if resolving ``symbol_id`` traverses a re-export in an __init__.

        A barrel consumer (``from pkg import X`` where ``pkg/__init__.py``
        re-exports ``X`` from a submodule) resolves *through* the package's
        ``__init__`` import; a direct import (``from pkg.sub import X``) does
        not. This lets rename gate barrel-routed import updates behind
        ``--include-exports``, since rewriting such an import is only valid
        once the re-export it depends on is also updated.
        """
        chain = self._resolve_chain(symbol_id)
        # The last id is the definition; every prior id is an import hop.
        for hop_id in chain[:-1]:
            symbol = self._symbols.get(hop_id)
            if (
                symbol is not None
                and symbol.kind == SymbolKind.IMPORT
                and symbol.location.file_path.endswith("__init__.py")
            ):
                return True
        return False

    def _resolve_chain(self, symbol_id: str) -> list[str]:
        """Walk IMPORT -> definition, returning every id visited in order.

        The final element is the canonical definition (or the last reachable
        node for external/circular cases); earlier elements are the import
        hops traversed to get there.
        """
        chain: list[str] = []
        seen: set[str] = set()
        current = symbol_id

        while True:
            chain.append(current)
            if current in seen:
                break  # circular re-export — stop at the last node seen
            seen.add(current)

            symbol = self._symbols.get(current)
            if symbol is None or symbol.kind != SymbolKind.IMPORT:
                break
            imported_from = symbol.imported_from
            if not imported_from:
                break

            # ``import pkg.mod`` / ``import pkg.mod as m`` — the import names a
            # module directly.
            if imported_from in self._modules:
                chain.append(imported_from)
                break

            module_part, _, name = imported_from.rpartition(".")
            if not module_part or module_part not in self._modules:
                break  # external / stdlib / unindexed module

            target = self._members.get(module_part, {}).get(name)
            if target is None:
                break  # name not found in the defining module
            current = target.symbol_id

        return chain

    def find_all_references(self, symbol_id: str) -> list[Reference]:
        """Every reference across the project that binds to a definition.

        Includes direct references, those made through import aliases and
        barrel re-exports, and qualified attribute/method access — any
        reference whose resolved canonical definition matches that of
        ``symbol_id``.
        """
        canonical = self.resolve_definition(symbol_id)
        return [
            ref
            for ref in self._references
            if self.resolve_reference(ref) == canonical
        ]
