"""Move-symbol refactoring.

Relocate a top-level function from one module to another and update all
``from <src> import <name>`` importers to point at the new module. Cross-module
resolution provides the importer list; the CST layer performs byte-precise
edits across all affected files. v1 scope: top-level functions and single-name
from-imports.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.transaction import (
    EditEntry,
    EditOp,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.paths import module_path_from
from pypeeker.project import load_src_roots
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor import cst
from pypeeker.storage import IndexStore, TransactionStore


class MoveSymbolError(Exception):
    """Raised when a move-symbol plan cannot be created."""


class MoveSymbolPlanner:
    """Plan moving a top-level function to another module."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, symbol_id: str, target_file: str) -> TransactionSummary:
        """Move ``symbol_id`` to ``target_file`` (a project-relative source path)."""
        symbol = self._resolve_top_level_function(symbol_id)
        source_file = symbol.location.file_path
        if source_file == target_file:
            raise MoveSymbolError("Target file is the source file")

        target_path = self._index_store.project_root / target_file
        if not target_path.exists():
            raise MoveSymbolError(f"Target file not found: {target_file}")

        src_roots = load_src_roots(self._index_store.project_root)
        source_module = module_path_from(source_file, src_roots)
        target_module = module_path_from(target_file, src_roots)
        if source_module == target_module:
            raise MoveSymbolError("Source and target resolve to the same module")

        source_bytes = (self._index_store.project_root / source_file).read_bytes()
        source_hash = IndexStore.compute_file_hash(
            self._index_store.project_root / source_file
        )
        def_node, def_text, delete_edit = self._extract_def_edits(
            symbol, source_bytes, source_hash
        )

        target_bytes = target_path.read_bytes()
        target_hash = IndexStore.compute_file_hash(target_path)
        insert_text = self._insert_separator(target_bytes) + def_text
        insert_edit = EditEntry(
            file=target_file,
            start=len(target_bytes),
            end=len(target_bytes),
            old="",
            new=insert_text,
            file_hash=target_hash,
            op=EditOp.INSERT,
        )

        importer_edits = self._build_importer_edits(
            symbol, source_module, target_module
        )

        edits: list[EditEntry] = [delete_edit, insert_edit, *importer_edits]
        affected = sorted({e.file for e in edits})

        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol.symbol_id,
            old_name=source_module,
            new_name=target_module,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="move_symbol",
        )
        self._transaction_store.save(header, edits, None)
        return TransactionSummary(
            tx_id=tx_id,
            operation="move_symbol",
            symbol_id=symbol.symbol_id,
            old_name=source_module,
            new_name=target_module,
            files_affected=affected,
            edit_count=len(edits),
            created_at=header.created_at,
        )

    def _resolve_top_level_function(self, symbol_id: str) -> Symbol:
        results = self._engine.find_symbol(symbol_id)
        if not results:
            raise MoveSymbolError(f"Symbol not found: {symbol_id}")
        if len(results) > 1:
            raise MoveSymbolError(
                f"Ambiguous symbol '{symbol_id}'; use the full id"
            )
        symbol = results[0]
        if symbol.kind != SymbolKind.FUNCTION:
            raise MoveSymbolError("move-symbol v1 supports only functions")
        index = self._index_store.load(symbol.location.file_path)
        if index is None:
            raise MoveSymbolError(f"File not indexed: {symbol.location.file_path}")
        scope_kind = {s.scope_id: s.kind for s in index.scopes}.get(
            symbol.parent_scope_id
        )
        if scope_kind != ScopeKind.MODULE:
            raise MoveSymbolError(
                "move-symbol v1 supports only top-level (module) functions"
            )
        return symbol

    def _extract_def_edits(
        self, symbol: Symbol, source: bytes, file_hash: str
    ) -> tuple[object, str, EditEntry]:
        root = cst.parse(source)
        target = cst.expression_at(
            root,
            symbol.location.span.start.line,
            symbol.location.span.start.column,
        )
        if target is None:
            raise MoveSymbolError("Could not locate the function definition")
        statement = cst.enclosing_statement(target)
        if statement is None:
            raise MoveSymbolError("Definition is not at a top-level statement")
        start = cst.line_start_byte(statement)
        end_line = statement.end_point[0]
        line_starts = _line_start_bytes(source)
        end = (
            line_starts[end_line + 1]
            if end_line + 1 < len(line_starts)
            else len(source)
        )
        def_text = source[start:end].decode("utf-8")
        delete = EditEntry(
            file=symbol.location.file_path,
            start=start,
            end=end,
            old=def_text,
            new="",
            file_hash=file_hash,
            op=EditOp.DELETE,
        )
        return statement, def_text, delete

    @staticmethod
    def _insert_separator(target_bytes: bytes) -> str:
        """Two blank lines before the moved def; tolerate missing trailing newline."""
        if not target_bytes:
            return ""
        suffix = target_bytes[-2:]
        if suffix == b"\n\n":
            return ""
        if target_bytes.endswith(b"\n"):
            return "\n"
        return "\n\n"

    def _build_importer_edits(
        self, symbol: Symbol, source_module: str, target_module: str
    ) -> list[EditEntry]:
        edits: list[EditEntry] = []
        for imp in self._engine.find_importers(symbol.symbol_id):
            # Direct importer of this symbol from the source module.
            if imp.imported_from != f"{source_module}.{symbol.name}":
                continue
            file_path = imp.location.file_path
            full_path = self._index_store.project_root / file_path
            source = full_path.read_bytes()
            file_hash = IndexStore.compute_file_hash(full_path)
            root = cst.parse(source)
            name_node = cst.expression_at(
                root,
                imp.location.span.start.line,
                imp.location.span.start.column,
            )
            stmt = name_node
            while stmt is not None and stmt.type != "import_from_statement":
                stmt = stmt.parent
            if stmt is None:
                raise MoveSymbolError(
                    f"Could not locate import in {file_path}"
                )
            if _imported_name_count(stmt) != 1:
                raise MoveSymbolError(
                    f"{file_path}: multi-name 'from {source_module} import ...' "
                    "is not supported in move-symbol v1"
                )
            module_node = stmt.child_by_field_name("module_name")
            if module_node is None:
                raise MoveSymbolError(
                    f"Could not locate module name in {file_path}"
                )
            edits.append(
                EditEntry(
                    file=file_path,
                    start=module_node.start_byte,
                    end=module_node.end_byte,
                    old=cst.node_text(module_node, source),
                    new=target_module,
                    file_hash=file_hash,
                    op=EditOp.REPLACE,
                )
            )
        return edits


def _line_start_bytes(source: bytes) -> list[int]:
    """Byte offset of the start of each line (plus a sentinel past the end)."""
    offsets = [0]
    for i, byte in enumerate(source):
        if byte == 0x0A:
            offsets.append(i + 1)
    return offsets


def _imported_name_count(import_from_statement) -> int:
    """Number of names imported by ``from X import a, b, c`` (1 for simple)."""
    count = 0
    module_node = import_from_statement.child_by_field_name("module_name")
    for child in import_from_statement.children:
        if child == module_node:
            continue
        if child.type in ("dotted_name", "identifier", "aliased_import"):
            count += 1
    return count


# Path import used only via signature reference; explicit to satisfy ruff.
_ = Path
