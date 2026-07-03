"""Rename planner: creates transaction plans for symbol renames."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.analysis import Hierarchy
from pypeeker.models import (
    EditEntry,
    EditOp,
    FileRenameEntry,
    Location,
    Reference,
    Symbol,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.preconditions import (
    AffectedFilesFresh,
    NewNameDiffers,
    NoScopeNameConflict,
    Precondition,
    PreconditionResult,
    RenameFlagsCompatible,
    SymbolResolvesUniquely,
    ValidIdentifier,
    evaluate_in_order,
)
from pypeeker.storage import IndexStore, TransactionStore


class RenamePlanError(Exception):
    """Raised when a rename plan cannot be created."""


# Sphinx cross-reference roles whose target is a symbol name: only the
# unambiguous role forms are rewritten by --update-docstrings (a plain-text
# mention of the old name proves nothing). The optional ``~`` display prefix
# is matched outside the captured dotted path.
_DOC_XREF_ROLE = re.compile(rb":(?:func|class|meth):`~?([A-Za-z_][A-Za-z0-9_.]*)`")


class _MethodOverrideSafe(Precondition):
    """Renaming a method must not silently split an override pair (TASK-94).

    When the target symbol is a METHOD, consults the class
    :class:`~pypeeker.analysis.Hierarchy`: if the method overrides a base
    method or is overridden by a subclass method, renaming only one side
    breaks the contract invisibly, so the rename is refused — naming the
    related method ids — unless ``allow_override_rename`` is passed. If the
    owning class's base chain is incomplete (``mro_unknown``), the rename is
    refused by default too, since overrides cannot be ruled out.

    The hierarchy needs every index, so it is built lazily inside
    :meth:`evaluate` and only when the symbol is a method and the flag is
    not set. Follows the :mod:`pypeeker.refactor.preconditions` contract:
    ``evaluate()`` reports failure via the result, never by raising.
    """

    name = "method-override-safe"

    def __init__(
        self,
        index_store: IndexStore,
        symbol: Symbol,
        allow_override_rename: bool,
    ) -> None:
        self._index_store = index_store
        self.symbol = symbol
        self.allow_override_rename = allow_override_rename

    def evaluate(self) -> PreconditionResult:
        """Evaluate override-safety for a method rename."""
        if self.symbol.kind is not SymbolKind.METHOD:
            return PreconditionResult(ok=True)
        if self.allow_override_rename:
            return PreconditionResult(ok=True)

        hierarchy = Hierarchy.from_store(self._index_store)
        symbol_id = self.symbol.symbol_id
        problems: list[str] = []
        overrides = hierarchy.overrides(symbol_id)
        if overrides:
            problems.append(f"overrides {', '.join(overrides)}")
        overridden_by = hierarchy.overridden_by(symbol_id)
        if overridden_by:
            problems.append(f"is overridden by {', '.join(overridden_by)}")
        if problems:
            return PreconditionResult(
                ok=False,
                reason=(
                    f"Cannot rename method '{symbol_id}': it {' and '.join(problems)}. "
                    "Renaming only one side of an override pair breaks the contract; "
                    "pass allow_override_rename=True to rename anyway."
                ),
            )

        owning_class = self.symbol.parent_scope_id
        if owning_class is not None and hierarchy.mro_unknown(owning_class):
            return PreconditionResult(
                ok=False,
                reason=(
                    f"Cannot rename method '{symbol_id}': hierarchy incomplete — "
                    f"class '{owning_class}' has unresolved or external bases, so "
                    "override relationships cannot be verified; pass "
                    "allow_override_rename=True to rename anyway."
                ),
            )
        return PreconditionResult(ok=True)


@dataclass
class _RenameState:
    """Values computed while evaluating preconditions, reused to build edits."""

    symbol: Symbol | None = None
    reexports_to_alias: list[Symbol] = field(default_factory=list)
    edit_locations: list[Location] = field(default_factory=list)
    affected_files: set[str] = field(default_factory=set)


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
        include_receivers: bool = False,
        keep_export: bool = False,
        allow_override_rename: bool = False,
        update_docstrings: bool = False,
    ) -> TransactionSummary:
        """Create a rename plan and persist it as a transaction.

        ``allow_override_rename`` bypasses the method-override safety check:
        by default a method that overrides / is overridden by another project
        method, or whose class hierarchy is incomplete, refuses to rename
        (see :class:`MethodOverrideSafe`).

        ``update_docstrings`` (default off) additionally rewrites docstring
        cross-references to the renamed symbol — only the unambiguous Sphinx
        role forms ``:func:`old``` / ``:class:`old``` / ``:meth:`old```
        (optionally module-qualified, ``:func:`pkg.mod.old```); plain-text
        mentions are never touched. See :meth:`_docstring_xref_edits` for the
        candidate-file selection and text-verification discipline. The flag
        adds no preconditions: the enumerable precondition set is unchanged.
        """
        state = _RenameState()
        _, failure = evaluate_in_order(
            self._iter_preconditions(
                state,
                symbol_id,
                new_name,
                include_exports=include_exports,
                include_receivers=include_receivers,
                keep_export=keep_export,
                allow_override_rename=allow_override_rename,
            )
        )
        if failure is not None:
            raise RenamePlanError(failure.reason)

        symbol = state.symbol
        old_name = symbol.name
        affected_files = state.affected_files

        # 6. Convert to EditEntry objects with byte offsets
        edits = self._build_edits(state.edit_locations, old_name, new_name)

        # 6a. --keep-export: rewrite each non-aliased re-export so the package
        #     keeps exporting the old public name — `from .lib import Old`
        #     becomes `from .lib import New as Old`. Barrel consumers of the
        #     public name are then untouched and stay valid.
        if state.reexports_to_alias:
            alias_locations = [imp.location for imp in state.reexports_to_alias]
            edits.extend(
                self._build_edits(alias_locations, old_name, f"{new_name} as {old_name}")
            )

        if not edits:
            raise RenamePlanError(
                f"No edits could be generated for renaming '{old_name}' to '{new_name}'. "
                "The symbol locations may not contain the expected text."
            )

        # 6a'. --update-docstrings: rewrite Sphinx-role docstring
        #      cross-references (:func:`old` etc.) to the renamed symbol.
        if update_docstrings:
            doc_edits = self._docstring_xref_edits(edits, old_name, new_name, affected_files)
            edits.extend(doc_edits)
            affected_files.update(edit.file for edit in doc_edits)

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

    def preconditions(
        self,
        symbol_id: str,
        new_name: str,
        *,
        include_file: bool = False,
        include_exports: bool = False,
        include_receivers: bool = False,
        keep_export: bool = False,
        allow_override_rename: bool = False,
    ) -> list[Precondition]:
        """The ordered precondition set for this rename, in enumerable form.

        Each precondition is evaluated as it is constructed (later ones
        depend on cached results of earlier ones, e.g. the conflict check
        needs the resolved symbol), so the returned objects reflect current
        state; if a precondition fails, the list ends at that precondition.
        ``include_file`` adds no preconditions and is accepted only for
        signature parity with :meth:`plan`.
        """
        preconditions, _ = evaluate_in_order(
            self._iter_preconditions(
                _RenameState(),
                symbol_id,
                new_name,
                include_exports=include_exports,
                include_receivers=include_receivers,
                keep_export=keep_export,
                allow_override_rename=allow_override_rename,
            )
        )
        return preconditions

    def _iter_preconditions(
        self,
        state: _RenameState,
        symbol_id: str,
        new_name: str,
        *,
        include_exports: bool,
        include_receivers: bool,
        keep_export: bool,
        allow_override_rename: bool = False,
    ) -> Iterator[Precondition]:
        """Yield this rename's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`evaluate_in_order`): later preconditions are constructed
        from cached results of earlier ones, and the edit targets collected
        between yields are stashed on ``state`` for :meth:`plan`.
        """
        yield RenameFlagsCompatible(include_exports, keep_export)

        # 1. Resolve symbol
        resolve = SymbolResolvesUniquely(self._engine, symbol_id)
        yield resolve
        symbol = resolve.symbol
        state.symbol = symbol

        yield NewNameDiffers(symbol.name, new_name)

        # 2. Validate new name
        yield ValidIdentifier(new_name)
        yield NoScopeNameConflict(self._index_store, symbol, new_name)

        # 2b. A method rename must not split an override pair. Only part of
        #     the set for METHOD symbols (the hierarchy needs every index, so
        #     it is built lazily inside evaluate()).
        if symbol.kind is SymbolKind.METHOD:
            yield _MethodOverrideSafe(
                self._index_store, symbol, allow_override_rename
            )

        self._collect_edit_targets(
            state,
            symbol,
            include_exports=include_exports,
            include_receivers=include_receivers,
            keep_export=keep_export,
        )

        # 5. Check affected files are indexed and not stale
        yield AffectedFilesFresh(self._index_store, state.affected_files)

    def _collect_edit_targets(
        self,
        state: _RenameState,
        symbol: Symbol,
        *,
        include_exports: bool,
        include_receivers: bool,
        keep_export: bool,
    ) -> None:
        """Collect the locations the rename will edit (steps 3–5b)."""
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
        #    --keep-export takes a different route (see below): it preserves the
        #    public export name by aliasing the innermost re-export.
        imports_to_edit: list[Symbol] = []
        reexports_to_alias: list[Symbol] = []
        for imp in self._engine.find_importers(symbol.symbol_id):
            in_init = imp.location.file_path.endswith("__init__.py")
            crosses = self._engine.import_crosses_barrel(imp.symbol_id)
            if keep_export:
                if in_init and imp.imported_name_location is None:
                    # `from .lib import Old` re-export → `... import New as Old`
                    reexports_to_alias.append(imp)
                elif crosses and not in_init:
                    continue  # barrel consumer: public name preserved, leave it
                else:
                    # direct importer, or an already-aliased re-export (its
                    # public alias is preserved by renaming the imported token)
                    imports_to_edit.append(imp)
                continue
            on_export_surface = in_init or crosses
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
            references.extend(self._engine.references_to_binding(binding_id))

        # 5. Collect edit locations: definition + references + import tokens.
        edit_locations: list[Location] = [symbol.location]
        for ref in references:
            edit_locations.append(ref.location)
        for imp in imports_to_edit:
            # Use imported_name_location for aliased imports (e.g.
            # "from lib import helper as h") so we rename "helper", not "h".
            loc = imp.imported_name_location or imp.location
            edit_locations.append(loc)

        # 5b. With --include-receivers, also rename attribute/method call sites
        #     that resolve to this definition through a receiver — but only
        #     high-confidence ones (declared annotations, self/cls, module or
        #     class receivers). Constructor-inferred receivers are best-effort
        #     and deliberately excluded, since rename mutates code:
        #     declared_only filters out matches the resolver classifies as
        #     ResolutionKind.RECEIVER_INFERRED (see
        #     CrossModuleResolver.references_to_definition_classified — the single
        #     code path deciding what "declared only" means). The text guard
        #     in _build_edits keeps only tokens equal to old_name.
        if include_receivers:
            for ref in self._engine.references_to_definition(
                symbol.symbol_id, declared_only=True
            ):
                if ref.is_attribute_access:
                    edit_locations.append(ref.location)

        affected_files = {loc.file_path for loc in edit_locations}
        affected_files.update(imp.location.file_path for imp in reexports_to_alias)

        state.reexports_to_alias = reexports_to_alias
        state.edit_locations = edit_locations
        state.affected_files = affected_files

    def _build_edits(
        self,
        locations: list[Location],
        old_name: str,
        replacement: str,
    ) -> list[EditEntry]:
        """Convert Location objects to EditEntry objects with byte offsets.

        Each location must currently hold ``old_name``; it is replaced with
        ``replacement`` (normally the new name, but e.g. ``"New as Old"`` for an
        alias-preserving re-export edit).
        """
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
            start_byte = _position_to_byte_offset(
                content, loc.span.start.line, loc.span.start.column
            )
            end_byte = _position_to_byte_offset(
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
                    new=replacement,
                    file_hash=file_hashes[loc.file_path],
                )
            )

        return edits

    def _docstring_xref_edits(
        self,
        existing_edits: list[EditEntry],
        old_name: str,
        new_name: str,
        affected_files: set[str],
    ) -> list[EditEntry]:
        """REPLACE edits rewriting Sphinx-role docstring cross-references.

        Only the unambiguous role forms ``:func:`X``` / ``:class:`X``` /
        ``:meth:`X``` are rewritten, where ``X`` is ``old_name`` or a dotted
        path whose final component is ``old_name`` (an optional ``~`` display
        prefix is allowed); the edit covers just the name token inside the
        backticks. Plain-text mentions of the old name are never touched.

        Candidate files (deliberately conservative — Symbol.location points
        at the NAME token, not the docstring, so role hits are re-found
        textually rather than via index offsets): the rename's affected files
        plus every indexed file whose index records a symbol docstring
        containing ``old_name``. Each candidate is scanned with the role
        regex against its CURRENT bytes and every hit is text-verified, the
        same guard discipline as :meth:`_build_edits`; offsets and the
        ``file_hash`` come from the bytes read here, so the applier's hash
        check keeps even index-stale candidates safe. A role form sitting
        outside a docstring (e.g. in a comment) matches too — accepted, since
        the flag is opt-in and the role syntax names the symbol explicitly.
        """
        candidates = set(affected_files)
        for file_path in self._index_store.list_indexed_files():
            if file_path in candidates:
                continue
            index = self._index_store.load(file_path)
            if index is None:
                continue
            if any(
                symbol.docstring and old_name in symbol.docstring
                for symbol in index.symbols
            ):
                candidates.add(file_path)

        seen = {(edit.file, edit.start, edit.end) for edit in existing_edits}
        old_bytes = old_name.encode("utf-8")
        edits: list[EditEntry] = []
        for file_path in sorted(candidates):
            source = self._index_store.project_root / file_path
            if not source.exists():
                continue
            content = source.read_bytes()
            file_hash = IndexStore.compute_file_hash(source)
            for match in _DOC_XREF_ROLE.finditer(content):
                dotted = match.group(1)
                if dotted != old_bytes and not dotted.endswith(b"." + old_bytes):
                    continue
                start = match.end(1) - len(old_bytes)
                end = match.end(1)
                key = (file_path, start, end)
                if key in seen:
                    continue
                seen.add(key)
                if content[start:end] != old_bytes:  # text guard, like _build_edits
                    continue
                edits.append(
                    EditEntry(
                        op=EditOp.REPLACE,
                        file=file_path,
                        start=start,
                        end=end,
                        old=old_name,
                        new=new_name,
                        file_hash=file_hash,
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


def _position_to_byte_offset(content: bytes, line: int, column: int) -> int:
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
