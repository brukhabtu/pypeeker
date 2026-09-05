"""Semantic query engine for searching symbols, references, and scopes."""

from __future__ import annotations

from pypeeker.models import (
    FileIndex,
    Reference,
    Scope,
    ScopeKind,
    Symbol,
    SymbolKind,
    TreeIndex,
    module_of,
    module_symbol_id,
    to_dict,
)
from pypeeker.query.match import symbol_matcher
from pypeeker.resolve import CrossModuleResolver, ResolvedReference
from pypeeker.storage import IndexStoreLike, TreeStoreLike


class SemanticQueryEngine:
    """Provides query operations over the indexed semantic model.

    Answers questions about symbols, references, and scopes. File indexes are
    read through an :class:`~pypeeker.storage.IndexStoreLike` store, which
    owns the single in-process per-file cache (invalidated by
    ``save()``/``remove()``); the engine keeps no per-file cache of its own,
    so a *single-file* read (:meth:`get_scope_at`) observes writes made
    through the same store.

    Caching/freshness contract: symbol and reference queries are *live*,
    derived structures are *frozen*. :meth:`all_indexes` re-reads the store
    on every call (through the store's own per-file cache, which
    ``save()``/``remove()`` invalidate), so :meth:`find_symbol` and
    :meth:`references_to_binding` observe writes made through the same
    store after the engine was built. The whole-corpus structures derived
    from that list — ``_tree``, ``_module_index`` and :meth:`resolver` —
    are built once on first use and are *not* invalidated, so a caller that
    mutates the store and then asks a resolver-backed question
    (:meth:`find_importers`, :meth:`members`, ``via``-annotated resolution)
    through the same engine sees the pre-mutation corpus.
    Construct a new engine after a mutation when both halves must agree.
    In practice the CLI refreshes stale indexes (``cli._refresh_index``)
    *before* constructing the engine, and planners and batch materializers
    build a fresh engine per plan over the store as previous intents left
    it, so the split is not observed in-tree.

    Dependency injection: the composition root (the CLI group callback) is
    expected to construct the stores and pass them in. ``tree_store`` is
    optional only for backward compatibility — when omitted, a default is
    asked of the store (``store.default_tree_store()``) once here, rather
    than derived from ``store.project_root`` inline; the engine never builds
    storage ad hoc inside query methods. That indirection matters under
    simulation: a plain :class:`~pypeeker.storage.IndexStore` hands back a
    real, disk-backed store (byte-identical to the old inline construction),
    while a simulation store (:class:`~pypeeker.storage.OverlayIndexStore`)
    hands back a non-persisting in-memory one, because its ``project_root``
    **is** the real project root and a disk-backed default there would
    persist a simulated tree into the user's real ``.pypeeker/tree.json``.
    ``get_tree`` (and :meth:`members`, which is built on it) is the only
    query method that touches the tree store at all — every other query
    method reads through ``self._store`` and is therefore overlay-correct by
    construction. An injected ``tree_store`` need only be structurally
    compatible with :class:`~pypeeker.storage.TreeStore` (a ``save``/``load``
    pair), not a subclass of it.
    """

    def __init__(
        self, store: IndexStoreLike, tree_store: TreeStoreLike | None = None
    ) -> None:
        self._store = store
        self._tree_store = (
            tree_store if tree_store is not None else store.default_tree_store()
        )
        # Engine-lifetime snapshots of derived structures (see class docstring).
        self._tree: TreeIndex | None = None
        self._module_index: dict[str, list[FileIndex]] | None = None
        self._resolver: CrossModuleResolver | None = None

    def find_symbol(self, name: str) -> list[Symbol]:
        """Find all symbols matching the given name.

        Supports:
          - Exact name match: "validate"
          - Full symbol ID match: "src/auth/service.py:AuthService.validate"
          - Partial path match: "AuthService.validate"
        """
        matches = symbol_matcher(name)
        return [
            symbol
            for index in self.all_indexes()
            for symbol in index.symbols
            if matches(symbol)
        ]

    def references_to_binding(self, symbol_id: str) -> list[Reference]:
        """References whose binding is exactly ``symbol_id`` — no resolution.

        Matches on the reference's recorded symbol id alone. Because a consumer
        module's usages bind to its *local* IMPORT symbol (not the definition
        in another module), this does **not** cross module boundaries: asking
        for a definition's id returns only same-module usages, and asking for
        an import's id returns only that module's usages of the import. Use
        :meth:`references_to_definition` to follow imports to the definition.

        O(files) scan but simple and correct for v1.
        """
        results: list[Reference] = []
        for index in self.all_indexes():
            for ref in index.references:
                if ref.symbol_id == symbol_id:
                    results.append(ref)
        return results

    def resolve_definition(self, symbol_id: str) -> str:
        """Resolve an import/alias to its canonical cross-module definition id.

        Idempotent for definitions and external imports. See
        :class:`pypeeker.resolve.CrossModuleResolver`.
        """
        return self.resolver().resolve_definition(symbol_id)

    def references_to_definition(
        self, symbol_id: str, *, declared_only: bool = False
    ) -> list[Reference]:
        """References to a *definition* across modules, following imports.

        Every reference (in any module) is resolved to its canonical
        definition and matched against the canonical definition of
        ``symbol_id``. Unlike :meth:`references_to_binding` (exact binding-id
        match, never crosses modules), this reaches usages made through
        import aliases, ``__init__.py`` re-exports, and qualified/receiver
        attribute access. With ``declared_only``, receiver resolution that
        relies on constructor-inferred types is excluded.
        """
        return self.resolver().references_to_definition(
            symbol_id, declared_only=declared_only
        )

    def references_to_definition_classified(
        self, symbol_id: str
    ) -> list[ResolvedReference]:
        """Like :meth:`references_to_definition`, with each match tagged by
        *how* it resolved — a :class:`pypeeker.resolve.ResolutionKind`:
        ``direct``, ``import_alias``, ``barrel``, ``receiver_declared``, or
        ``receiver_inferred``. Lets consumers calibrate trust per match.
        """
        return self.resolver().references_to_definition_classified(symbol_id)

    def resolver(self) -> CrossModuleResolver:
        """Return the cross-module resolver over this engine's index snapshot.

        Built once over :meth:`all_indexes` and shared for the engine's
        lifetime, so consumers that need resolution alongside the engine's
        own queries (the call graph, the class hierarchy) reuse one resolver
        instead of loading every index a second time to build their own.
        """
        if self._resolver is None:
            self._resolver = CrossModuleResolver(self.all_indexes())
        return self._resolver

    def find_importers(self, symbol_id: str) -> list[Symbol]:
        """All IMPORT symbols that resolve to the same definition as ``symbol_id``.

        A superset of :meth:`find_import_symbols`: it also catches imports
        routed through ``__init__.py`` barrels (``from pkg import X`` where the
        package re-exports ``X`` from a submodule), by resolving each import's
        canonical target rather than string-matching the module path.
        """
        resolver = self.resolver()
        canonical = resolver.resolve_definition(symbol_id)
        results: list[Symbol] = []
        for index in self.all_indexes():
            for symbol in index.symbols:
                if (
                    symbol.kind == SymbolKind.IMPORT
                    and resolver.resolve_definition(symbol.symbol_id) == canonical
                ):
                    results.append(symbol)
        return results

    def import_crosses_barrel(self, symbol_id: str) -> bool:
        """True if resolving ``symbol_id`` passes through an __init__ re-export."""
        return self.resolver().crosses_barrel(symbol_id)

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
            from pypeeker.treebuild import load_or_rebuild

            self._tree = load_or_rebuild(self._store, self._tree_store).tree
        return self._tree

    def document_symbols(self, module_path: str) -> list[dict]:
        """Top-level symbols declared in a module (excluding the module itself).

        A module id is not injective over indexed files (see
        storage-transaction-architecture.md -> Symbol IDs), so every file
        that binds ``module_path`` contributes its top-level symbols, not
        just one.
        """
        return [
            to_dict(s)
            for index in self._module_to_indexes().get(module_path, ())
            for s in index.symbols
            if s.parent_scope_id == module_path
        ]

    def members(self, symbol_id: str) -> list[dict]:
        """List the direct children of a node anywhere in the symbol tree.

        Above/at the module boundary the children come from the tree skeleton
        (subpackages + modules); a module also contributes its own top-level
        symbols. Below the module boundary, children are the nested symbols
        whose ``parent_scope_id`` points at ``symbol_id`` — read from every
        file sharing the enclosing module id (a module id is not injective
        over indexed files; see storage-transaction-architecture.md -> Symbol
        IDs), so a symbol shadowed in one colliding file still surfaces here.
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
        for index in self._module_to_indexes().get(module_path, ()):
            for s in index.symbols:
                if s.parent_scope_id == symbol_id:
                    results.append(to_dict(s))
        return results

    def _module_to_indexes(self) -> dict[str, list[FileIndex]]:
        """Map dotted module_path -> every FileIndex declaring it (cached).

        A module id is not injective over indexed files (see
        storage-transaction-architecture.md -> Symbol IDs), so the value is
        every file that binds it, in indexed-path order — keying one index
        here dropped a whole file's top-level symbols from ``members``.
        """
        if self._module_index is None:
            mapping: dict[str, list[FileIndex]] = {}
            for index in self.all_indexes():
                module_id = module_symbol_id(index)
                if module_id is not None:
                    mapping.setdefault(module_id, []).append(index)
            self._module_index = mapping
        return self._module_index

    def all_indexes(self) -> list[FileIndex]:
        """Return every indexed file's :class:`~pypeeker.models.FileIndex`, freshly loaded.

        Re-reads the store on every call (see the class docstring): the list
        is new each time, in ``list_indexed_files`` order, and reflects
        saves made through the store since the engine was built. Files
        listed as indexed whose index fails to load are skipped.
        """
        indexes: list[FileIndex] = []
        for source_path in self._store.list_indexed_files():
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
        (unless we're directly inside the class) — except for a PEP 695 type
        parameter, which lives in the class scope but is visible from nested
        method bodies, mirroring the same exception in `ScopeStack.resolve`.
        """
        scope_chain = self._build_scope_chain(scopes, from_scope)
        symbol_map = {s.symbol_id: s for s in symbols}
        visible: list[Symbol] = []
        seen_names: set[str] = set()

        for i, scope in enumerate(scope_chain):
            # Skip class scopes (except the innermost if we're directly in it)
            type_params_only = i > 0 and scope.kind == ScopeKind.CLASS
            for sym_id in scope.symbol_ids:
                sym = symbol_map.get(sym_id)
                if sym is None or sym.name in seen_names:
                    continue
                if type_params_only and sym.kind is not SymbolKind.TYPE_PARAMETER:
                    continue
                visible.append(sym)
                seen_names.add(sym.name)

        return visible
