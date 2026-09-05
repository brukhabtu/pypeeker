"""Rename-planner preconditions (:mod:`pypeeker.refactor.planner`)."""

from __future__ import annotations

from typing import Iterable

from pypeeker.models import (
    Symbol,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)
from pypeeker.storage import IndexStore


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


class RenameFlagsCompatible(Precondition):
    """``--include-exports`` and ``--keep-export`` are not combined."""

    name = "rename-flags-compatible"

    def __init__(self, include_exports: bool, keep_export: bool) -> None:
        self.include_exports = include_exports
        self.keep_export = keep_export

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.include_exports and self.keep_export:
            return _fail(
                "--include-exports and --keep-export are mutually exclusive: "
                "one changes the public export name, the other preserves it."
            )
        return _PASS


class SymbolResolvesUniquely(Precondition):
    """The symbol id matches exactly one symbol (rename).

    Caches the resolved symbol as :attr:`symbol` on a successful evaluation.
    """

    name = "symbol-resolves-uniquely"

    def __init__(self, engine: SemanticQueryEngine, symbol_id: str) -> None:
        self._engine = engine
        self.symbol_id = symbol_id
        self.symbol: Symbol | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        results = self._engine.find_symbol(self.symbol_id)
        if not results:
            return _fail(f"Symbol not found: {self.symbol_id}")
        if len(results) > 1:
            ids = [s.symbol_id for s in results]
            return _fail(
                f"Ambiguous symbol '{self.symbol_id}', matched {len(results)}: {ids}. "
                "Use the full symbol ID to disambiguate."
            )
        self.symbol = results[0]
        return _PASS


class NewNameDiffers(Precondition):
    """The new name differs from the symbol's current name."""

    name = "new-name-differs"

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.old_name == self.new_name:
            return _fail(f"New name is same as old name: {self.new_name}")
        return _PASS


class NoScopeNameConflict(Precondition):
    """No sibling symbol in the same scope already uses the new name.

    Takes the resolved symbol as a constructor argument (mid-plan value
    produced by :class:`SymbolResolvesUniquely`).
    """

    name = "no-scope-name-conflict"

    def __init__(self, index_store: IndexStore, symbol: Symbol, new_name: str) -> None:
        self._index_store = index_store
        self.symbol = symbol
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.symbol.parent_scope_id:
            index = self._index_store.load(self.symbol.location.file_path)
            if index:
                for s in index.symbols:
                    if (
                        s.parent_scope_id == self.symbol.parent_scope_id
                        and s.name == self.new_name
                        and s.symbol_id != self.symbol.symbol_id
                    ):
                        return _fail(
                            f"Name conflict: '{self.new_name}' already exists in scope "
                            f"'{self.symbol.parent_scope_id}' as {s.symbol_id}"
                        )
        return _PASS


class AffectedFilesFresh(Precondition):
    """All files the rename touches are indexed and not stale.

    Takes the computed set of affected files as a constructor argument
    (mid-plan value: the planner derives it from the edit locations).
    """

    name = "affected-files-fresh"

    def __init__(self, index_store: IndexStore, file_paths: Iterable[str]) -> None:
        self._index_store = index_store
        self.file_paths = set(file_paths)

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        for fp in self.file_paths:
            if self._index_store.is_stale(fp):
                return _fail(
                    f"File '{fp}' is stale or not indexed. "
                    "Run 'pypeeker index' first."
                )
        return _PASS
