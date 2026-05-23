"""Semantic query engine for searching symbols, references, and scopes."""

from __future__ import annotations

from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.serialize import to_dict
from pypeeker.models.tree import TreeIndex
from pypeeker.resolve import CrossModuleResolver
from pypeeker.storage import IndexStore, TreeStore


class SemanticQueryEngine:
    """Provides query operations over the indexed semantic model.

    Loads file indexes on demand and answers questions about symbols,
    references, and scopes.
    """

    def __init__(self, store: IndexStore) -> None:
        self._store = store
        self._loaded_indexes: dict[str, FileIndex] = {}
        self._tree: TreeIndex | None = None
        self._module_index: dict[str, FileIndex] | None = None
        self._resolver: CrossModuleResolver | None = None

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

    def resolve_definition(self, symbol_id: str) -> str:
        """Resolve an import/alias to its canonical cross-module definition id.

        Idempotent for definitions and external imports. See
        :class:`pypeeker.resolve.CrossModuleResolver`.
        """
        return self._get_resolver().resolve_definition(symbol_id)

    def find_all_references(self, symbol_id: str) -> list[Reference]:
        """Find references to a definition across modules, following imports.

        Unlike :meth:`find_references` (exact symbol-id match), this reaches
        usages made through import aliases and ``__init__.py`` re-exports.
        """
        return self._get_resolver().find_all_references(symbol_id)

    def _get_resolver(self) -> CrossModuleResolver:
        if self._resolver is None:
            self._resolver = CrossModuleResolver(self._load_all_indexes())
        return self._resolver

    def find_importers(self, symbol_id: str) -> list[Symbol]:
        """All IMPORT symbols that resolve to the same definition as ``symbol_id``.

        A superset of :meth:`find_import_symbols`: it also catches imports
        routed through ``__init__.py`` barrels (``from pkg import X`` where the
        package re-exports ``X`` from a submodule), by resolving each import's
        canonical target rather than string-matching the module path.
        """
        resolver = self._get_resolver()
        canonical = resolver.resolve_definition(symbol_id)
        results: list[Symbol] = []
        for index in self._load_all_indexes():
            for symbol in index.symbols:
                if (
                    symbol.kind == SymbolKind.IMPORT
                    and resolver.resolve_definition(symbol.symbol_id) == canonical
                ):
                    results.append(symbol)
        return results

    def import_crosses_barrel(self, symbol_id: str) -> bool:
        """True if resolving ``symbol_id`` passes through an __init__ re-export."""
        return self._get_resolver().crosses_barrel(symbol_id)

    def find_import_symbols(self, symbol_id: str) -> list[Symbol]:
        """Find all IMPORT symbols that import the given definition.

        For example, if renaming "lib.py:helper", this finds all IMPORT symbols
        with imported_from="lib.helper".

        Args:
            symbol_id: The definition being renamed (e.g., "lib.py:helper")

        Returns:
            List of IMPORT symbols that import this definition.
        """
        if ":" not in symbol_id:
            return []

        # Parse symbol_id: "lib.py:helper" -> file="lib.py", name="helper"
        # Also handles "lib.py:Foo.bar" -> file="lib.py", name="bar"
        file_path, remainder = symbol_id.split(":", 1)
        symbol_name = remainder.split(".")[-1]

        # Convert file path to module path: "lib.py" -> "lib", "pkg/mod.py" -> "pkg.mod"
        module_name = file_path.removesuffix(".py").replace("/", ".")

        # Expected imported_from for imports of this symbol
        expected_import_path = f"{module_name}.{symbol_name}"

        results: list[Symbol] = []
        for index in self._load_all_indexes():
            for symbol in index.symbols:
                if (
                    symbol.kind == SymbolKind.IMPORT
                    and symbol.imported_from == expected_import_path
                ):
                    results.append(symbol)

        return results

    def find_reexport_locations(self, symbol_id: str) -> list[Location]:
        """Find locations in __init__.py files that re-export the symbol.

        This is used by --include-exports to update barrel files when renaming.
        Returns the location of the imported name (for editing).

        Args:
            symbol_id: The definition being renamed (e.g., "models/user.py:User")

        Returns:
            List of Locations where the symbol is re-exported in __init__.py files.
        """
        from pypeeker.models.location import Location

        results: list[Location] = []

        # Find all import symbols that import this definition
        import_symbols = self.find_import_symbols(symbol_id)

        for imp in import_symbols:
            # Only include __init__.py files (barrel exports)
            if not imp.location.file_path.endswith("__init__.py"):
                continue

            # Use imported_name_location for aliased imports, otherwise use location
            loc = imp.imported_name_location or imp.location
            results.append(loc)

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
            "scope": to_dict(innermost),
            "visible_symbols": [to_dict(s) for s in visible],
            "scope_chain": [to_dict(s) for s in scope_chain],
        }

    def get_tree(self) -> TreeIndex:
        """Return the cross-file package/module tree, fresh on first read.

        The tree is rebuilt incrementally against the per-file indexes and
        cached for the lifetime of this engine.
        """
        if self._tree is None:
            from pypeeker.tree import load_or_rebuild

            tree_store = TreeStore(self._store.project_root)
            self._tree = load_or_rebuild(self._store, tree_store).tree
        return self._tree

    def document_symbols(self, module_path: str) -> list[dict]:
        """Top-level symbols declared in a module (excluding the module itself)."""
        index = self._module_to_index().get(module_path)
        if index is None:
            return []
        return [
            to_dict(s)
            for s in index.symbols
            if s.parent_scope_id == module_path
        ]

    def members(self, symbol_id: str) -> list[dict]:
        """List the direct children of a node anywhere in the symbol tree.

        Above/at the module boundary the children come from the tree skeleton
        (subpackages + modules); a module also contributes its own top-level
        symbols. Below the module boundary, children are the nested symbols
        whose ``parent_scope_id`` points at ``symbol_id``.
        """
        tree = self.get_tree()
        node = tree.nodes.get(symbol_id)
        if node is not None:
            results = [to_dict(tree.nodes[child_id]) for child_id in node.children]
            if node.file_path is not None:
                results.extend(self.document_symbols(symbol_id))
            return results

        results: list[dict] = []
        module_path = symbol_id.split(":", 1)[0]
        index = self._module_to_index().get(module_path)
        if index is not None:
            for s in index.symbols:
                if s.parent_scope_id == symbol_id:
                    results.append(to_dict(s))
        return results

    def _module_to_index(self) -> dict[str, FileIndex]:
        """Map dotted module_path -> its FileIndex (cached)."""
        if self._module_index is None:
            mapping: dict[str, FileIndex] = {}
            for index in self._load_all_indexes():
                for symbol in index.symbols:
                    if symbol.kind == SymbolKind.MODULE:
                        mapping[symbol.symbol_id] = index
                        break
            self._module_index = mapping
        return self._module_index

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
