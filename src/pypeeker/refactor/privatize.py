"""Batch demotion of over-exposed public symbols (TASK-92).

Findings from the visibility-shaped check rules (``unused-public-symbol``,
``test-only-production-code``, the TASK-81 over-exposure rules) share one
mechanized fix: demote the symbol (``name -> _name``) across the project,
rewriting barrel re-exports and their consumers. Single-symbol demotion is
:meth:`~pypeeker.refactor.visibility_ops.VisibilityPlanner.plan_demote`;
this module is the *batch* counterpart, built for direct reuse by TASK-97
(mass demotion): collisions and ordering between many demotions are handled
by routing every demotion through the batch machinery
(:func:`~pypeeker.refactor.batch.run_batch` /
:func:`~pypeeker.refactor.batch.flatten_batch`), so the result is ONE
ordinary pending transaction that ``apply`` / ``rollback`` handle unchanged.

Three layers, each independently reusable (TASK-97 should compose them the
same way :func:`plan_privatize` does):

* :func:`demote_candidates` — pre-filter plain symbol ids into
  :class:`DemoteCandidate` / :class:`SkippedSymbol` with machine-readable
  skip reasons (already-private names, dunders, heuristic-confidence
  findings, hierarchy-unsafe methods, library-mode published API, ``_name``
  collisions — including collisions *among* the pending batch).
* :func:`demote_intents` — turn candidates into
  :class:`~pypeeker.refactor.intents.RenameIntent` objects whose
  ``include_exports`` mirrors ``plan_demote``'s app-mode barrel handling.
* :func:`plan_privatize` — run the intents as a simulated batch on a
  temporary mirror, flatten the net change, persist it, and report what
  executed / dropped / was skipped.

Layering contract (important for TASK-97): ``refactor`` may not import
``check`` (see :mod:`pypeeker.refactor.intents`), so this module never sees
:class:`~pypeeker.check.models.Violation` objects. Callers that start from
check findings must extract ``(symbol_id, confidence_str)`` pairs themselves
— e.g. ``[(violation_symbol_id, violation.confidence.value) for v in
violations]`` — and feed those to :func:`demote_candidates` /
:func:`plan_privatize`. Confidence travels as a plain string; any value
equal to ``"heuristic"`` (the :class:`~pypeeker.models.capabilities.
Confidence` ``HEURISTIC`` member's value) marks the finding as
dynamic-access-adjacent and excludes it from auto-fix by default.

Export-handling limitation, by design: ``plan_demote`` offers ``keep_export``
(alias the re-export as ``from .mod import _name as name`` so the public
surface holds). Batch demotion uses **export-rewrite mode only** — every
barrel-exported candidate is planned with ``include_exports`` so the
``__init__`` re-export and its consumers switch to the private name. A
keep-export demotion changes per-symbol policy and stays a single-symbol
decision via the ``demote --keep-export`` CLI; :class:`RenameIntent` could
carry ``keep_export``, but mixing surface-preserving and surface-changing
demotions in one mass batch would make the resulting public API a function
of batch composition, which is exactly the kind of guessing this module
refuses to do.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from pypeeker.analysis.hierarchy import Hierarchy
from pypeeker.models.capabilities import Confidence
from pypeeker.models.symbol_id import module_of
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.transaction import TransactionSummary
from pypeeker.project import load_visibility_config
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.refactor.batch import (
    BatchPolicy,
    DroppedIntent,
    flatten_batch,
    run_batch,
)
from pypeeker.refactor.intents import RenameIntent
from pypeeker.storage import IndexStore, TransactionStore

PRIVATIZE_OPERATION = "privatize"
"""The ``operation`` stamped on the flattened transaction header."""

_ALL_ASSIGNMENT_RE = re.compile(rb"^__all__\s*(?::[^=\n]+)?=\s*[\[(]", re.MULTILINE)
"""Start of a top-level ``__all__`` list/tuple assignment.

Same pattern as ``visibility_ops._ALL_ASSIGNMENT_RE`` (a private constant of
that module, replicated rather than reached into).
"""

_METHOD_KINDS = (SymbolKind.METHOD, SymbolKind.PROPERTY)
"""Symbol kinds whose demotion must clear the class-hierarchy safety check."""

type CandidateEntry = str | tuple[str, str | None]
"""Accepted input shapes: a plain symbol id, or ``(symbol_id, confidence)``.

The confidence string is the :class:`~pypeeker.models.capabilities.Confidence`
*value* carried by the check finding that nominated the symbol (``None`` when
the caller has no finding metadata). This is the whole cross-layer contract:
TASK-97 callers reduce each violation to this pair — no check types cross
into refactor-land.
"""


@dataclass(frozen=True)
class DemoteCandidate:
    """A symbol that passed every pre-filter and is safe to plan for demotion.

    ``symbol_id`` is the fully resolved id (even when the caller submitted a
    shorthand); ``new_name`` is always ``"_" + name``. ``include_exports``
    and ``barrel_packages`` mirror ``plan_demote``'s app-mode export
    handling: when the symbol is barrel-exported, the batch rename rewrites
    the ``__init__`` re-export and its consumers to the private name (the
    packages are recorded so callers can warn that the public surface
    changed). ``barrel_inits`` holds those ``__init__.py`` file paths —
    :func:`plan_privatize` rewrites any stale ``__all__`` entries there.
    ``confidence`` echoes the submitted finding confidence.
    """

    symbol_id: str
    name: str
    new_name: str
    file_path: str
    include_exports: bool = False
    barrel_packages: tuple[str, ...] = ()
    barrel_inits: tuple[str, ...] = ()
    confidence: str | None = None


@dataclass(frozen=True)
class SkippedSymbol:
    """A submitted symbol the pre-filter excluded, with a stable reason code.

    ``reason`` is machine-readable (one of ``not-found``, ``ambiguous``,
    ``already-private``, ``dunder-or-main``, ``heuristic-confidence``,
    ``hierarchy-unsafe``, ``protected-public-api``, ``name-collision``,
    ``pending-collision``); ``detail`` is the human-readable explanation.
    ``symbol_id`` is the id *as submitted*, so reports map back to the
    caller's input.
    """

    symbol_id: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class ExecutedDemotion:
    """One demotion the batch executed: which symbol became which name."""

    intent_id: str
    symbol_id: str
    new_name: str


@dataclass
class PrivatizeOutcome:
    """The result of :func:`plan_privatize`: one transaction plus the report.

    ``summary`` is the persisted flattened transaction (operation
    ``"privatize"``), or ``None`` when nothing executed or the batch was a
    net no-op. ``executed`` lists the demotions that made it into the
    transaction; ``dropped`` carries batch-machinery drops (precondition
    failures, hard conflicts — see
    :class:`~pypeeker.refactor.batch.DroppedIntent`); ``skipped`` carries the
    pre-filter exclusions; ``warnings`` notes public-surface changes (barrel
    rewrites). TASK-97 can render this shape directly: it deliberately
    mirrors the ``plan-batch`` CLI's ``{tx_id, executed, dropped, ...}``
    report plus the pre-filter column that command does not have.
    """

    summary: TransactionSummary | None
    executed: list[ExecutedDemotion] = field(default_factory=list)
    dropped: tuple[DroppedIntent, ...] = ()
    skipped: list[SkippedSymbol] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalize_entries(
    symbol_ids: list[CandidateEntry],
) -> list[tuple[str, str | None]]:
    """Normalize the accepted input shapes to ``(symbol_id, confidence)`` pairs."""
    normalized: list[tuple[str, str | None]] = []
    for entry in symbol_ids:
        if isinstance(entry, str):
            normalized.append((entry, None))
        else:
            symbol_id, confidence = entry
            normalized.append((symbol_id, confidence))
    return normalized


def _is_heuristic(confidence: str | None) -> bool:
    """True when a finding's confidence string marks it dynamic-access-adjacent."""
    return confidence is not None and confidence == Confidence.HEURISTIC.value


def _top_level_packages(store: IndexStore) -> list[str]:
    """First segment of every indexed module's dotted path.

    Minimal replica of ``VisibilityPlanner._top_level_packages`` (a private
    method of :class:`~pypeeker.refactor.visibility_ops.VisibilityPlanner`,
    not importable without constructing a planner — which needs a
    transaction store this pre-filter deliberately does not take).
    """
    packages: set[str] = set()
    for file_path in store.list_indexed_files():
        index = store.load(file_path)
        if index is None:
            continue
        for symbol in index.symbols:
            if symbol.kind is SymbolKind.MODULE:
                packages.add(symbol.symbol_id.split(".")[0])
                break
    return sorted(packages)


def _protected_packages(
    store: IndexStore, barrel_packages: tuple[str, ...]
) -> list[str]:
    """Barrel packages at/under an effective public root (library mode only).

    Minimal replica of the protected computation in
    ``VisibilityPlanner._refuse_if_public_root_protected`` (same semantics,
    same config source): in ``mode = "library"`` a symbol barrel-exported
    under an effective public root is published API — external consumers are
    invisible to the index, so demoting it silently breaks them.
    """
    if not barrel_packages:
        return []
    vis = load_visibility_config(store.project_root)
    if not vis.is_library:
        return []
    roots = vis.effective_public_roots(_top_level_packages(store))
    return sorted(
        package
        for package in set(barrel_packages)
        if any(
            package == root or package.startswith(root + ".") for root in roots
        )
    )


def _scope_binds_name(store: IndexStore, symbol: Symbol, name: str) -> bool:
    """True when ``symbol``'s own scope already binds ``name`` in its file."""
    index = store.load(symbol.location.file_path)
    if index is None:
        return False
    return any(
        s.name == name and s.parent_scope_id == symbol.parent_scope_id
        for s in index.symbols
    )


def _hierarchy_detail(hierarchy: Hierarchy, symbol: Symbol) -> str | None:
    """Why demoting this method is hierarchy-unsafe, or ``None`` when safe.

    Mirrors the rename planner's ``method-override-safe`` refusal — and goes
    one conservative step further with ``mro_unknown``: a method on a class
    whose base chain is incomplete (external/dynamic bases) *may* override
    something the index cannot see, so the batch pre-filter skips it rather
    than half-renaming an override pair.
    """
    overrides = hierarchy.overrides(symbol.symbol_id)
    if overrides:
        return f"overrides {', '.join(sorted(overrides))}"
    overridden_by = hierarchy.overridden_by(symbol.symbol_id)
    if overridden_by:
        return f"overridden by {', '.join(sorted(overridden_by))}"
    owner = symbol.parent_scope_id
    if owner is not None and hierarchy.mro_unknown(owner):
        return (
            f"owning class '{owner}' has an incomplete base chain "
            "(mro unknown) — an unseen override may exist"
        )
    return None


def demote_candidates(
    store: IndexStore,
    symbol_ids: list[CandidateEntry],
    *,
    skip_heuristic: bool = True,
) -> tuple[list[DemoteCandidate], list[SkippedSymbol]]:
    """Pre-filter symbols nominated for demotion into candidates and skips.

    ``symbol_ids`` entries are plain symbol ids or ``(symbol_id,
    confidence)`` pairs — see :data:`CandidateEntry` for the cross-layer
    contract (TASK-97: extract the pair from each violation; no check types
    enter this module). Entries are processed in input order; the returned
    lists preserve it, which is what makes the pending-collision rule
    deterministic.

    Skip reasons (stable codes on :class:`SkippedSymbol`):

    * ``not-found`` / ``ambiguous`` — the id resolved to zero / multiple
      symbols;
    * ``dunder-or-main`` — dunder names and ``main`` have conventional
      meaning and are never demoted (checked before ``already-private``,
      so dunders report this more precise reason);
    * ``already-private`` — the name already starts with an underscore;
    * ``heuristic-confidence`` — the nominating finding carried
      ``"heuristic"`` confidence (dynamic access nearby may consume the
      symbol invisibly); excluded from auto-fix unless
      ``skip_heuristic=False``;
    * ``hierarchy-unsafe`` — a method that overrides / is overridden by a
      project method, or whose owning class has an unknown MRO
      (:class:`~pypeeker.analysis.hierarchy.Hierarchy`, conservative);
    * ``protected-public-api`` — library mode and the symbol is
      barrel-exported under an effective public root (published API);
    * ``name-collision`` — the symbol's scope already binds ``_name``;
    * ``pending-collision`` — an earlier entry in this same batch already
      claims ``_name`` in the same scope (duplicate submissions and shadowed
      re-definitions); the first entry wins, later ones skip.

    The pre-filter is a fast, reportable first line — the rename planner
    re-validates every candidate at batch-materialization time, so anything
    that slips through (or goes stale between filtering and planning)
    surfaces as a batch drop, never a broken edit.
    """
    engine = SemanticQueryEngine(store)
    hierarchy: Hierarchy | None = None
    candidates: list[DemoteCandidate] = []
    skipped: list[SkippedSymbol] = []
    pending: dict[tuple[str | None, str], str] = {}

    for submitted_id, confidence in _normalize_entries(symbol_ids):
        if skip_heuristic and _is_heuristic(confidence):
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "heuristic-confidence",
                    "the nominating finding has heuristic confidence "
                    "(dynamic access nearby); excluded from auto-fix",
                )
            )
            continue
        matches = engine.find_symbol(submitted_id)
        if not matches:
            skipped.append(
                SkippedSymbol(submitted_id, "not-found", "symbol not found")
            )
            continue
        if len(matches) > 1:
            ids = sorted(s.symbol_id for s in matches)
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "ambiguous",
                    f"matched {len(matches)} symbols: {', '.join(ids)}",
                )
            )
            continue
        symbol = matches[0]
        if symbol.name == "main" or (
            symbol.name.startswith("__") and symbol.name.endswith("__")
        ):
            # Checked before already-private so dunders report the more
            # precise reason (they also start with an underscore).
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "dunder-or-main",
                    f"'{symbol.name}' has conventional meaning; never demoted",
                )
            )
            continue
        if symbol.name.startswith("_"):
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "already-private",
                    f"'{symbol.name}' already starts with an underscore",
                )
            )
            continue
        if symbol.kind in _METHOD_KINDS:
            if hierarchy is None:
                hierarchy = Hierarchy.from_store(store)
            detail = _hierarchy_detail(hierarchy, symbol)
            if detail is not None:
                skipped.append(
                    SkippedSymbol(submitted_id, "hierarchy-unsafe", detail)
                )
                continue
        barrel_imports = [
            imp
            for imp in engine.find_importers(symbol.symbol_id)
            if imp.location.file_path.endswith("__init__.py")
        ]
        barrel_packages = tuple(
            sorted({module_of(imp.symbol_id) for imp in barrel_imports})
        )
        barrel_inits = tuple(
            sorted({imp.location.file_path for imp in barrel_imports})
        )
        protected_by = _protected_packages(store, barrel_packages)
        if protected_by:
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "protected-public-api",
                    f"barrel-exported by {', '.join(protected_by)} under a "
                    "public root — protected public API (library mode)",
                )
            )
            continue
        new_name = "_" + symbol.name
        if _scope_binds_name(store, symbol, new_name):
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "name-collision",
                    f"the target scope already binds '{new_name}'",
                )
            )
            continue
        pending_key = (symbol.parent_scope_id, new_name)
        winner = pending.get(pending_key)
        if winner is not None:
            skipped.append(
                SkippedSymbol(
                    submitted_id,
                    "pending-collision",
                    f"'{winner}' earlier in this batch already demotes to "
                    f"'{new_name}' in the same scope",
                )
            )
            continue
        pending[pending_key] = symbol.symbol_id
        candidates.append(
            DemoteCandidate(
                symbol_id=symbol.symbol_id,
                name=symbol.name,
                new_name=new_name,
                file_path=symbol.location.file_path,
                include_exports=bool(barrel_packages),
                barrel_packages=barrel_packages,
                barrel_inits=barrel_inits,
                confidence=confidence,
            )
        )
    return candidates, skipped


def demote_intents(candidates: list[DemoteCandidate]) -> list[RenameIntent]:
    """Rename intents (``name -> _name``) for pre-filtered demotion candidates.

    One :class:`~pypeeker.refactor.intents.RenameIntent` per candidate, with
    intent id ``demote:<symbol_id>`` (stable and unique because
    :func:`demote_candidates` deduplicates symbols). ``include_exports``
    mirrors ``plan_demote``'s app-mode handling: barrel-exported candidates
    rewrite the ``__init__`` re-export and its consumers to the private name.
    ``keep_export`` is never set — batch demotion is export-rewrite mode
    only; see the module docstring for why keep-export stays a single-symbol
    CLI decision.
    """
    return [
        RenameIntent(
            f"demote:{candidate.symbol_id}",
            candidate.symbol_id,
            candidate.new_name,
            include_exports=candidate.include_exports,
        )
        for candidate in candidates
    ]


def _rewrite_dunder_all_entry(content: bytes, old: str, new: str) -> bytes | None:
    """``content`` with the ``__all__`` entry ``"old"`` rewritten to ``"new"``.

    Returns ``None`` when there is no top-level literal ``__all__``
    list/tuple assignment, the assignment is unterminated, or no
    single/double-quoted ``old`` entry sits inside it — the same
    literal-assignment limits as ``visibility_ops``'s ``__all__`` handling,
    by design. Only the first occurrence inside the first ``__all__``
    assignment is rewritten (one export, one entry).
    """
    match = _ALL_ASSIGNMENT_RE.search(content)
    if match is None:
        return None
    open_bracket = match.end() - 1
    close = b"]" if content[open_bracket:open_bracket + 1] == b"[" else b")"
    close_at = content.find(close, open_bracket + 1)
    if close_at < 0:
        return None  # unterminated — leave __all__ alone
    body = content[open_bracket + 1:close_at]
    for quote in (b'"', b"'"):
        entry = quote + old.encode("utf-8") + quote
        at = body.find(entry)
        if at >= 0:
            start = open_bracket + 1 + at
            replacement = quote + new.encode("utf-8") + quote
            return content[:start] + replacement + content[start + len(entry):]
    return None


def _rewrite_barrel_all_entries(
    mirror_root: Path,
    executed: list[ExecutedDemotion],
    candidates: list[DemoteCandidate],
) -> None:
    """Rewrite stale ``__all__`` entries in the mirror after the batch ran.

    The rename engine rewrites *references* (imports, call sites, barrel
    re-export lines) but not string literals, so a barrel ``__init__`` —
    or the defining module itself — listing the demoted name in ``__all__``
    would go stale. For every executed demotion this rewrites the
    ``"name"`` entry to ``"_name"`` in the candidate's barrel ``__init__``
    files and its defining file, consistent with export-rewrite mode: the
    import line now binds the private name, so the ``__all__`` entry follows
    it (star-import consumers keep working). Mutating the mirror *before*
    flattening is what folds these edits into the same single transaction.
    """
    by_intent = {f"demote:{c.symbol_id}": c for c in candidates}
    for done in executed:
        candidate = by_intent.get(done.intent_id)
        if candidate is None:  # pragma: no cover — ids are built from candidates
            continue
        for path in dict.fromkeys((*candidate.barrel_inits, candidate.file_path)):
            target = mirror_root / path
            if not target.is_file():
                continue
            rewritten = _rewrite_dunder_all_entry(
                target.read_bytes(), candidate.name, candidate.new_name
            )
            if rewritten is not None:
                target.write_bytes(rewritten)


def _barrel_warnings(executed: list[ExecutedDemotion],
                     candidates: list[DemoteCandidate]) -> list[str]:
    """Public-surface warnings for executed barrel-exported demotions."""
    by_id = {candidate.symbol_id: candidate for candidate in candidates}
    warnings: list[str] = []
    for done in executed:
        candidate = by_id.get(done.symbol_id)
        if candidate is None or not candidate.barrel_packages:
            continue
        warnings.append(
            f"'{candidate.name}' is barrel-exported by "
            f"{', '.join(candidate.barrel_packages)}; the export and its "
            f"consumers were rewritten to '{candidate.new_name}' — the "
            "public API surface changed."
        )
    return warnings


def plan_privatize(
    store: IndexStore,
    transaction_store: TransactionStore,
    symbol_ids: list[CandidateEntry],
    *,
    skip_heuristic: bool = True,
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT,
    work_dir: Path | None = None,
) -> PrivatizeOutcome:
    """Plan a batch demotion of ``symbol_ids`` as ONE flattened transaction.

    The composition TASK-97 should reuse: :func:`demote_candidates` filters,
    :func:`demote_intents` lifts to rename intents, then — the ``plan-batch``
    CLI's conventions exactly — :func:`~pypeeker.refactor.batch.run_batch`
    simulates the intents against a temporary mirror of the project (each
    demotion re-plans against the state earlier ones left, so collisions and
    ordering are the batch machinery's problem, not ours), stale ``__all__``
    entries naming a demoted symbol are rewritten in the mirror (see
    :func:`_rewrite_barrel_all_entries`), and
    :func:`~pypeeker.refactor.batch.flatten_batch` diffs the mirror into one
    transaction, persisted with operation ``"privatize"``. The real tree is
    never written; ``apply`` / ``rollback`` execute the result unchanged.

    ``work_dir`` overrides the mirror directory (a fresh temp dir
    otherwise); either way it is deleted before returning — the flattened
    transaction is the only durable output. Under
    :attr:`~pypeeker.refactor.batch.BatchPolicy.ALL_OR_NOTHING`,
    :class:`~pypeeker.refactor.batch.BatchAborted` propagates (pre-filter
    skips are *not* aborts: they are reportable exclusions by design — only
    batch-execution drops trigger the policy).

    Returns a :class:`PrivatizeOutcome`; ``summary`` is ``None`` when no
    candidate survived the pre-filter, every intent dropped, or the batch
    was a net no-op.
    """
    candidates, skipped = demote_candidates(
        store, symbol_ids, skip_heuristic=skip_heuristic
    )
    if not candidates:
        return PrivatizeOutcome(summary=None, skipped=skipped)

    intents = demote_intents(candidates)
    mirror_dir = (
        Path(tempfile.mkdtemp(prefix="pypeeker-privatize-"))
        if work_dir is None
        else work_dir
    )
    try:
        result = run_batch(intents, store, policy=policy, work_dir=mirror_dir)
        executed = [
            ExecutedDemotion(
                intent_id=done.intent.intent_id,
                symbol_id=done.intent.symbol_id,
                new_name=done.intent.new_name,
            )
            for done in result.executed
            if isinstance(done.intent, RenameIntent)
        ]
        # Fold stale __all__ entries into the mirror before flattening so
        # they land in the same single transaction (see the helper).
        _rewrite_barrel_all_entries(result.root, executed, candidates)
        header, edits = flatten_batch(result, store)
    finally:
        shutil.rmtree(mirror_dir, ignore_errors=True)
    summary: TransactionSummary | None = None
    if edits:
        header.operation = PRIVATIZE_OPERATION
        transaction_store.save(header, edits)
        summary = TransactionSummary(
            tx_id=header.tx_id,
            operation=PRIVATIZE_OPERATION,
            symbol_id="",
            old_name="",
            new_name="",
            edit_count=len(edits),
            created_at=header.created_at,
            files_affected=sorted({edit.file for edit in edits}),
        )
    return PrivatizeOutcome(
        summary=summary,
        executed=executed,
        dropped=result.dropped,
        skipped=skipped,
        warnings=_barrel_warnings(executed, candidates),
    )


__all__ = [
    "PRIVATIZE_OPERATION",
    "CandidateEntry",
    "DemoteCandidate",
    "SkippedSymbol",
    "ExecutedDemotion",
    "PrivatizeOutcome",
    "demote_candidates",
    "demote_intents",
    "plan_privatize",
]
