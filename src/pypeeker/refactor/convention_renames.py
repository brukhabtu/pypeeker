"""Convention-violation findings -> batch rename intents (TASK-91).

The naming-conventions rule *detects* per file; the *fix* is a cross-module
rename — definition, importers, call sites, optionally barrel re-exports —
which is exactly :class:`~pypeeker.refactor.intents.RenameIntent` territory:
too big for the per-file Fix protocol, sized for
:func:`~pypeeker.refactor.batch.run_batch`. This module is the converter in
the workflow ``check (rule) -> converter -> plan-batch``.

Layering: ``refactor`` may not import ``check`` (rules carry fixes; fixes
ride down into refactor-land, not the other way — see
:mod:`pypeeker.refactor.intents`). The converter therefore takes plain
``(symbol_id, suggested_name)`` pairs; the **caller** (tests, a CLI
follow-up) extracts them from violations, e.g. via
``pypeeker.check.builtin.naming_conventions.rename_pair``.

Declared/direct gating: the emitted intents inherit
:class:`~pypeeker.refactor.planner.RenamePlanner`'s reference discipline by
construction — only references binding to the definition or to an import
being renamed are edited, and receiver-resolved call sites are touched only
under ``include_receivers`` with ``declared_only`` filtering (this module
leaves ``include_receivers`` off). Nothing here re-implements that gating;
the planner is the single owner.

Collision policy (pre-batch, deterministic): the scheduler would catch two
renames of one symbol, and each planner's ``NoScopeNameConflict`` would catch
a taken target — but only at batch time, with planner-shaped errors. The
converter pre-checks the cheap cases so the skip report says *why in naming
terms*, in submission order (earlier pair wins; see :class:`SkipReason`).
Method override safety is also pre-checked via
:class:`~pypeeker.analysis.Hierarchy` for the same reason — the planner's
``MethodOverrideSafe`` precondition remains the authoritative batch-time
backstop, this is a courtesy triage. Conservative by construction: a rename
*freeing* a name within the same batch is not credited (the taken-name check
reads the current index only).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pypeeker.analysis import Hierarchy
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.refactor.intents import RenameIntent

if TYPE_CHECKING:
    from pypeeker.storage import IndexStore

INTENT_ID_PREFIX = "convention-rename:"
"""Emitted intent ids are ``convention-rename:<symbol_id>`` — deterministic,
unique (duplicate symbols are skipped), and self-describing in batch reports."""


class SkipReason(str, Enum):
    """Machine-readable reason a pair did not become a rename intent.

    * ``NO_OP`` — the suggested name equals the current name.
    * ``SYMBOL_NOT_FOUND`` — the symbol id is in no loaded index.
    * ``DUPLICATE_SYMBOL`` — an earlier pair already targets this symbol
      (two renames of one symbol have no resolving order; the batch
      scheduler would hard-conflict-drop the later one anyway).
    * ``TARGET_EXISTS`` — a symbol with the suggested name already exists in
      the same scope (the planner's ``NoScopeNameConflict`` would refuse).
    * ``PENDING_COLLISION`` — an earlier pair in this batch already claims
      the suggested name in the same scope: executing both would land two
      same-named symbols in one scope, which no ordering fixes, so the
      later-submitted pair is skipped deterministically.
    * ``OVERRIDE_UNSAFE`` — a METHOD with override edges (or an incomplete
      class hierarchy): renaming one side splits the override contract. The
      planner's ``MethodOverrideSafe`` precondition is the batch-time
      backstop; pre-checking yields this naming-flavoured reason instead of
      a generic precondition failure.
    """

    NO_OP = "no-op"
    SYMBOL_NOT_FOUND = "symbol-not-found"
    DUPLICATE_SYMBOL = "duplicate-symbol"
    TARGET_EXISTS = "target-exists"
    PENDING_COLLISION = "pending-collision"
    OVERRIDE_UNSAFE = "override-unsafe"


@dataclass(frozen=True)
class SkippedRename:
    """One ``(symbol_id, new_name)`` pair the converter declined, with why.

    ``reason`` is machine-readable (:class:`SkipReason`), ``detail``
    human-readable and names the conflicting party where one exists.
    """

    symbol_id: str
    new_name: str
    reason: SkipReason
    detail: str = ""


def _intent_id(symbol_id: str) -> str:
    """The deterministic intent id for renaming ``symbol_id``."""
    return f"{INTENT_ID_PREFIX}{symbol_id}"


def _load_symbols(store: "IndexStore") -> dict[str, Symbol]:
    """Every symbol in every loaded index, keyed by symbol_id."""
    symbols: dict[str, Symbol] = {}
    for path in store.list_indexed_files():
        index = store.load(path)
        if index is None:
            continue
        for symbol in index.symbols:
            symbols[symbol.symbol_id] = symbol
    return symbols


def _names_by_scope(symbols: Iterable[Symbol]) -> dict[str | None, set[str]]:
    """Names currently bound per scope id (the taken-name check's read)."""
    out: dict[str | None, set[str]] = {}
    for symbol in symbols:
        out.setdefault(symbol.parent_scope_id, set()).add(symbol.name)
    return out


def _override_problem(hierarchy: Hierarchy, symbol: Symbol) -> str | None:
    """Why renaming this METHOD is override-unsafe, or None when it is safe.

    Mirrors the planner's ``MethodOverrideSafe`` checks (override edges in
    either direction, then incomplete MRO) so the pre-check and the
    batch-time precondition agree on what is unsafe.
    """
    overrides = hierarchy.overrides(symbol.symbol_id)
    if overrides:
        return f"overrides {', '.join(overrides)}"
    overridden_by = hierarchy.overridden_by(symbol.symbol_id)
    if overridden_by:
        return f"is overridden by {', '.join(overridden_by)}"
    owning_class = symbol.parent_scope_id
    if owning_class is not None and hierarchy.mro_unknown(owning_class):
        return (
            f"class '{owning_class}' has unresolved or external bases, so "
            "override relationships cannot be verified"
        )
    return None


def convention_rename_intents(
    store: "IndexStore",
    pairs: Sequence[tuple[str, str]],
    *,
    include_exports: bool = False,
) -> tuple[list[RenameIntent], list[SkippedRename]]:
    """Turn ``(symbol_id, suggested_name)`` pairs into batch rename intents.

    Pairs are processed in submission order; each either becomes a
    :class:`~pypeeker.refactor.intents.RenameIntent` with the deterministic
    id ``convention-rename:<symbol_id>`` or lands in the skipped list with a
    :class:`SkipReason` (see that enum for the inventory — taken targets,
    pending same-scope collisions where the later-submitted pair loses,
    override-unsafe methods, and the trivial no-op / not-found / duplicate
    cases). ``include_exports`` is passed through to every intent: with it,
    barrel (``__init__.py``) re-exports and their consumers are renamed too;
    without it the planner leaves the export surface alone (its documented
    policy). Reference gating is the planner's, not re-implemented here —
    see the module docstring.

    The returned intents feed :func:`~pypeeker.refactor.batch.run_batch`
    directly, or :func:`write_intents_file` for the plan-batch CLI.
    """
    symbols = _load_symbols(store)
    names_by_scope = _names_by_scope(symbols.values())
    hierarchy: Hierarchy | None = None

    intents: list[RenameIntent] = []
    skipped: list[SkippedRename] = []
    seen_symbols: set[str] = set()
    claimed: dict[tuple[str | None, str], str] = {}

    for symbol_id, new_name in pairs:
        def skip(reason: SkipReason, detail: str = "") -> None:
            """Record this pair as skipped."""
            skipped.append(SkippedRename(symbol_id, new_name, reason, detail))

        if symbol_id in seen_symbols:
            skip(
                SkipReason.DUPLICATE_SYMBOL,
                f"an earlier pair already renames '{symbol_id}'",
            )
            continue
        seen_symbols.add(symbol_id)

        symbol = symbols.get(symbol_id)
        if symbol is None:
            skip(SkipReason.SYMBOL_NOT_FOUND, "symbol is in no loaded index")
            continue
        if new_name == symbol.name:
            skip(SkipReason.NO_OP, f"'{symbol.name}' already has the suggested name")
            continue

        scope = symbol.parent_scope_id
        if new_name in names_by_scope.get(scope, ()):
            skip(
                SkipReason.TARGET_EXISTS,
                f"'{new_name}' is already bound in scope '{scope}'",
            )
            continue
        earlier = claimed.get((scope, new_name))
        if earlier is not None:
            skip(
                SkipReason.PENDING_COLLISION,
                f"'{earlier}' already claims '{new_name}' in scope '{scope}'",
            )
            continue

        if symbol.kind is SymbolKind.METHOD:
            if hierarchy is None:
                hierarchy = Hierarchy.from_store(store)
            problem = _override_problem(hierarchy, symbol)
            if problem is not None:
                skip(SkipReason.OVERRIDE_UNSAFE, f"'{symbol_id}' {problem}")
                continue

        claimed[(scope, new_name)] = _intent_id(symbol_id)
        intents.append(
            RenameIntent(
                _intent_id(symbol_id),
                symbol_id,
                new_name,
                include_exports=include_exports,
            )
        )
    return intents, skipped


def write_intents_file(intents: Iterable[RenameIntent], path: Path | str) -> Path:
    """Write ``intents`` as a plan-batch-compatible JSON intents file.

    Emits the ``{"kind": "rename", "id": ..., "symbol_id": ...,
    "new_name": ...}`` entry shape the CLI's plan-batch command consumes
    (``cli._build_batch_intents``); option flags are included only when set
    and ``deps`` only when non-empty, keeping files minimal and stable.
    Returns the written path, so the workflow is
    ``check (rule) -> convention_rename_intents -> write_intents_file ->
    pypeeker plan-batch <file>``.
    """
    entries: list[dict[str, object]] = []
    for intent in intents:
        entry: dict[str, object] = {
            "kind": "rename",
            "id": intent.intent_id,
            "symbol_id": intent.symbol_id,
            "new_name": intent.new_name,
        }
        for flag in (
            "include_file",
            "include_exports",
            "include_receivers",
            "keep_export",
            "allow_override_rename",
        ):
            if getattr(intent, flag):
                entry[flag] = True
        if intent.deps:
            entry["deps"] = sorted(intent.deps)
        entries.append(entry)
    target = Path(path)
    target.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return target


__all__ = [
    "INTENT_ID_PREFIX",
    "SkipReason",
    "SkippedRename",
    "convention_rename_intents",
    "write_intents_file",
]
