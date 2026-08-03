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

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from pypeeker.intents import ChangeVisibilityIntent, Intent
from pypeeker.models import (
    EditEntry,
    EditOp,
    Symbol,
    SymbolKind,
    TransactionSummary,
    builtin_id,
    module_of,
)
from pypeeker.project import load_visibility_config
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.refactor.registry import (
    Materialized,
    MaterializeError,
    load_transaction,
    register_planner,
)
from pypeeker.storage import IndexStore, TransactionStore

_ALL_ASSIGNMENT_RE = re.compile(rb"^__all__\s*(?::[^=\n]+)?=\s*[\[(]", re.MULTILINE)
"""Start of a top-level ``__all__`` list/tuple assignment."""

_IMPORT_LINE_RE = re.compile(rb"^(?:import\s+\S|from\s+\S+\s+import\s)")
"""A top-level (column-0) import statement line."""

_DYNAMIC_ACCESS_IDS = frozenset(
    builtin_id(name) for name in ("getattr", "globals", "vars", "locals")
)
"""Builtins whose presence makes a module's reference evidence heuristic.

Deliberately duplicated from ``check/rules.py``'s project-wide sweep rather
than shared: ``check/**`` is a frozen oracle path for the DSL rewrite and
cannot be refactored to export this, and the two uses differ in shape anyway
(a rule quantifies over every module; the demote advisory asks about one).
"""

_SCAN_SKIP_DIRS = frozenset(
    {"__pycache__", "node_modules", "site-packages", "build", "dist", "venv"}
)
"""Directory names never counted when looking for unindexed Python files.

Dot-prefixed directories (``.venv``, ``.git``, ``.pypeeker``) are pruned
separately.
"""


class VisibilityOpError(Exception):
    """A structured promote/demote refusal: a stable ``code`` plus message.

    ``code`` identifies the refusal class machine-readably (e.g.
    ``"protected-public-api"``); ``str(error)`` carries the human-readable
    explanation. ``precondition`` (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition` for the
    ``"rename-refused"`` code, whose message is the underlying
    :class:`~pypeeker.refactor.planner.RenamePlanError`'s — every other code
    here is a visibility-specific business rule with no Precondition behind
    it, so it stays ``None``.
    """

    def __init__(
        self, code: str, message: str, *, precondition: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.precondition = precondition


class _DemoteError(VisibilityOpError):
    """Raised when a demote plan is refused."""


class _PromoteError(VisibilityOpError):
    """Raised when a promote plan is refused."""


@dataclass
class _VisibilityPlanResult:
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
    ) -> _VisibilityPlanResult:
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
        symbol = self._resolve(symbol_id, _DemoteError)
        if symbol.name.startswith("_"):
            raise _DemoteError(
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
            _DemoteError,
            symbol,
            new_name,
            include_exports=include_exports,
            keep_export=keep_export,
        )
        summary = self._finalize(summary, "demote")
        return _VisibilityPlanResult(summary=summary, warnings=warnings)

    # ------------------------------------------------------------------
    # Promote
    # ------------------------------------------------------------------

    def plan_promote(
        self, symbol_id: str, *, add_export: str | None = None
    ) -> _VisibilityPlanResult:
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
        symbol = self._resolve(symbol_id, _PromoteError)
        if not symbol.name.startswith("_"):
            raise _PromoteError(
                "already-public",
                f"Cannot promote '{symbol.symbol_id}': name '{symbol.name}' "
                "has no leading underscore.",
            )
        if symbol.name.startswith("__") and symbol.name.endswith("__"):
            raise _PromoteError(
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
            _PromoteError, symbol, new_name, include_exports=include_exports
        )
        summary = self._finalize(summary, "promote", extra_edits=export_edits)
        return _VisibilityPlanResult(summary=summary, warnings=warnings)

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
            raise error_cls(
                "rename-refused", str(e), precondition=e.precondition
            ) from e

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
            raise _DemoteError(
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
        header = loaded.header
        header.operation = operation
        edits = loaded.edits + list(extra_edits or [])
        self._transaction_store.save(
            header, edits, loaded.file_rename, loaded.creates, loaded.deletes
        )
        files = set(summary.files_affected)
        files.update(edit.file for edit in extra_edits or [])
        return replace(
            summary,
            operation=operation,
            edit_count=len(edits) + (1 if loaded.file_rename else 0),
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
            raise _PromoteError(
                "export-target",
                f"Cannot add export: package '{package}' has no indexed "
                "__init__.py.",
            )
        module = module_of(symbol.symbol_id)
        if module == package:
            raise _PromoteError(
                "export-target",
                f"Cannot add export: '{symbol.symbol_id}' is defined in "
                f"'{package}/__init__.py' itself; no import to add.",
            )
        if self._init_binds_name(init_path, new_name):
            raise _PromoteError(
                "export-target",
                f"Cannot add export: '{package}/__init__.py' already binds "
                f"the name '{new_name}'.",
            )
        if module.startswith(package + "."):
            source = "." + module[len(package) + 1:]
        else:
            source = module
        import_line = f"from {source} import {new_name}\n"

        content = self._index_store.read_file(init_path)
        file_hash = self._index_store.file_hash(init_path)

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


@register_planner(ChangeVisibilityIntent.kind)
def _materialize_change_visibility(
    intent: Intent, store: IndexStore, tx_store: TransactionStore
) -> Materialized | str:
    """Re-plan a :class:`ChangeVisibilityIntent` against ``store`` (batch materializer).

    Same guarded-re-validation contract every registered materializer has
    (see :mod:`pypeeker.refactor.registry`): dispatches on ``direction`` to
    :meth:`VisibilityPlanner.plan_demote` / :meth:`VisibilityPlanner.
    plan_promote`, returns the materialized edits (plus ``summary`` and
    ``warnings`` — TASK-123's single-intent submit path reads both to
    reproduce ``demote``/``promote``'s pre-TASK-123 JSON byte-for-byte) on
    success. A refusal is a :class:`~pypeeker.refactor.registry.
    MaterializeError` carrying the rejecting :class:`VisibilityOpError`'s
    own ``code`` (``"already-private"``, ``"protected-public-api"``,
    ``"rename-refused"``, ...) — unlike the other builtin materializers,
    promote/demote's refusals are not all the same CLI error code, so the
    plain-``str`` contract alone would lose that distinction.
    """
    assert isinstance(intent, ChangeVisibilityIntent)
    planner = VisibilityPlanner(store, tx_store)
    try:
        if intent.direction == "demote":
            result = planner.plan_demote(intent.symbol_id, keep_export=intent.keep_export)
        elif intent.direction == "promote":
            result = planner.plan_promote(intent.symbol_id, add_export=intent.add_export)
        else:
            return MaterializeError(
                f"unknown visibility direction '{intent.direction}'",
                code="invalid-direction",
            )
    except VisibilityOpError as error:
        return MaterializeError(
            str(error), code=error.code, precondition=error.precondition
        )
    materialized = load_transaction(tx_store, result.summary.tx_id)
    materialized.summary = result.summary
    materialized.warnings = result.warnings
    return materialized


def _dynamic_access_module(store: IndexStore, symbol: Symbol) -> str | None:
    """The module id of ``symbol``'s file when that file uses dynamic access.

    ``store`` loads the file index; ``symbol`` is the resolved demote target.
    Returns the defining module's symbol id when the file references any of
    ``getattr``/``globals``/``vars``/``locals`` (so name-based reference
    evidence for anything it defines is only heuristic), otherwise ``None``.
    Pointwise by design — the rule-side equivalent sweeps every index because
    a rule quantifies; a demote has exactly one anchor.
    """
    index = store.load(symbol.location.file_path)
    if index is None:
        return None
    if not any(ref.symbol_id in _DYNAMIC_ACCESS_IDS for ref in index.references):
        return None
    module = next(
        (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE), None
    )
    return module or module_of(symbol.symbol_id)


def _unindexed_python_files(
    store: IndexStore, mention: str
) -> tuple[int, int, list[str]]:
    """Survey unindexed Python files for whole-word mentions of ``mention``.

    ``store`` supplies the project root and the indexed-file set; ``mention``
    is the symbol name whose demote the caller is judging. Returns
    ``(mentioning, total, directories)``: how many unindexed files mention the
    name as a whole word, how many unindexed Python files exist at all, and
    the sorted distinct first path segments of the *mentioning* files — enough
    to say where the blind spot is (typically ``tests``) without listing every
    file. The total alone is deliberately not enough to warn on: it is
    project-constant, and an advisory the caller sees on every demote
    regardless of symbol is a banner, and banners get ignored. The
    word-boundary byte scan is textual and lossy on purpose — a dynamically
    constructed name it cannot see is exactly what the dynamic-access
    advisory covers. Dot-prefixed and build/vendor directories are pruned.
    """
    root = store.project_root
    indexed = set(store.list_indexed_files())
    pattern = re.compile(rb"\b" + re.escape(mention.encode("utf-8")) + rb"\b")
    mentioning = 0
    total = 0
    directories: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SCAN_SKIP_DIRS
        ]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath, name)
            relative = path.relative_to(root)
            if str(relative) in indexed:
                continue
            total += 1
            try:
                content = path.read_bytes()
            except OSError:  # pragma: no cover — racing delete
                continue
            if pattern.search(content):
                mentioning += 1
                parts = relative.parts
                directories.add(parts[0] if len(parts) > 1 else ".")
    return mentioning, total, sorted(directories)


def demotion_advisories(store: IndexStore, symbol_id: str) -> list[str]:
    """Honest caveats to attach to a ``demote`` of ``symbol_id``.

    ``store`` is the index store to read evidence from; ``symbol_id`` is the
    id as the caller typed it. Returns the advisories, in order, or an empty
    list when there is nothing honest to say — silence means the evidence
    behind the demote is as good as the index can make it. Nothing here
    refuses or changes the plan: a hand-typed symbol id is a deliberate
    instruction, so demote proceeds and reports what it could not see.

    Two conditions produce an advisory:

    * the defining module uses dynamic access (``getattr``/``globals``/
      ``vars``/``locals``), which is exactly the evidence quality that makes
      ``privatize`` skip a symbol (``heuristic-confidence``);
    * unindexed Python files textually mention the symbol's name — their
      references were never searched and will break; files that never
      mention the name produce no advisory, so the caveat is evidence about
      *this* symbol rather than a project-constant banner.

    An id that resolves to zero or several symbols yields no advisory — the
    planner refuses it with its own message, and duplicating that here would
    speak twice about one problem.
    """
    matches = SemanticQueryEngine(store).find_symbol(symbol_id)
    if len(matches) != 1:
        return []
    symbol = matches[0]
    advisories: list[str] = []
    module = _dynamic_access_module(store, symbol)
    if module is not None:
        advisories.append(
            f"'{symbol.symbol_id}': module '{module}' uses dynamic access "
            "(getattr/globals/vars/locals), so reference evidence for this symbol "
            "is HEURISTIC — 'privatize' skips such symbols (heuristic-confidence). "
            "Verify the edits before relying on them."
        )
    mentioning, total, directories = _unindexed_python_files(store, symbol.name)
    if mentioning:
        shown = ", ".join(directories[:3]) + ("..." if len(directories) > 3 else "")
        advisories.append(
            f"'{symbol.symbol_id}': the reference search covered only indexed files; "
            f"{mentioning} of {total} unindexed Python file(s) mention "
            f"'{symbol.name}' (under {shown}) and were not searched, so any use "
            "there will break. Index them ('pypeeker index <dir>') and re-run to "
            "include them."
        )
    return advisories
