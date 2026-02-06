"""Semantic query engine for searching symbols, references, and scopes."""

from __future__ import annotations

from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol
from pypeeker.storage.store import IndexStore


class SemanticQueryEngine:
    """Provides query operations over the indexed semantic model.

    Loads file indexes on demand and answers questions about symbols,
    references, and scopes.
    """

    def __init__(self, store: IndexStore) -> None:
        self._store = store
        self._loaded_indexes: dict[str, FileIndex] = {}

    def find_symbol(self, name: str) -> list[Symbol]:
        """Find all symbols matching the given name.

        Supports:
          - Exact name match: "validate"
          - Full symbol ID match: "src/auth/service.py:AuthService.validate"
          - Partial path match: "AuthService.validate"
        """
        results: list[Symbol] = []
        for index in self._load_all_indexes():
            for symbol in index.symbols:
                if (
                    symbol.name == name
                    or symbol.symbol_id == name
                    or symbol.symbol_id.endswith(f":{name}")
                    or symbol.symbol_id.endswith(f".{name}")
                ):
                    results.append(symbol)
        return results

    def find_references(self, symbol_id: str) -> list[Reference]:
        """Find all references to a symbol across all indexed files.

        O(files) scan but simple and correct for v1.
        """
        results: list[Reference] = []
        for index in self._load_all_indexes():
            for ref in index.references:
                if ref.symbol_id == symbol_id:
                    results.append(ref)
        return results

    def get_scope_at(self, file_path: str, line: int) -> dict:
        """Show what's visible at a specific file:line location.

        Returns a dict with:
          - "scope": the innermost scope at that line
          - "visible_symbols": all symbols visible at that location
          - "scope_chain": the list of scopes from innermost to module
        """
        index = self._load_index(file_path)
        if index is None:
            return {"error": f"File not indexed: {file_path}"}

        innermost = self._find_innermost_scope(index.scopes, line)
        if innermost is None:
            return {"error": f"No scope found at {file_path}:{line}"}

        scope_chain = self._build_scope_chain(index.scopes, innermost)
        visible = self._collect_visible_symbols(
            index.scopes, index.symbols, innermost
        )

        return {
            "scope": innermost.model_dump(),
            "visible_symbols": [s.model_dump() for s in visible],
            "scope_chain": [s.model_dump() for s in scope_chain],
        }

    def _load_all_indexes(self) -> list[FileIndex]:
        """Load all indexed files."""
        indexed_files = self._store.list_indexed_files()
        indexes: list[FileIndex] = []
        for source_path in indexed_files:
            idx = self._load_index(source_path)
            if idx:
                indexes.append(idx)
        return indexes

    def _load_index(self, source_path: str) -> FileIndex | None:
        """Load and cache a single file index."""
        if source_path in self._loaded_indexes:
            return self._loaded_indexes[source_path]
        index = self._store.load(source_path)
        if index:
            self._loaded_indexes[source_path] = index
        return index

    def _find_innermost_scope(self, scopes: list[Scope], line: int) -> Scope | None:
        """Find the deepest scope that contains the given line."""
        best: Scope | None = None
        best_size = float("inf")
        for scope in scopes:
            if scope.span.start.line <= line <= scope.span.end.line:
                size = scope.span.end.line - scope.span.start.line
                if size < best_size:
                    best = scope
                    best_size = size
        return best

    def _build_scope_chain(self, scopes: list[Scope], from_scope: Scope) -> list[Scope]:
        """Build the scope chain from innermost to module."""
        scope_map = {s.scope_id: s for s in scopes}
        chain: list[Scope] = [from_scope]
        current = from_scope
        while current.parent_scope_id:
            parent = scope_map.get(current.parent_scope_id)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return chain

    def _collect_visible_symbols(
        self, scopes: list[Scope], symbols: list[Symbol], from_scope: Scope
    ) -> list[Symbol]:
        """Walk up the scope chain from `from_scope` and collect all visible symbols.

        Respects Python scoping: skip class scopes for nested lookups
        (unless we're directly inside the class).
        """
        scope_chain = self._build_scope_chain(scopes, from_scope)
        symbol_map = {s.symbol_id: s for s in symbols}
        visible: list[Symbol] = []
        seen_names: set[str] = set()

        for i, scope in enumerate(scope_chain):
            # Skip class scopes (except the innermost if we're directly in it)
            if i > 0 and scope.kind == ScopeKind.CLASS:
                continue
            for sym_id in scope.symbol_ids:
                sym = symbol_map.get(sym_id)
                if sym and sym.name not in seen_names:
                    visible.append(sym)
                    seen_names.add(sym.name)

        return visible
