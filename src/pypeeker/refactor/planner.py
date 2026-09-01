"""Rename planner: creates transaction plans for symbol renames."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    TransactionSummary,
)
from pypeeker.paths import is_barrel_path
from pypeeker.intents import RenameIntent, predict_file_rename
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.plan_support import (
    method_override_conflicts,
    persist,
    simple_materializer,
)
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
from pypeeker.refactor.registry import register_planner
from pypeeker.refactor.text_anchor import position_to_byte_offset
from pypeeker.storage import IndexStore, TransactionStore


class RenamePlanError(Exception):
    """Raised when a rename plan cannot be created.

    ``precondition`` (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition` when the refusal
    came from :meth:`RenamePlanner.plan`'s guarded precondition set rather
    than a later edit-building check (e.g. "no edits could be generated").
    """

    def __init__(self, message: str, *, precondition: str | None = None) -> None:
        """Store the message alongside the name of the precondition that failed, if any."""
        super().__init__(message)
        self.precondition = precondition


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
        overrides, overridden_by, owning_class = method_override_conflicts(
            hierarchy, self.symbol
        )
        problems: list[str] = []
        if overrides:
            problems.append(f"overrides {', '.join(overrides)}")
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

        if owning_class is not None:
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
        (see :class:`_MethodOverrideSafe`).

        ``update_docstrings`` (default off) additionally rewrites docstring
        cross-references to the renamed symbol — only the unambiguous Sphinx
        role forms ``:func:`old``` / ``:class:`old``` / ``:meth:`old```
        (optionally module-qualified, ``:func:`pkg.mod.old```); plain-text
        mentions are never touched. See :meth:`_docstring_xref_edits` for the
        candidate-file selection and text-verification discipline. The flag
        adds no preconditions: the enumerable precondition set is unchanged.
        """
        state = _RenameState()
        evaluated, failure = evaluate_in_order(
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
            raise RenamePlanError(failure.reason, precondition=evaluated[-1].name)

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

        # 7. Generate transaction. ``files_affected`` is passed explicitly:
        #    it counts every file a candidate location lives in (even one
        #    whose token failed the text guard and produced no edit) plus
        #    the renamed file's new path, so it is wider than the edit set.
        return persist(
            self._transaction_store,
            "rename",
            symbol.symbol_id,
            old_name,
            new_name,
            edits,
            file_rename=file_rename,
            files_affected=sorted(affected_files),
            include_file=include_file,
            include_exports=include_exports,
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
        #    Re-exports are a public API surface (architecture.md): a barrel
        #    (__init__.py re-export) deliberately exposes a name, so "rename
        #    the definition" and "rename the public export" are different
        #    intents, and two flags separate them. --include-exports
        #    propagates the rename through barrels and their consumers: the
        #    definition, the __init__ re-export, and each barrel consumer's
        #    import and call sites are all rewritten to the new name.
        #    --keep-export is the alias-preserving mode: it renames the
        #    definition but holds the public export name, rewriting the
        #    re-export to `from pkg.lib import NewName as X` and leaving pure
        #    barrel consumers untouched. The two are mutually exclusive
        #    (RenameFlagsCompatible); without either flag a barrel consumer is
        #    left untouched.
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
            in_init = is_barrel_path(imp.location.file_path)
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
                content = self._index_store.read_file(loc.file_path)
                file_contents[loc.file_path] = content
                file_hashes[loc.file_path] = self._index_store.file_hash(loc.file_path)

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

            # Verify the text at this location matches the old name. Done on
            # bytes rather than decoded text (TASK-141): the question is only
            # whether the anchor still reads as the name, and comparing the
            # encoded name is exactly equivalent for every span that decodes,
            # while an undecodable byte can no longer crash the verification —
            # such a span simply does not match and is skipped, the same
            # branch a shifted anchor already takes.
            if content[start_byte:end_byte] != old_name.encode("utf-8"):
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
            if not self._index_store.file_exists(file_path):
                continue
            content = self._index_store.read_file(file_path)
            file_hash = self._index_store.file_hash(file_path)
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
        (case-insensitive), or None if no rename is needed. The path rule is
        :func:`~pypeeker.intents.predict_file_rename`, shared with
        :meth:`~pypeeker.intents.RenameIntent.predicted_effect` so the
        intent's prediction and the plan agree by construction.
        """
        rename = predict_file_rename(
            symbol.location.file_path, symbol.name, new_name
        )
        if rename is None:
            return None
        old_path, new_path = rename
        return FileRenameEntry(
            old_path=old_path,
            new_path=new_path,
            file_hash=self._index_store.file_hash(old_path),
        )


def _position_to_byte_offset(content: bytes, line: int, column: int) -> int:
    """Convert 0-indexed line/byte-column to byte offset, raising when out of range.

    Wraps :func:`~pypeeker.refactor.text_anchor.position_to_byte_offset`: an
    index location that points outside the file's current bytes is a bug for
    a rename (its edits come from the same index it just verified fresh), so
    unlike the replannable anchors it raises instead of returning ``None``.
    """
    offset = position_to_byte_offset(content, line, column)
    if offset is None:
        raise ValueError(f"Position {line}:{column} out of range")
    return offset


_materialize_rename = register_planner(RenameIntent.kind)(
    simple_materializer(
        RenameIntent,
        RenamePlanner,
        RenamePlanError,
        lambda intent: (intent.symbol_id, intent.new_name),
        lambda intent: {
            "include_file": intent.include_file,
            "include_exports": intent.include_exports,
            "include_receivers": intent.include_receivers,
            "keep_export": intent.keep_export,
            "allow_override_rename": intent.allow_override_rename,
        },
    )
)
