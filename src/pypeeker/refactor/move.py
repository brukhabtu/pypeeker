"""move-symbol planner: relocate a top-level definition into another module.

The payoff refactoring of TASK-131 and the first consumer of everything the
two PRs before it built: a move **deletes** a definition from one file,
**creates or extends** another (possibly a module that did not exist), and
**rewrites every import** that bound it — three things the transaction model,
the applier, and the batch flattener could not express until file birth and
death became first-class (``FileCreateEntry``/``FileDeleteEntry``,
``Effect.files_created``/``files_deleted``,
``Materialized.files_created``/``files_deleted``).

Four decisions worth reading before the code:

**A move is a rename in id space.** :class:`~pypeeker.intents.MoveSymbolIntent`
predicts ``renamed={symbol_id: dest_module:leaf}``, so every existing piece of
the footprint/effect algebra works on moves unchanged — prefix descent carries
a moved class's methods, ``Effect.then`` composes a move with a later rename,
and pending intents anchored on the moved symbol follow it. Only the *file*
half of ``Effect`` needed new fields.

**Rename's collection is reused; rename's edit builder is not.**
Importers are found exactly as :meth:`~pypeeker.refactor.planner.RenamePlanner._collect_edit_targets`
finds them (``find_importers`` + ``import_crosses_barrel`` +
``imported_name_location``), and the multi-name line surgery is
``imports_ops._import_name_segments`` + :class:`~pypeeker.refactor.preconditions.ImportLineSurgerySafe`.
But ``_build_edits`` silently drops a location whose text does not match, and
that is right for a rename (a skipped import keeps working) and *dangerous*
for a move (a skipped import dangles). So every collected edge either
produces an edit or refuses by name through
:class:`~pypeeker.refactor.preconditions.ImportEdgeRewritable`.

That contract is only as strong as the collection, so the one edge shape
collection *cannot* see is refused separately: ``from <source> import *``
binds the local name ``*``, not the symbol, so ``find_importers`` yields no
edge for it and "every collected edge is rewritten" would hold vacuously
while the name goes unbound.
:class:`~pypeeker.refactor.preconditions.SourceStarImportOpaque` is what
keeps that from being silence.

**Barrel re-exports are rewritten; barrel consumers are not.** A move does
not change the exported *name*, only the definition's home, so rewriting
``from .old import X`` in a package ``__init__`` to ``from pkg.new import X``
is repair, not cascade — the divergence from rename's ``--include-exports``
gate is deliberate (see architecture.md's "Re-exports are a public API
surface" note, which is about the name). A *consumer* of that barrel
(``from pkg import X``) needs no edit at all: the barrel it goes through is
repaired, so the consumer still resolves. Leaving it alone is the smaller,
more precise change.

**Reads go through the store surface, never the filesystem.** Bytes and
hashes come from ``store.read_file`` / ``store.file_hash`` (TASK-129's
store-read port), so the planner is simulatable: under
:class:`~pypeeker.storage.overlay.OverlayIndexStore` it plans against the
simulated tree, which is what lets a move be batched with other intents.

Refusals are named :class:`~pypeeker.refactor.preconditions.Precondition`
objects and reach the CLI through the standard ``plan-refused`` envelope.
:class:`MoveSymbolError` deliberately carries **no** ``code``: move-symbol is
a CLI operation, not a ``check --fix`` remedy, so it maps onto none of the
frozen refusal slugs. Where the ordered set reuses a shared precondition that
happens to carry a slug (``UndecoratedDefinition``, ``DeletableScope``,
``ScopeSpanClean``, ``AnchorFileExists``, ``AnchorIndexFresh``,
``ImportLineSurgerySafe``), that slug is simply not read here.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from pypeeker.intents import (
    EdgeAnchor,
    Intent,
    MoveSymbolIntent,
    SymbolAnchor,
    module_file_path,
)
from pypeeker.models import (
    EditEntry,
    EditOp,
    FileCreateEntry,
    FileIndex,
    Reference,
    Scope,
    Symbol,
    SymbolKind,
    TransactionHeader,
    TransactionSummary,
    module_of,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.imports_ops import _import_name_segments
from pypeeker.refactor.preconditions import (
    AffectedFilesFresh,
    AnchorFileExists,
    AnchorIndexFresh,
    DeletableScope,
    DestinationImportsCompatible,
    DestinationModuleResolvable,
    DestinationPathUnobstructed,
    ImportBindingReproducible,
    ImportEdgeRewritable,
    ImportLineInRange,
    ImportLineSurgerySafe,
    ImportStatementLine,
    MoveIsNotSelf,
    MovedBodyClosed,
    MoveQualifiedUseUnsupported,
    NoDestinationNameCollision,
    Precondition,
    ScopeSpanClean,
    SourceExportListClean,
    SourceModuleFree,
    SourceStarImportOpaque,
    SymbolMatchFound,
    SymbolResolvesUniquely,
    TopLevelDefinition,
    UnconditionalDefinition,
    UndecoratedDefinition,
    ValidModulePath,
    evaluate_in_order,
)
from pypeeker.refactor.registry import (
    Materialized,
    MaterializeError,
    load_transaction,
    register_planner,
)
from pypeeker.refactor.text_anchor import line_end
from pypeeker.storage import IndexStore, TransactionStore

_DEFINITION_KINDS = (SymbolKind.FUNCTION, SymbolKind.CLASS)
"""The kinds a move may relocate — ``DeleteSymbolPlanner``'s set, module-level only."""


class MoveSymbolError(Exception):
    """Raised when a move-symbol plan cannot be created.

    ``precondition`` names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition`, exactly as
    :class:`~pypeeker.refactor.planner.RenamePlanError` does. There is no
    ``code``: a move refusal has no legacy ``check --fix`` slug to map onto
    (see the module docstring), so the CLI reports it under the uniform
    ``plan-refused`` code every other CLI-only refactoring uses.
    """

    def __init__(self, message: str, *, precondition: str | None = None) -> None:
        """Store the message alongside the name of the precondition that failed."""
        super().__init__(message)
        self.precondition = precondition


@dataclass(frozen=True)
class _EdgeRewrite:
    """One import edge plus everything needed to rewrite its statement."""

    edge: EdgeAnchor
    file_path: str
    file_hash: str
    line_start: int
    body: bytes
    module_start: int
    module_end: int
    segments: list
    segment_index: int


@dataclass
class _MoveState:
    """Values computed while evaluating preconditions, reused to build edits."""

    symbol: Symbol | None = None
    source_file: str = ""
    source_module: str = ""
    content: bytes = b""
    index: FileIndex | None = None
    scope: Scope | None = None
    line_starts: list[int] = field(default_factory=list)
    definition_start: int = 0
    definition_end: int = 0
    definition_text: str = ""
    import_statements: list[str] = field(default_factory=list)
    dest_path: str = ""
    dest_exists: bool = False
    rewrites: list[_EdgeRewrite] = field(default_factory=list)
    edges: list[EdgeAnchor] = field(default_factory=list)


def _import_statement(symbol: Symbol, content: bytes, line_starts: list[int]) -> str | None:
    """Re-express an IMPORT symbol as the statement that would rebind it.

    Two forms cover every statically declared import the binder records
    (``binder/imports.py``), and they are told apart by ``imported_from``:

    * ``import a.b`` / ``import a.b as c`` — ``imported_from`` *is* the
      original dotted name;
    * ``from m import x`` / ``from m import x as y`` — ``imported_from`` is
      ``m.x``, i.e. the original name suffixed onto the module.

    The original name is read from ``imported_name_location`` when the import
    is aliased (that location is the pre-``as`` token) and is the bound name
    otherwise. ``None`` means neither form reconstructs the binding, which
    :class:`~pypeeker.refactor.preconditions.ImportBindingReproducible` turns
    into a named refusal rather than a guessed import line.
    """
    origin = symbol.imported_from
    if not origin:
        return None
    alias_location = symbol.imported_name_location
    if alias_location is None:
        original = symbol.name
    else:
        start = line_starts[alias_location.span.start.line] + alias_location.span.start.column
        stop = line_starts[alias_location.span.end.line] + alias_location.span.end.column
        original = content[start:stop].decode("utf-8")
    if not original:
        return None
    suffix = "" if original == symbol.name else f" as {symbol.name}"
    if origin == original:
        return f"import {original}{suffix}"
    if origin.endswith(f".{original}"):
        module = origin[: -(len(original) + 1)]
        if module:
            return f"from {module} import {original}{suffix}"
    return None


def _new_module_content(
    dest_module: str, statements: list[str], definition: str
) -> str:
    """The full text of a destination module this move brings into existence.

    A one-line module docstring naming the module, the import bindings the
    moved body needs, then the definition — PEP 8 spacing throughout (two
    blank lines before a top-level definition). The docstring is not
    decoration: a module created without one trips ``D100`` in every project
    that lints docstrings, and a refactoring whose output fails the project's
    own linter is not a finished refactoring.
    """
    body = definition if definition.endswith("\n") else definition + "\n"
    parts = [f'"""{dest_module}."""\n']
    if statements:
        parts.append("\n")
        parts.append("\n".join(statements))
        parts.append("\n")
    parts.append("\n\n")
    parts.append(body)
    return "".join(parts)


def _appended_block(existing: str, statements: list[str], definition: str) -> str:
    """The text that replaces an existing destination's trailing whitespace.

    Expressed as a replacement of the trailing whitespace rather than a bare
    append so the destination's final newline count is normalised in the same
    edit: whatever the file ended with, the result ends with exactly the
    definition and one newline, separated from the previous top-level
    statement by two blank lines.

    Needed imports go immediately above the definition rather than at the top
    of the file. Both positions are valid Python; this one is *deterministic*
    (there is no heuristic for "where the import block ends" that survives
    conditional imports, ``__future__`` lines, or a module docstring
    followed by a comment) and it keeps the whole extension to one contiguous
    byte range.
    """
    body = definition if definition.endswith("\n") else definition + "\n"
    parts: list[str] = []
    if existing.strip():
        parts.append("\n\n")
        if statements:
            parts.append("\n".join(statements))
            parts.append("\n\n")
        parts.append("\n")
    elif statements:
        parts.append("\n".join(statements))
        parts.append("\n\n\n")
    parts.append(body)
    return "".join(parts)


class MoveSymbolPlanner:
    """Move a top-level FUNCTION/CLASS definition into another module.

    One transaction expresses the whole move: a DELETE over the source
    definition's whole-line span (exactly ``DeleteSymbolPlanner``'s span
    discipline, trailing blank lines included), either a
    :class:`~pypeeker.models.transaction.FileCreateEntry` carrying the new
    module's full text or a splice extending an existing one, and one or two
    edits per rewritten import line. Apply and rollback are therefore exact
    inverses over the entire move, including the birth of the destination.
    """

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    def plan(self, anchor: SymbolAnchor, dest_module: str) -> TransactionSummary:
        """Re-locate ``anchor``'s definition and re-home it in ``dest_module``."""
        state = _MoveState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, anchor.symbol_id, dest_module)
        )
        if failure is not None:
            raise MoveSymbolError(failure.reason, precondition=evaluated[-1].name)

        symbol = state.symbol
        edits = [self._deletion_edit(state)]
        creates: list[FileCreateEntry] = []
        if state.dest_exists:
            edits.append(self._extend_destination_edit(state))
        else:
            creates.append(self._create_destination(state, dest_module))
        edits.extend(self._importer_edits(state, dest_module))

        affected = {state.source_file, state.dest_path}
        affected.update(rewrite.file_path for rewrite in state.rewrites)

        tx_id = uuid.uuid4().hex[:12]
        header = TransactionHeader(
            tx_id=tx_id,
            symbol_id=symbol.symbol_id,
            # A move does not change the name — the modules are what move,
            # so the header's from/to pair names them. `transactions show`
            # then reads as "pkg.old -> pkg.new" for the symbol in symbol_id.
            old_name=state.source_module,
            new_name=dest_module,
            created_at=datetime.now(timezone.utc).isoformat(),
            operation="move-symbol",
        )
        self._transaction_store.save(header, edits, None, creates=creates, deletes=[])
        return TransactionSummary(
            tx_id=tx_id,
            operation="move-symbol",
            symbol_id=symbol.symbol_id,
            old_name=state.source_module,
            new_name=dest_module,
            files_affected=sorted(affected),
            edit_count=len(edits) + len(creates),
            created_at=header.created_at,
        )

    # -- edit construction --------------------------------------------------

    def _deletion_edit(self, state: _MoveState) -> EditEntry:
        """DELETE the source definition, trailing blank lines included.

        The span is ``DeleteSymbolPlanner``'s verbatim: from the start of the
        ``def``/``class`` line through the end of the scope's last line, then
        onward over any run of blank lines, so removing a definition does not
        leave a widening gap behind it.
        """
        content = state.content
        line_starts = state.line_starts
        end = state.definition_end
        for next_line in range(state.scope.span.end.line + 1, len(line_starts)):
            next_end = line_end(line_starts, content, next_line)
            if content[line_starts[next_line] : next_end].strip():
                break
            end = (
                line_starts[next_line + 1]
                if next_line + 1 < len(line_starts)
                else len(content)
            )
        return EditEntry(
            op=EditOp.DELETE,
            file=state.source_file,
            start=state.definition_start,
            end=end,
            old=content[state.definition_start : end].decode("utf-8"),
            new="",
            file_hash=self._index_store.file_hash(state.source_file),
        )

    def _create_destination(
        self, state: _MoveState, dest_module: str
    ) -> FileCreateEntry:
        """The newborn destination module, content and all."""
        content = _new_module_content(
            dest_module, state.import_statements, state.definition_text
        )
        encoded = content.encode("utf-8")
        return FileCreateEntry(
            path=state.dest_path,
            content=content,
            content_hash=hashlib.sha256(encoded).hexdigest(),
        )

    def _extend_destination_edit(self, state: _MoveState) -> EditEntry:
        """Append the definition (and any imports it needs) to an existing module."""
        original = self._index_store.read_file(state.dest_path)
        text = original.decode("utf-8")
        keep = len(text.rstrip())
        old_tail = text[keep:]
        new_tail = _appended_block(
            text[:keep], state.import_statements, state.definition_text
        )
        start = len(text[:keep].encode("utf-8"))
        return EditEntry(
            op=EditOp.REPLACE if old_tail else EditOp.INSERT,
            file=state.dest_path,
            start=start,
            end=len(original),
            old=old_tail,
            new=new_tail,
            file_hash=self._index_store.file_hash(state.dest_path),
        )

    def _importer_edits(
        self, state: _MoveState, dest_module: str
    ) -> list[EditEntry]:
        """Rewrite every collected import edge to name the destination module.

        A line importing only the moved symbol has its **module part**
        replaced in place — the imported name and any alias are untouched,
        which is what makes ``from a import X as Y`` survive a move without
        the text guard rename needs.

        A line importing several names is split instead: the moved entry is
        removed from the old statement (the comma discipline
        ``RemoveImportPlanner`` established) and a fresh
        ``from <dest> import <entry>`` line is inserted above it, carrying the
        entry's text verbatim so an alias travels with it. The two edits never
        overlap, and the insert sits at a lower offset than the deletion, so
        the applier's bottom-to-top splice order keeps both offsets valid.
        """
        edits: list[EditEntry] = []
        for rewrite in state.rewrites:
            body = rewrite.body
            if len(rewrite.segments) == 1:
                edits.append(
                    EditEntry(
                        op=EditOp.REPLACE,
                        file=rewrite.file_path,
                        start=rewrite.line_start + rewrite.module_start,
                        end=rewrite.line_start + rewrite.module_end,
                        old=body[rewrite.module_start : rewrite.module_end].decode("utf-8"),
                        new=dest_module,
                        file_hash=rewrite.file_hash,
                    )
                )
                continue
            segments = rewrite.segments
            index = rewrite.segment_index
            entry = body[segments[index].text_start : segments[index].text_end]
            if index + 1 < len(segments):
                del_start = segments[index].text_start
                del_end = segments[index + 1].text_start
            else:
                del_start = segments[index - 1].text_end
                del_end = segments[index].text_end
            edits.append(
                EditEntry(
                    op=EditOp.DELETE,
                    file=rewrite.file_path,
                    start=rewrite.line_start + del_start,
                    end=rewrite.line_start + del_end,
                    old=body[del_start:del_end].decode("utf-8"),
                    new="",
                    file_hash=rewrite.file_hash,
                )
            )
            indent = body[: len(body) - len(body.lstrip())].decode("utf-8")
            edits.append(
                EditEntry(
                    op=EditOp.INSERT,
                    file=rewrite.file_path,
                    start=rewrite.line_start,
                    end=rewrite.line_start,
                    old="",
                    new=f"{indent}from {dest_module} import {entry.decode('utf-8')}\n",
                    file_hash=rewrite.file_hash,
                )
            )
        return edits

    # -- preconditions ------------------------------------------------------

    def _iter_preconditions(
        self, state: _MoveState, symbol_id: str, dest_module: str
    ) -> Iterator[Precondition]:
        """Yield this move's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`~pypeeker.refactor.preconditions.evaluate_in_order`):
        later ones are constructed from earlier ones' cached results, and
        everything :meth:`plan` needs to build edits is stashed on ``state``.

        Order is the refusal contract — the first failure is what a caller
        sees — so it runs outside-in: resolve and qualify the target, then
        the source-side deletion span (delete-symbol's own three checks),
        then the destination, then the body's closure over its free names,
        then one group per import edge.
        """
        resolve = SymbolResolvesUniquely(self._engine, symbol_id)
        yield resolve
        symbol = resolve.symbol
        state.symbol = symbol
        state.source_file = symbol.location.file_path
        state.source_module = module_of(symbol.symbol_id)

        yield TopLevelDefinition(symbol)
        yield ValidModulePath(dest_module)
        yield MoveIsNotSelf(symbol.symbol_id, state.source_module, dest_module)

        yield AnchorFileExists(self._index_store, state.source_file)
        source_fresh = AnchorIndexFresh(self._index_store, state.source_file)
        yield source_fresh
        state.content = source_fresh.content
        state.index = source_fresh.index

        fresh_matches = [
            s
            for s in source_fresh.index.symbols
            if s.symbol_id == symbol.symbol_id and s.kind in _DEFINITION_KINDS
        ]
        still = SymbolMatchFound(symbol.symbol_id, fresh_matches, noun="symbol")
        yield still
        state.symbol = symbol = still.symbol

        yield UndecoratedDefinition(symbol)
        scope_check = DeletableScope(source_fresh.index, state.content, symbol)
        yield scope_check
        state.scope = scope_check.scope
        state.line_starts = scope_check.line_starts
        state.definition_start = scope_check.start
        yield UnconditionalDefinition(symbol, state.scope)
        yield ScopeSpanClean(state.content, state.line_starts, state.scope)

        dest_path = module_file_path(self._index_store, dest_module)
        destination = DestinationModuleResolvable(
            self._index_store, dest_module, dest_path
        )
        yield destination
        yield DestinationPathUnobstructed(self._index_store, dest_module, dest_path)
        state.dest_path = destination.dest_path
        state.dest_exists = destination.exists

        importers = self._collect_importers(symbol)
        state.edges = [
            EdgeAnchor(importer.symbol_id, symbol.symbol_id) for importer in importers
        ]
        yield NoDestinationNameCollision(
            dest_module,
            state.dest_path,
            destination.index,
            symbol.name,
            {importer.location.file_path for importer in importers},
        )
        yield MoveQualifiedUseUnsupported(symbol.name, self._qualified_uses(symbol))
        yield SourceStarImportOpaque(
            state.source_module, symbol.name, self._star_importers(state.source_module)
        )

        body = MovedBodyClosed(
            source_fresh.index,
            state.content,
            state.line_starts,
            symbol,
            state.scope,
            state.source_module,
        )
        yield body
        state.definition_end = body.definition_end
        state.definition_text = body.definition_text

        compatible = DestinationImportsCompatible(
            dest_module, destination.index, body.imports
        )
        yield compatible
        reproducible = ImportBindingReproducible(
            [
                (binding, _import_statement(binding, state.content, state.line_starts))
                for binding in compatible.missing
            ]
        )
        yield reproducible
        state.import_statements = reproducible.statements

        yield SourceModuleFree(source_fresh.index, symbol, state.scope)
        yield SourceExportListClean(
            state.content, source_fresh.index, symbol, state.source_module
        )

        for importer, edge in zip(importers, state.edges):
            yield from self._iter_edge_preconditions(state, importer, edge)

        fresh_files = {state.source_file}
        fresh_files.update(rewrite.file_path for rewrite in state.rewrites)
        if state.dest_exists:
            fresh_files.add(state.dest_path)
        yield AffectedFilesFresh(self._index_store, fresh_files)

    def _iter_edge_preconditions(
        self, state: _MoveState, importer: Symbol, edge: EdgeAnchor
    ) -> Iterator[Precondition]:
        """Yield one import edge's preconditions and stash its rewrite plan."""
        file_path = importer.location.file_path
        yield AnchorFileExists(self._index_store, file_path)
        fresh = AnchorIndexFresh(self._index_store, file_path)
        yield fresh

        line_check = ImportLineInRange(
            fresh.content, importer.location.span.start.line
        )
        yield line_check
        yield ImportStatementLine(line_check.body)
        yield ImportLineSurgerySafe(line_check.body)

        body = line_check.body
        segments = _import_name_segments(body, body.lstrip().startswith(b"from "))
        rewritable = ImportEdgeRewritable(edge, body, importer.name, segments)
        yield rewritable

        state.rewrites.append(
            _EdgeRewrite(
                edge=edge,
                file_path=file_path,
                file_hash=self._index_store.file_hash(file_path),
                line_start=line_check.line_start,
                body=body,
                module_start=rewritable.module_start,
                module_end=rewritable.module_end,
                segments=segments,
                segment_index=rewritable.segment_index,
            )
        )

    # -- collection (reused from the rename planner) ------------------------

    def _collect_importers(self, symbol: Symbol) -> list[Symbol]:
        """The IMPORT symbols this move must rewrite, in deterministic order.

        Collected exactly as :meth:`~pypeeker.refactor.planner.RenamePlanner._collect_edit_targets`
        collects them, with one deliberate divergence in the gating. Rename
        splits importers into *direct* (always rewritten) and *on the
        re-export surface* (gated on ``--include-exports``, because renaming
        through a barrel changes the package's public name). A move changes
        no name, so the split falls differently:

        * an import **inside** an ``__init__.py`` is a re-export whose source
          module is now wrong — rewritten unconditionally, no flag;
        * an import that merely *crosses* a barrel (``from pkg import X``
          where ``pkg`` re-exports it) is a consumer of a surface this move
          repairs in place. Its own statement stays correct, so it is left
          alone — the precise change, not the clever one.
        """
        importers: list[Symbol] = []
        for importer in self._engine.find_importers(symbol.symbol_id):
            in_init = importer.location.file_path.endswith("__init__.py")
            if not in_init and self._engine.import_crosses_barrel(importer.symbol_id):
                continue
            importers.append(importer)
        importers.sort(
            key=lambda s: (
                s.location.file_path,
                s.location.span.start.line,
                s.location.span.start.column,
                s.symbol_id,
            )
        )
        return importers

    def _star_importers(self, source_module: str) -> list[str]:
        """Files doing ``from <source_module> import *``, in path order.

        The edge :meth:`_collect_importers` structurally cannot see. The
        binder records a star as an IMPORT symbol bound to the local name
        ``*`` with ``imported_from`` naming the (relative-resolved) module,
        so ``find_importers`` — which matches the *imported symbol* — yields
        nothing for it and the move would silently leave every name the star
        supplied unbound. Finding them is a scan of the same module-level
        IMPORT symbols every other check reads; judging them is
        :class:`~pypeeker.refactor.preconditions.SourceStarImportOpaque`'s.
        """
        files: list[str] = []
        for path in self._index_store.list_indexed_files():
            index = self._index_store.load(path)
            if index is None:
                continue
            if any(
                symbol.kind is SymbolKind.IMPORT
                and symbol.name == "*"
                and symbol.imported_from == source_module
                for symbol in index.symbols
            ):
                files.append(path)
        return sorted(files)

    def _qualified_uses(self, symbol: Symbol) -> list[Reference]:
        """Attribute-access uses of the definition outside its own module.

        The ``import a`` + ``a.X`` shape: the importing module binds the
        *module*, so ``find_importers`` sees nothing to rewrite while the
        call sites still name the old home.

        Two passes, because one is not enough. The resolver finds the forms
        it can attribute (``from pkg import lib`` + ``lib.X``,
        ``import pkg.lib as L`` + ``L.X``, ``import lib`` + ``lib.X``) —
        ``declared_only`` keeps constructor-inferred receivers out, the same
        trust boundary ``rename --include-receivers`` draws. It attributes
        **nothing** for a *dotted* receiver whose root names a package
        (``import pkg.lib`` + ``pkg.lib.X`` leaves
        ``receiver_root_symbol_id`` unset, since ``pkg`` binds no local
        name), so the second pass matches such a chain against the local
        IMPORT symbols that name the source module. Without it the single
        commonest qualified spelling would slip past a check whose whole
        purpose is to refuse it.
        """
        source_module = module_of(symbol.symbol_id)
        source_file = symbol.location.file_path
        matches = [
            ref
            for ref in self._engine.references_to_definition(
                symbol.symbol_id, declared_only=True
            )
            if ref.is_attribute_access and ref.location.file_path != source_file
        ]
        seen = {
            (ref.location.file_path, ref.location.span.start.line, ref.location.span.start.column)
            for ref in matches
        }
        for path in self._index_store.list_indexed_files():
            if path == source_file:
                continue
            index = self._index_store.load(path)
            if index is None:
                continue
            module_imports = {
                s.symbol_id: s.name
                for s in index.symbols
                if s.kind is SymbolKind.IMPORT and s.imported_from == source_module
            }
            if not module_imports:
                continue
            receivers = set(module_imports.values())
            for ref in index.references:
                if not ref.is_attribute_access:
                    continue
                if ref.symbol_id.rsplit(".", 1)[-1] != symbol.name:
                    continue
                chain = ".".join(ref.receiver_chain or ())
                if ref.receiver_root_symbol_id not in module_imports and chain not in receivers:
                    continue
                key = (
                    ref.location.file_path,
                    ref.location.span.start.line,
                    ref.location.span.start.column,
                )
                if key in seen:
                    continue
                seen.add(key)
                matches.append(ref)
        matches.sort(
            key=lambda ref: (
                ref.location.file_path,
                ref.location.span.start.line,
                ref.location.span.start.column,
            )
        )
        return matches


@register_planner(MoveSymbolIntent.kind)
def _materialize_move_symbol(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`~pypeeker.intents.MoveSymbolIntent` against ``store``.

    Same guarded-re-validation contract every registered materializer has
    (see :mod:`pypeeker.refactor.registry`). The refusal carries the failing
    precondition's name but **no** ``code``, so
    :func:`~pypeeker.app.submit.submit_intent` falls back to the caller's
    ``default_error_code`` — ``"plan-refused"`` for the CLI, keeping the
    frozen ``check --fix`` slug vocabulary untouched.

    The persisted transaction comes back through
    :func:`~pypeeker.refactor.registry.load_transaction`, which turns its
    :class:`~pypeeker.models.transaction.FileCreateEntry` into
    ``Materialized.files_created`` — so a move that brings a module into
    existence is simulatable with no per-planner wiring, and the batch
    engine's authorization rule can check it against the intent's declared
    :class:`~pypeeker.intents.footprint.Effect`.
    """
    assert isinstance(intent, MoveSymbolIntent)
    try:
        summary = MoveSymbolPlanner(store, tx_store).plan(
            intent.anchor, intent.dest_module
        )
    except MoveSymbolError as error:
        return MaterializeError(str(error), precondition=error.precondition)
    materialized = load_transaction(tx_store, summary.tx_id)
    materialized.summary = summary
    return materialized


__all__ = ["MoveSymbolError", "MoveSymbolPlanner"]
