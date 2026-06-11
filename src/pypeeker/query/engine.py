"""Semantic query engine for searching symbols, references, and scopes."""

from __future__ import annotations

from pypeeker.models.index import FileIndex
from pypeeker.models.references import Reference
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.models.symbol_id import module_of
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.serialize import to_dict
from pypeeker.models.tree import TreeIndex
from pypeeker.resolve import CrossModuleResolver, ResolvedReference
from pypeeker.storage import IndexStore, TreeStore


class SemanticQueryEngine:
    """Provides query operations over the indexed semantic model.

    Answers questions about symbols, references, and scopes. File indexes are
    read through :class:`pypeeker.storage.IndexStore`, which owns the single
    in-process cache (invalidated by ``save()``/``remove()``); the engine keeps
    no per-file index cache of its own, so per-file reads observe writes made
    through the same store.

    Caching/freshness contract: an engine instance is a snapshot view. The
    derived structures it memoizes (``_tree``, ``_module_index``, ``_resolver``)
    are built lazily from the indexes as of their first use and are *not*
    invalidated when the store changes afterwards — queries are consistent as
    of first load. Construct a new engine to pick up index changes. In
    practice the CLI refreshes stale indexes (``cli._refresh_index``) *before*
    constructing the engine, so the snapshot lifetime matches the command
    lifetime.

    Dependency injection: the composition root (the CLI group callback) is
    expected to construct the stores and pass them in. ``tree_store`` is
    optional only for backward compatibility — when omitted, a default is
    derived once here from ``store.project_root``; the engine never builds
    storage ad hoc inside query methods.
    """

    def __init__(self, store: IndexStore, tree_store: TreeStore | None = None) -> None:
        self._store = store
        self._tree_store = (
            tree_store if tree_store is not None else TreeStore(store.project_root)
        )
        # Engine-lifetime snapshots of derived structures (see class docstring).
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

    def find_all_references(
        self, symbol_id: str, *, declared_only: bool = False
    ) -> list[Reference]:
        """Find references to a definition across modules, following imports.

        Unlike :meth:`find_references` (exact symbol-id match), this reaches
        usages made through import aliases, ``__init__.py`` re-exports, and
        qualified/receiver attribute access. With ``declared_only``, receiver
        resolution that relies on constructor-inferred types is excluded.
        """
        return self._get_resolver().find_all_references(
            symbol_id, declared_only=declared_only
        )

    def find_all_references_classified(
        self, symbol_id: str
    ) -> list[ResolvedReference]:
        """Like :meth:`find_all_references`, with each match tagged by *how*
        it resolved — a :class:`pypeeker.resolve.ResolutionKind`: ``direct``,
        ``import_alias``, ``barrel``, ``receiver_declared``, or
        ``receiver_inferred``. Lets consumers calibrate trust per match.
        """
        return self._get_resolver().find_all_references_classified(symbol_id)

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

    def get_scope_at(self, file_path: str, line: int) -> dict:
        """Show what's visible at a specific file:line location.

        Returns a dict with:
          - "scope": the innermost scope at that line
          - "visible_symbols": all symbols visible at that location
          - "scope_chain": the list of scopes from innermost to module
        """
        index = self._store.load(file_path)
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

            self._tree = load_or_rebuild(self._store, self._tree_store).tree
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
        module_path = module_of(symbol_id)
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
        """Load all indexed files, reading through the store's cache."""
        indexed_files = self._store.list_indexed_files()
        indexes: list[FileIndex] = []
        for source_path in indexed_files:
            idx = self._store.load(source_path)
            if idx:
                indexes.append(idx)
        return indexes

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
