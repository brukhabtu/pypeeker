"""Rename planner: creates transaction plans for symbol renames."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pypeeker.models.location import Location
from pypeeker.models.references import Reference
from pypeeker.models.symbols import Symbol
from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    FileRenameEntry,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.storage import IndexStore, TransactionStore


class RenamePlanError(Exception):
    """Raised when a rename plan cannot be created."""


class RenamePlanner:
    """Creates a transactional rename plan.

    Usage:
        planner = RenamePlanner(index_store, transaction_store)
        summary = planner.plan("src/auth/service.py:AuthService", "AccountService")
    """

    def __init__(
        self,
        index_store: IndexStore,
        transaction_store: TransactionStore,
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(
        self,
        symbol_id: str,
        new_name: str,
        *,
        include_file: bool = False,
        include_exports: bool = False,
    ) -> TransactionSummary:
        """Create a rename plan and persist it as a transaction."""
        # 1. Resolve symbol
        symbol = self._resolve_symbol(symbol_id)
        old_name = symbol.name

        if old_name == new_name:
            raise RenamePlanError(f"New name is same as old name: {new_name}")

        # 2. Validate new name
        self._validate_new_name(symbol, new_name)

        # 3. Find the import symbols that bind this definition into other
        #    modules, applying the --include-exports filter for __init__.py
        #    re-exports. Each import is its own symbol, distinct from the
        #    definition.
        #
        #    Design tradeoff (see TASK-31): a barrel (__init__.py re-export) is
        #    a deliberate *public API surface*, so "rename the definition" and
        #    "rename the public export" are different intents. Renaming the def
        #    does not necessarily mean the exported name should change — keeping
        #    it stable (e.g. `from pkg.lib import NewName as X`) is a legitimate
        #    goal. --include-exports currently conflates the two: it rewrites the
        #    export to the new name. A cleaner future split would keep this flag
        #    for "propagate through barrels" and add a separate alias-preserving
        #    mode for "rename the def but hold the public name", rather than
        #    overloading one flag with both meanings.
        #
        #    Gating: a direct import (`from pkg.sub import X`) is always
        #    updated. An import that lives in an __init__.py, or a barrel
        #    consumer whose resolution passes *through* an __init__ re-export
        #    (`from pkg import X`), is part of the re-export surface and only
        #    sound to rewrite when the re-export itself is updated — so it is
        #    gated on --include-exports.
        imports_to_edit: list[Symbol] = []
        for imp in self._engine.find_importers(symbol.symbol_id):
            on_export_surface = imp.location.file_path.endswith(
                "__init__.py"
            ) or self._engine.import_crosses_barrel(imp.symbol_id)
            if on_export_surface and not include_exports:
                continue
            imports_to_edit.append(imp)

        # 4. Collect references that bind to the definition itself or to an
        #    import we are renaming. A consumer's call site binds to its local
        #    import symbol, so it is reached via that import's id — not the
        #    definition's. This keeps each module internally consistent: we
        #    only rename usages whose binding import is also being renamed.
        #    Aliased usages bind to a renamed import too, but their token
        #    differs from old_name and is dropped by the text guard in
        #    _build_edits, so the alias is preserved.
        binding_ids = {symbol.symbol_id} | {imp.symbol_id for imp in imports_to_edit}
        references: list[Reference] = []
        for binding_id in binding_ids:
            references.extend(self._engine.find_references(binding_id))

        # 5. Collect edit locations: definition + references + import tokens.
        edit_locations: list[Location] = [symbol.location]
        for ref in references:
            edit_locations.append(ref.location)
        for imp in imports_to_edit:
            # Use imported_name_location for aliased imports (e.g.
            # "from lib import helper as h") so we rename "helper", not "h".
            loc = imp.imported_name_location or imp.location
            edit_locations.append(loc)

        # 5. Check affected files are indexed and not stale
        affected_files = {loc.file_path for loc in edit_locations}
        self._validate_files(affected_files)

        # 6. Convert to EditEntry objects with byte offsets
        edits = self._build_edits(edit_locations, old_name, new_name)

        if not edits:
            raise RenamePlanError(
                f"No edits could be generated for renaming '{old_name}' to '{new_name}'. "
                "The symbol locations may not contain the expected text."
            )

        # 6b. Check for file rename (--include-file)
        file_rename: FileRenameEntry | None = None
        if include_file:
            file_rename = self._check_file_rename(symbol, new_name)
            if file_rename:
                affected_files.add(file_rename.new_path)

        # 7. Generate transaction
        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol.symbol_id,
            old_name=old_name,
            new_name=new_name,
            created_at=datetime.now(timezone.utc).isoformat(),
            include_file=include_file,
            include_exports=include_exports,
        )

        self._transaction_store.save(header, edits, file_rename)

        return TransactionSummary(
            tx_id=tx_id,
            operation="rename",
            symbol_id=symbol.symbol_id,
            old_name=old_name,
            new_name=new_name,
            files_affected=sorted(affected_files),
            edit_count=len(edits) + (1 if file_rename else 0),
            created_at=header.created_at,
        )

    def _resolve_symbol(self, symbol_id: str) -> Symbol:
        """Find exactly one symbol matching the given ID."""
        results = self._engine.find_symbol(symbol_id)
        if not results:
            raise RenamePlanError(f"Symbol not found: {symbol_id}")
        if len(results) > 1:
            ids = [s.symbol_id for s in results]
            raise RenamePlanError(
                f"Ambiguous symbol '{symbol_id}', matched {len(results)}: {ids}. "
                "Use the full symbol ID to disambiguate."
            )
        return results[0]

    def _validate_new_name(self, symbol: Symbol, new_name: str) -> None:
        """Check the new name is valid and does not conflict in the same scope."""
        if not new_name.isidentifier():
            raise RenamePlanError(f"Invalid Python identifier: {new_name}")

        if symbol.parent_scope_id:
            index = self._index_store.load(symbol.location.file_path)
            if index:
                for s in index.symbols:
                    if (
                        s.parent_scope_id == symbol.parent_scope_id
                        and s.name == new_name
                        and s.symbol_id != symbol.symbol_id
                    ):
                        raise RenamePlanError(
                            f"Name conflict: '{new_name}' already exists in scope "
                            f"'{symbol.parent_scope_id}' as {s.symbol_id}"
                        )

    def _validate_files(self, file_paths: set[str]) -> None:
        """Ensure all affected files are indexed and not stale."""
        for fp in file_paths:
            if self._index_store.is_stale(fp):
                raise RenamePlanError(
                    f"File '{fp}' is stale or not indexed. "
                    "Run 'pypeeker index' first."
                )

    def _build_edits(
        self,
        locations: list[Location],
        old_name: str,
        new_name: str,
    ) -> list[EditEntry]:
        """Convert Location objects to EditEntry objects with byte offsets."""
        file_contents: dict[str, bytes] = {}
        file_hashes: dict[str, str] = {}
        edits: list[EditEntry] = []
        seen: set[tuple[str, int, int]] = set()

        for loc in locations:
            if loc.file_path not in file_contents:
                source_file = self._index_store.project_root / loc.file_path
                content = source_file.read_bytes()
                file_contents[loc.file_path] = content
                file_hashes[loc.file_path] = IndexStore.compute_file_hash(source_file)

            content = file_contents[loc.file_path]
            start_byte = position_to_byte_offset(
                content, loc.span.start.line, loc.span.start.column
            )
            end_byte = position_to_byte_offset(
                content, loc.span.end.line, loc.span.end.column
            )

            # Deduplicate by (file, start, end)
            key = (loc.file_path, start_byte, end_byte)
            if key in seen:
                continue
            seen.add(key)

            # Verify the text at this location matches the old name
            actual_text = content[start_byte:end_byte].decode("utf-8")
            if actual_text != old_name:
                continue

            edits.append(
                EditEntry(
                    op=EditOp.REPLACE,
                    file=loc.file_path,
                    start=start_byte,
                    end=end_byte,
                    old=old_name,
                    new=new_name,
                    file_hash=file_hashes[loc.file_path],
                )
            )

        return edits

    def _check_file_rename(
        self, symbol: Symbol, new_name: str
    ) -> FileRenameEntry | None:
        """Check if the file should be renamed to match the new symbol name.

        Returns a FileRenameEntry if the file name matches the symbol name
        (case-insensitive), or None if no rename is needed.
        """
        from pathlib import Path

        file_path = symbol.location.file_path
        file_stem = Path(file_path).stem  # "user" from "user.py"

        # Check if file name matches symbol name (case-insensitive)
        if file_stem.lower() != symbol.name.lower():
            return None

        # Build new file path
        new_file_name = new_name.lower() + ".py"
        parent = Path(file_path).parent
        if parent == Path("."):
            new_path = new_file_name
        else:
            new_path = str(parent / new_file_name)

        source_file = self._index_store.project_root / file_path
        return FileRenameEntry(
            old_path=file_path,
            new_path=new_path,
            file_hash=IndexStore.compute_file_hash(source_file),
        )


def position_to_byte_offset(content: bytes, line: int, column: int) -> int:
    """Convert 0-indexed line/column to byte offset.

    tree-sitter columns are byte offsets within the line, so
    this sums line lengths (including newlines) up to the target line,
    then adds the column.
    """
    offset = 0
    for i, file_line in enumerate(content.split(b"\n")):
        if i == line:
            return offset + column
        offset += len(file_line) + 1  # +1 for the newline
    raise ValueError(f"Line {line} out of range")
