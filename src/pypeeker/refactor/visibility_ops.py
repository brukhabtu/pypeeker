"""Visibility operations: promote/demote a symbol as planned transactions.

``demote`` plans ``name -> _name`` (public to non-public) and ``promote``
plans ``_name -> name`` (non-public to public). Both are thin orchestration
over :class:`~pypeeker.refactor.planner.RenamePlanner`, so every reference,
import, and barrel re-export is rewritten through the same engine and the
result is an ordinary pending transaction that ``apply`` / ``rollback`` /
``transactions show`` handle unchanged.

What this module adds on top of the rename engine:

* **Name-shape rules** — demote refuses an already-underscored name; promote
  strips exactly one leading underscore and refuses non-underscored or
  dunder names.
* **Hierarchy safety** — the planner's ``method-override-safe`` precondition
  fires automatically (``allow_override_rename`` is never passed), so a
  method that overrides or is overridden by another project method refuses
  to change visibility.
* **Library-mode protection** — in ``mode = "library"``
  (``[tool.pypeeker.visibility]``), a symbol barrel-exported under an
  effective public root is published API; demoting it is refused.
* **Export handling** — a barrel-exported symbol is demoted/promoted with
  ``include_exports`` so the ``__init__`` re-export (and its consumers) are
  rewritten too, with a warning in the result; ``keep_export`` instead
  aliases the re-export (``from .mod import _name as name``) so the public
  surface holds while the definition goes private.
* **Export addition** — ``promote(..., add_export="pkg")`` appends an
  ``INSERT`` edit adding ``from .mod import Name`` to ``pkg/__init__.py``
  (plus a ``__all__`` entry when ``__all__`` exists) to the same
  transaction.

The transaction header's ``operation`` field is rewritten to
``"demote"`` / ``"promote"`` after planning by re-saving the loaded
transaction through :class:`~pypeeker.storage.TransactionStore` — the same
header-rewrite pattern ``update_status`` uses — so listings report the real
operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from pypeeker.models.symbol_id import module_of
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.transaction import EditEntry, EditOp, TransactionSummary
from pypeeker.project import load_visibility_config
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.storage import IndexStore, TransactionStore

_ALL_ASSIGNMENT_RE = re.compile(rb"^__all__\s*(?::[^=\n]+)?=\s*[\[(]", re.MULTILINE)
"""Start of a top-level ``__all__`` list/tuple assignment."""

_IMPORT_LINE_RE = re.compile(rb"^(?:import\s+\S|from\s+\S+\s+import\s)")
"""A top-level (column-0) import statement line."""


class VisibilityOpError(Exception):
    """A structured promote/demote refusal: a stable ``code`` plus message.

    ``code`` identifies the refusal class machine-readably (e.g.
    ``"protected-public-api"``); ``str(error)`` carries the human-readable
    explanation.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DemoteError(VisibilityOpError):
    """Raised when a demote plan is refused."""


class PromoteError(VisibilityOpError):
    """Raised when a promote plan is refused."""


@dataclass
class VisibilityPlanResult:
    """A planned visibility change: the persisted transaction plus warnings.

    ``summary.operation`` is ``"demote"`` or ``"promote"``; ``warnings``
    carries non-fatal notes (e.g. that barrel consumers were rewritten).
    """

    summary: TransactionSummary
    warnings: list[str] = field(default_factory=list)


class VisibilityPlanner:
    """Plans demote (``name -> _name``) and promote (``_name -> name``).

    Usage:
        planner = VisibilityPlanner(index_store, transaction_store)
        result = planner.plan_demote("pkg.mod:helper")
    """

    def __init__(
        self,
        index_store: IndexStore,
        transaction_store: TransactionStore,
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store
        self._engine = SemanticQueryEngine(index_store)

    # ------------------------------------------------------------------
    # Demote
    # ------------------------------------------------------------------

    def plan_demote(
        self, symbol_id: str, *, keep_export: bool = False
    ) -> VisibilityPlanResult:
        """Plan demoting a public symbol to non-public (``name -> _name``).

        Refusals (:class:`DemoteError`):

        * ``already-private`` — the name already starts with an underscore;
        * ``protected-public-api`` — library mode and the symbol is
          barrel-exported under an effective public root (published API);
        * ``rename-refused`` — any rename precondition failed (name conflict
          in scope, method override pair, stale index, ...); the planner's
          message is preserved.

        Export handling: a barrel-exported symbol is planned with
        ``include_exports`` so the ``__init__`` re-export and its consumers
        switch to the private name (a warning notes the public surface
        changed). With ``keep_export`` the re-export is aliased
        (``from .mod import _name as name``) instead: the definition goes
        private while the package keeps exporting the public name.
        """
        symbol = self._resolve(symbol_id, DemoteError)
        if symbol.name.startswith("_"):
            raise DemoteError(
                "already-private",
                f"Cannot demote '{symbol.symbol_id}': name '{symbol.name}' "
                "already starts with an underscore.",
            )
        new_name = "_" + symbol.name

        barrel_exports = self._barrel_exports(symbol)
        self._refuse_if_public_root_protected(symbol, barrel_exports)

        warnings: list[str] = []
        include_exports = bool(barrel_exports) and not keep_export
        if include_exports:
            packages = sorted({module_of(imp.symbol_id) for imp in barrel_exports})
            warnings.append(
                f"'{symbol.name}' is barrel-exported by {', '.join(packages)}; "
                f"the export and its consumers were rewritten to '{new_name}' "
                "— the public API surface changed. Use keep_export to hold "
                "the public name."
            )

        summary = self._plan_rename(
            DemoteError,
            symbol,
            new_name,
            include_exports=include_exports,
            keep_export=keep_export,
        )
        summary = self._finalize(summary, "demote")
        return VisibilityPlanResult(summary=summary, warnings=warnings)

    # ------------------------------------------------------------------
    # Promote
    # ------------------------------------------------------------------

    def plan_promote(
        self, symbol_id: str, *, add_export: str | None = None
    ) -> VisibilityPlanResult:
        """Plan promoting a non-public symbol to public (``_name -> name``).

        The new name strips exactly one leading underscore. Refusals
        (:class:`PromoteError`):

        * ``already-public`` — the name has no leading underscore;
        * ``dunder`` — dunder names (``__init__``) have no visibility to
          promote;
        * ``export-target`` — ``add_export`` names a package without an
          indexed ``__init__.py``, the symbol's own package ``__init__``, or
          a package whose ``__init__`` already binds the public name;
        * ``rename-refused`` — any rename precondition failed (name
          conflict, method override pair, ...); the planner's message is
          preserved.

        Without ``add_export`` the promote is just the rename (existing
        barrel re-exports of the private name are rewritten so they stay
        valid, with a warning). With ``add_export`` (a dotted package path)
        the transaction additionally inserts ``from .mod import Name`` into
        that package's ``__init__.py`` after its last top-level import line,
        and prepends ``"Name"`` to ``__all__`` when one exists. Insertion is
        line-based and simple by design: an ``__init__`` with no top-level
        imports gets the line at the top of the file (before any docstring),
        and ``__all__`` detection assumes a literal list/tuple assignment.
        """
        symbol = self._resolve(symbol_id, PromoteError)
        if not symbol.name.startswith("_"):
            raise PromoteError(
                "already-public",
                f"Cannot promote '{symbol.symbol_id}': name '{symbol.name}' "
                "has no leading underscore.",
            )
        if symbol.name.startswith("__") and symbol.name.endswith("__"):
            raise PromoteError(
                "dunder",
                f"Cannot promote '{symbol.symbol_id}': '{symbol.name}' is a "
                "dunder name, not a private symbol.",
            )
        new_name = symbol.name[1:]

        barrel_exports = self._barrel_exports(symbol)
        warnings: list[str] = []
        include_exports = bool(barrel_exports)
        if include_exports:
            packages = sorted({module_of(imp.symbol_id) for imp in barrel_exports})
            warnings.append(
                f"'{symbol.name}' is barrel-exported by {', '.join(packages)}; "
                f"the export and its consumers were rewritten to '{new_name}'."
            )

        # Validate the export target (and build the edits against current
        # file content) BEFORE planning, so a refused add_export does not
        # leave a half-meant transaction behind.
        export_edits: list[EditEntry] = []
        if add_export is not None:
            export_edits = self._build_export_edits(symbol, new_name, add_export)

        summary = self._plan_rename(
            PromoteError, symbol, new_name, include_exports=include_exports
        )
        summary = self._finalize(summary, "promote", extra_edits=export_edits)
        return VisibilityPlanResult(summary=summary, warnings=warnings)

    # ------------------------------------------------------------------
    # Shared plumbing
    # ------------------------------------------------------------------

    def _resolve(
        self, symbol_id: str, error_cls: type[VisibilityOpError]
    ) -> Symbol:
        """Resolve ``symbol_id`` to exactly one symbol, or raise ``error_cls``."""
        matches = self._engine.find_symbol(symbol_id)
        if not matches:
            raise error_cls("not-found", f"Symbol not found: {symbol_id}")
        if len(matches) > 1:
            ids = [s.symbol_id for s in matches]
            raise error_cls(
                "ambiguous",
                f"Ambiguous symbol '{symbol_id}', matched {len(matches)}: "
                f"{ids}. Use the full symbol ID to disambiguate.",
            )
        return matches[0]

    def _plan_rename(
        self,
        error_cls: type[VisibilityOpError],
        symbol: Symbol,
        new_name: str,
        *,
        include_exports: bool = False,
        keep_export: bool = False,
    ) -> TransactionSummary:
        """Run the rename engine, converting its refusal into ``error_cls``.

        ``allow_override_rename`` is deliberately never passed: the planner's
        ``method-override-safe`` precondition is the hierarchy refusal for
        both operations. The planner's message (including its
        ``allow_override_rename`` escape-hatch wording, which applies to
        ``plan-rename``, not to demote/promote) is preserved verbatim.
        """
        planner = RenamePlanner(self._index_store, self._transaction_store)
        try:
            return planner.plan(
                symbol.symbol_id,
                new_name,
                include_exports=include_exports,
                keep_export=keep_export,
            )
        except RenamePlanError as e:
            raise error_cls("rename-refused", str(e)) from e

    def _barrel_exports(self, symbol: Symbol) -> list[Symbol]:
        """IMPORT symbols in ``__init__.py`` files that re-export ``symbol``."""
        return [
            imp
            for imp in self._engine.find_importers(symbol.symbol_id)
            if imp.location.file_path.endswith("__init__.py")
        ]

    def _refuse_if_public_root_protected(
        self, symbol: Symbol, barrel_exports: list[Symbol]
    ) -> None:
        """Refuse a demote of library-mode published API.

        Mirrors the check engine's public-root protection (see
        ``check.rules._public_root_protected``): in library mode, a symbol
        barrel-exported by a package at or under an effective public root is
        the library's published API — external consumers are invisible to
        the index, so demoting it silently breaks them.
        """
        if not barrel_exports:
            return
        vis = load_visibility_config(self._index_store.project_root)
        if not vis.is_library:
            return
        roots = vis.effective_public_roots(self._top_level_packages())
        protected_by = sorted(
            package
            for package in {module_of(imp.symbol_id) for imp in barrel_exports}
            if any(
                package == root or package.startswith(root + ".")
                for root in roots
            )
        )
        if protected_by:
            raise DemoteError(
                "protected-public-api",
                f"Cannot demote '{symbol.symbol_id}': it is barrel-exported "
                f"by {', '.join(protected_by)} under a public root — "
                "protected public API (library mode).",
            )

    def _top_level_packages(self) -> list[str]:
        """First segment of every indexed module's dotted path."""
        packages: set[str] = set()
        for file_path in self._index_store.list_indexed_files():
            index = self._index_store.load(file_path)
            if index is None:
                continue
            for s in index.symbols:
                if s.kind is SymbolKind.MODULE:
                    packages.add(s.symbol_id.split(".")[0])
                    break
        return sorted(packages)

    def _finalize(
        self,
        summary: TransactionSummary,
        operation: str,
        extra_edits: list[EditEntry] | None = None,
    ) -> TransactionSummary:
        """Stamp the real operation on the saved transaction (+ extra edits).

        ``RenamePlanner.plan`` persists a header whose ``operation`` defaults
        to ``"rename"``; this reloads the transaction, rewrites the header
        field (the ``TransactionStore.update_status`` pattern), appends any
        extra edits, and re-saves — apply/rollback/preview see one ordinary
        transaction.
        """
        loaded = self._transaction_store.load(summary.tx_id)
        if loaded is None:  # pragma: no cover — the planner just saved it
            raise VisibilityOpError(
                "transaction-missing",
                f"Transaction {summary.tx_id} disappeared after planning.",
            )
        header, edits, file_rename = loaded
        header.operation = operation
        edits = edits + list(extra_edits or [])
        self._transaction_store.save(header, edits, file_rename)
        files = set(summary.files_affected)
        files.update(edit.file for edit in extra_edits or [])
        return replace(
            summary,
            operation=operation,
            edit_count=len(edits) + (1 if file_rename else 0),
            files_affected=sorted(files),
        )

    # ------------------------------------------------------------------
    # Export addition (promote --add-export)
    # ------------------------------------------------------------------

    def _build_export_edits(
        self, symbol: Symbol, new_name: str, package: str
    ) -> list[EditEntry]:
        """INSERT edits adding ``from .mod import new_name`` to a package.

        Limits, by design: the import line goes after the last top-level
        (column-0) import line, or at the very top of the file when there is
        none (before any docstring); the ``__all__`` update assumes a
        top-level literal list/tuple assignment and prepends the new name
        right after the opening bracket. Both edits carry the plan-time file
        hash, so the applier refuses if the ``__init__`` changed since.
        """
        init_path = self._package_init_path(package)
        if init_path is None:
            raise PromoteError(
                "export-target",
                f"Cannot add export: package '{package}' has no indexed "
                "__init__.py.",
            )
        module = module_of(symbol.symbol_id)
        if module == package:
            raise PromoteError(
                "export-target",
                f"Cannot add export: '{symbol.symbol_id}' is defined in "
                f"'{package}/__init__.py' itself; no import to add.",
            )
        if self._init_binds_name(init_path, new_name):
            raise PromoteError(
                "export-target",
                f"Cannot add export: '{package}/__init__.py' already binds "
                f"the name '{new_name}'.",
            )
        if module.startswith(package + "."):
            source = "." + module[len(package) + 1:]
        else:
            source = module
        import_line = f"from {source} import {new_name}\n"

        init_file = self._index_store.project_root / init_path
        content = init_file.read_bytes()
        file_hash = IndexStore.compute_file_hash(init_file)

        import_offset = _import_insert_offset(content)
        edits = [
            EditEntry(
                op=EditOp.INSERT,
                file=init_path,
                start=import_offset,
                end=import_offset,
                old="",
                new=import_line,
                file_hash=file_hash,
            )
        ]
        all_insert = _dunder_all_insert(content, new_name)
        if all_insert is not None:
            offset, text = all_insert
            edits.append(
                EditEntry(
                    op=EditOp.INSERT,
                    file=init_path,
                    start=offset,
                    end=offset,
                    old="",
                    new=text,
                    file_hash=file_hash,
                )
            )
        return edits

    def _package_init_path(self, package: str) -> str | None:
        """The indexed ``__init__.py`` file path of a dotted package, or None."""
        for file_path in self._index_store.list_indexed_files():
            if not file_path.endswith("__init__.py"):
                continue
            index = self._index_store.load(file_path)
            if index is None:
                continue
            for s in index.symbols:
                if s.kind is SymbolKind.MODULE:
                    if s.symbol_id == package:
                        return file_path
                    break
        return None

    def _init_binds_name(self, init_path: str, name: str) -> bool:
        """True when the package ``__init__`` already binds ``name`` top-level."""
        index = self._index_store.load(init_path)
        if index is None:
            return False
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE),
            None,
        )
        return any(
            s.name == name and s.parent_scope_id == module_id
            for s in index.symbols
        )


def _import_insert_offset(content: bytes) -> int:
    """Byte offset just after the last top-level import line (0 when none).

    A simple line scan: any column-0 line matching ``import X`` /
    ``from X import`` counts, including lines inside a triple-quoted string
    (documented limit of the simple approach).
    """
    offset = 0
    insert_at = 0
    for line in content.split(b"\n"):
        line_end = offset + len(line) + 1  # +1 for the newline
        if _IMPORT_LINE_RE.match(line):
            insert_at = min(line_end, len(content))
        offset = line_end
    return insert_at


def _dunder_all_insert(content: bytes, name: str) -> tuple[int, str] | None:
    """(offset, text) prepending ``name`` to ``__all__``, or None when absent.

    Inserts immediately after the opening bracket of a top-level
    ``__all__ = [...]`` / ``(...)`` assignment, which sidesteps trailing-comma
    handling: ``["a"]`` becomes ``["name", "a"]`` and ``[]`` becomes
    ``["name"]``.
    """
    match = _ALL_ASSIGNMENT_RE.search(content)
    if match is None:
        return None
    open_bracket = match.end() - 1
    close = b"]" if content[open_bracket:open_bracket + 1] == b"[" else b")"
    close_at = content.find(close, open_bracket + 1)
    if close_at < 0:
        return None  # unterminated — leave __all__ alone
    is_empty = not content[open_bracket + 1:close_at].strip()
    text = f'"{name}"' if is_empty else f'"{name}", '
    return open_bracket + 1, text
