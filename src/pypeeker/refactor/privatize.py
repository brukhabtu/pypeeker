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

Two layers, composed by :func:`plan_privatize` — the module's one public entry
point, alongside the :class:`PrivatizeOutcome` report it returns:

* :func:`_demote_candidates` — pre-filter the submitted symbol ids into
  :class:`_DemoteCandidate` / :class:`SkippedSymbol` with machine-readable
  skip reasons (unresolvable or ambiguous ids, hierarchy-unsafe methods,
  library-mode published API, ``_name`` collisions — including collisions
  *among* the pending batch).
* :func:`plan_privatize` — run the caller's intents as a simulated batch on an
  in-memory overlay, flatten the net change, persist it, and report what
  executed / dropped / was skipped.

**What this module does not decide.** Whether a symbol *deserves* demoting is
:data:`pypeeker.dsl.DEMOTE`'s question, not this one's: the confidence floor
and the two pointwise preconditions (a name that is already private, a dunder
or ``main``) refuse those rows before an intent exists. What arrives here is a
:class:`~pypeeker.intents.intents.ChangeVisibilityIntent` per surviving row,
and every skip reason left in this module is a fact about the *batch* or the
*project* rather than about the row. Export handling is likewise not decided
here: a ``change-visibility`` intent lowers to a rename with
``include_exports`` derived by
:meth:`~pypeeker.refactor.visibility_ops.VisibilityPlanner.plan_demote` from
the symbol's actual barrel exports, so this module records barrel exposure
(for the ``__all__`` rewrite and the public-surface warning) rather than
deciding on it.

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

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pypeeker.analysis import Hierarchy
from pypeeker.intents import ChangeVisibilityIntent
from pypeeker.models import Symbol, SymbolKind, TransactionSummary, module_of
from pypeeker.paths import is_barrel_path
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor.batch import (
    BatchPolicy,
    DroppedIntent,
    flatten_batch,
    run_batch,
)
from pypeeker.refactor.plan_support import method_override_conflicts
from pypeeker.refactor.visibility_ops import (
    dunder_all_literal_span,
    protected_packages,
)
from pypeeker.storage import IndexStore, OverlayIndexStore, TransactionStore

PRIVATIZE_OPERATION = "privatize"
"""The ``operation`` stamped on the flattened transaction header."""

_METHOD_KINDS = (SymbolKind.METHOD, SymbolKind.PROPERTY)
"""Symbol kinds whose demotion must clear the class-hierarchy safety check."""


@dataclass(frozen=True)
class _DemoteCandidate:
    """A symbol that passed every pre-filter and is safe to plan for demotion.

    ``symbol_id`` is the fully resolved id (even when the caller submitted a
    shorthand); ``new_name`` is always ``"_" + name``. ``include_exports``
    and ``barrel_packages`` mirror ``plan_demote``'s app-mode export
    handling: when the symbol is barrel-exported, the batch rename rewrites
    the ``__init__`` re-export and its consumers to the private name (the
    packages are recorded so callers can warn that the public surface
    changed). ``barrel_inits`` holds those ``__init__.py`` file paths —
    :func:`plan_privatize` rewrites any stale ``__all__`` entries there.

    ``submitted_id`` is the id *as submitted*, before
    :meth:`~pypeeker.query.SemanticQueryEngine.find_symbol` normalized it.
    :func:`plan_privatize` pairs candidates back to the intents it was handed
    by this field, never by ``symbol_id``: resolution matches an id, a tail
    *or* a bare name, so the resolved id is not always the key the caller
    submitted under.
    """

    symbol_id: str
    name: str
    new_name: str
    file_path: str
    include_exports: bool = False
    barrel_packages: tuple[str, ...] = ()
    barrel_inits: tuple[str, ...] = ()
    submitted_id: str = ""


@dataclass(frozen=True)
class SkippedSymbol:
    """A submitted symbol the pre-filter excluded, with a stable reason code.

    ``reason`` is machine-readable; ``detail`` is the human-readable
    explanation; ``symbol_id`` is the id *as submitted*, so reports map back to
    the caller's input.

    :func:`_demote_candidates` produces six of the codes — ``not-found``,
    ``ambiguous``, ``hierarchy-unsafe``, ``protected-public-api``,
    ``name-collision`` and ``pending-collision``. The remaining three that the
    ``privatize`` report can carry — ``heuristic-confidence``,
    ``dunder-or-main`` and ``already-private`` — are pointwise refusals by
    :data:`pypeeker.dsl.DEMOTE`, which
    :mod:`pypeeker.app.privatize` translates into this shape so the CLI's
    ``skipped`` list stays one vocabulary.
    """

    symbol_id: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class _ExecutedDemotion:
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
    mirrors the ``batch`` CLI's ``{tx_id, executed, dropped, ...}``
    report plus the pre-filter column that command does not have.
    """

    summary: TransactionSummary | None
    executed: list[_ExecutedDemotion] = field(default_factory=list)
    dropped: tuple[DroppedIntent, ...] = ()
    skipped: list[SkippedSymbol] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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

    The same question the rename planner's ``method-override-safe``
    precondition asks (:func:`~pypeeker.refactor.plan_support.method_override_conflicts`),
    worded as a skip detail: an override pair, or a class whose base chain
    is incomplete (external/dynamic bases) and so *may* override something
    the index cannot see — the batch pre-filter skips it rather than
    half-renaming an override pair.
    """
    overrides, overridden_by, unknown_owner = method_override_conflicts(
        hierarchy, symbol
    )
    if overrides:
        return f"overrides {', '.join(sorted(overrides))}"
    if overridden_by:
        return f"overridden by {', '.join(sorted(overridden_by))}"
    if unknown_owner is not None:
        return (
            f"owning class '{unknown_owner}' has an incomplete base chain "
            "(mro unknown) — an unseen override may exist"
        )
    return None


def _demote_candidates(
    store: IndexStore,
    symbol_ids: Sequence[str],
) -> tuple[list[_DemoteCandidate], list[SkippedSymbol]]:
    """Pre-filter symbols nominated for demotion into candidates and skips.

    Entries are processed in input order; the returned lists preserve it,
    which is what makes the pending-collision rule deterministic.

    Skip reasons (stable codes on :class:`SkippedSymbol`):

    * ``not-found`` / ``ambiguous`` — the id resolved to zero / multiple
      symbols;
    * ``hierarchy-unsafe`` — a method that overrides / is overridden by a
      project method, or whose owning class has an unknown MRO
      (:class:`~pypeeker.analysis.hierarchy.Hierarchy`, conservative);
    * ``protected-public-api`` — library mode and the symbol is
      barrel-exported under an effective public root (published API);
    * ``name-collision`` — the symbol's scope already binds ``_name``;
    * ``pending-collision`` — an earlier entry in this same batch already
      claims ``_name`` in the same scope (duplicate submissions and shadowed
      re-definitions); the first entry wins, later ones skip.

    Every reason left here is a fact about the *batch* or the *project*. The
    three that were pointwise properties of the row itself —
    ``heuristic-confidence``, ``dunder-or-main`` and ``already-private`` — are
    now :data:`pypeeker.dsl.DEMOTE`'s confidence floor and two preconditions,
    which refuse those rows before an intent exists; asking them again here
    would report one answer under two vocabularies.

    The pre-filter is a fast, reportable first line — the rename planner
    re-validates every candidate at batch-materialization time, so anything
    that slips through (or goes stale between filtering and planning)
    surfaces as a batch drop, never a broken edit.
    """
    engine = SemanticQueryEngine(store)
    hierarchy: Hierarchy | None = None
    candidates: list[_DemoteCandidate] = []
    skipped: list[SkippedSymbol] = []
    pending: dict[tuple[str | None, str], str] = {}

    for submitted_id in symbol_ids:
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
        if symbol.kind in _METHOD_KINDS:
            if hierarchy is None:
                hierarchy = Hierarchy.from_store(store, engine=engine)
            detail = _hierarchy_detail(hierarchy, symbol)
            if detail is not None:
                skipped.append(
                    SkippedSymbol(submitted_id, "hierarchy-unsafe", detail)
                )
                continue
        barrel_imports = [
            imp
            for imp in engine.find_importers(symbol.symbol_id)
            if is_barrel_path(imp.location.file_path)
        ]
        barrel_packages = tuple(
            sorted({module_of(imp.symbol_id) for imp in barrel_imports})
        )
        barrel_inits = tuple(
            sorted({imp.location.file_path for imp in barrel_imports})
        )
        protected_by = protected_packages(store, barrel_packages)
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
            _DemoteCandidate(
                symbol_id=symbol.symbol_id,
                name=symbol.name,
                new_name=new_name,
                file_path=symbol.location.file_path,
                include_exports=bool(barrel_packages),
                barrel_packages=barrel_packages,
                barrel_inits=barrel_inits,
                submitted_id=submitted_id,
            )
        )
    return candidates, skipped


def _rewrite_dunder_all_entry(content: bytes, old: str, new: str) -> bytes | None:
    """``content`` with the ``__all__`` entry ``"old"`` rewritten to ``"new"``.

    Returns ``None`` when there is no top-level literal ``__all__``
    list/tuple assignment, the assignment is unterminated, or no
    single/double-quoted ``old`` entry sits inside it — the literal is
    located by :func:`~pypeeker.refactor.visibility_ops.dunder_all_literal_span`,
    the same limits as promote's ``__all__`` insert, by design. Only the
    first occurrence inside the first ``__all__`` assignment is rewritten
    (one export, one entry).
    """
    span = dunder_all_literal_span(content)
    if span is None:
        return None
    open_bracket, close_at = span
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
    sim_store: OverlayIndexStore,
    executed: list[_ExecutedDemotion],
    candidates: list[_DemoteCandidate],
    *,
    by_intent: Mapping[str, _DemoteCandidate],
) -> None:
    """Rewrite stale ``__all__`` entries in the simulation after the batch ran.

    The rename engine rewrites *references* (imports, call sites, barrel
    re-export lines) but not string literals, so a barrel ``__init__`` —
    or the defining module itself — listing the demoted name in ``__all__``
    would go stale. For every executed demotion this rewrites the
    ``"name"`` entry to ``"_name"`` in the candidate's barrel ``__init__``
    files and its defining file, consistent with export-rewrite mode: the
    import line now binds the private name, so the ``__all__`` entry follows
    it (star-import consumers keep working). Mutating the *simulation*
    before flattening is what folds these edits into the same single
    transaction.

    Reads and writes go through the overlay's file-bytes layer, never
    ``project_root / path``: under an overlay that root is the user's real
    tree, and this runs between :func:`~pypeeker.refactor.batch.run_batch`
    and :func:`~pypeeker.refactor.batch.flatten_batch`, i.e. while the batch
    is still a plan. Deliberately no re-bind — ``flatten_batch`` only reads
    bytes, and no further planner runs against this state.

    ``by_intent`` maps intent id to candidate, and is supplied by the caller
    rather than derived here: a DSL demote intent's id is fork #5's derived
    ``<origin>:demote:<symbol_id>``, so the id is the origin rule's to spell,
    not this module's.
    """
    for done in executed:
        candidate = by_intent.get(done.intent_id)
        if candidate is None:  # pragma: no cover — ids are built from candidates
            continue
        for path in dict.fromkeys((*candidate.barrel_inits, candidate.file_path)):
            if not sim_store.file_exists(path):
                continue
            rewritten = _rewrite_dunder_all_entry(
                sim_store.read_file(path), candidate.name, candidate.new_name
            )
            if rewritten is not None:
                sim_store.write_file(path, rewritten)


def _barrel_warnings(executed: list[_ExecutedDemotion],
                     candidates: list[_DemoteCandidate]) -> list[str]:
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
    intents: Sequence[ChangeVisibilityIntent],
    *,
    policy: BatchPolicy = BatchPolicy.SKIP_AND_REPORT,
) -> PrivatizeOutcome:
    """Plan a batch demotion as ONE flattened transaction.

    The caller has already decided *which* symbols to demote: a row that clears
    :data:`pypeeker.dsl.DEMOTE`'s floor and its two preconditions arrives here
    as a :class:`~pypeeker.intents.intents.ChangeVisibilityIntent`, already
    carrying its symbol id and its ``direction``. So this entry point does not
    nominate, does not read a confidence and does not compute ``"_" + name``.
    It runs the five pre-filter questions the mutation cannot answer pointwise
    (``not-found``, ``ambiguous``, ``hierarchy-unsafe``,
    ``protected-public-api``, ``name-collision``) plus the batch-wide
    ``pending-collision`` dedupe, then — the ``batch`` CLI's conventions
    exactly — :func:`~pypeeker.refactor.batch.run_batch` simulates the intents
    on an in-memory overlay over the project (each demotion re-plans against
    the state earlier ones left, so collisions and ordering are the batch
    machinery's problem), stale ``__all__`` entries naming a demoted symbol are
    rewritten in the simulation (see :func:`_rewrite_barrel_all_entries`), and
    :func:`~pypeeker.refactor.batch.flatten_batch` diffs the simulation into
    one transaction, persisted with operation ``"privatize"``. The real tree is
    never written; ``apply`` / ``rollback`` execute the result unchanged.

    The only temp directory is a scratch
    :class:`~pypeeker.storage.TransactionStore` the simulated re-plans persist
    into, discarded before returning — the flattened transaction is the only
    durable output. Under
    :attr:`~pypeeker.refactor.batch.BatchPolicy.ALL_OR_NOTHING`,
    :class:`~pypeeker.refactor.batch.BatchAborted` propagates (pre-filter skips
    are *not* aborts: they are reportable exclusions by design — only
    batch-execution drops trigger the policy).

    Duplicate nomination is the normal case, not an edge case: one unreferenced
    public function is nominated by both ``over-exposed-module-symbol`` and
    ``unused-public-symbol``, so two intents arrive for one symbol. The first
    wins and the second is reported as ``pending-collision``, because the
    pre-filter sees every submission in order rather than a de-duplicated set.

    Intents are paired back to candidates by
    :attr:`_DemoteCandidate.submitted_id`, popped in submission order, so
    duplicates pair with the right occurrence even though
    :meth:`~pypeeker.query.SemanticQueryEngine.find_symbol` may normalize the
    id. A pairing miss is a bug in this function, not a user error, and raises
    rather than silently dropping a repair.

    Returns a :class:`PrivatizeOutcome`; ``summary`` is ``None`` when no
    candidate survived the pre-filter, every intent dropped, or the batch was a
    net no-op.

    Raises:
        ValueError: when a surviving candidate names a symbol the mutation's
            preconditions should have refused (a ``_``-prefixed name, a
            dunder, or ``main``) — nothing else here would stop ``_foo`` from
            being planned as ``__foo``; or when a candidate cannot be paired
            back to a submitted intent.
    """
    candidates, skipped = _demote_candidates(
        store, [intent.symbol_id for intent in intents]
    )
    for candidate in candidates:
        if candidate.name.startswith("_") or candidate.name == "main":
            raise ValueError(
                f"plan_privatize was handed '{candidate.submitted_id}', "
                f"whose name '{candidate.name}' the demote mutation's "
                "preconditions should have refused; demoting it would mangle "
                "the name. Refusing rather than planning "
                f"'_{candidate.name}'."
            )
    if not candidates:
        return PrivatizeOutcome(summary=None, skipped=skipped)

    by_submitted: dict[str, list[ChangeVisibilityIntent]] = {}
    for intent in intents:
        by_submitted.setdefault(intent.symbol_id, []).append(intent)
    paired: list[tuple[ChangeVisibilityIntent, _DemoteCandidate]] = []
    for candidate in candidates:
        queued = by_submitted.get(candidate.submitted_id)
        if not queued:
            raise ValueError(
                f"no submitted intent for candidate '{candidate.submitted_id}' "
                f"(resolved to '{candidate.symbol_id}') — the pre-filter and "
                "the intent list disagree"
            )
        paired.append((queued.pop(0), candidate))

    with tempfile.TemporaryDirectory(prefix="pypeeker-privatize-") as scratch:
        result = run_batch(
            [intent for intent, _ in paired],
            store,
            tx_store=TransactionStore(Path(scratch)),
            policy=policy,
        )
    by_intent = {intent.intent_id: candidate for intent, candidate in paired}
    executed = [
        _ExecutedDemotion(
            intent_id=done.intent.intent_id,
            symbol_id=by_intent[done.intent.intent_id].symbol_id,
            new_name=by_intent[done.intent.intent_id].new_name,
        )
        for done in result.executed
        if isinstance(done.intent, ChangeVisibilityIntent)
        and done.intent.intent_id in by_intent
    ]
    ordered = [candidate for _, candidate in paired]
    _rewrite_barrel_all_entries(result.store, executed, ordered, by_intent=by_intent)
    flat = flatten_batch(result, store)
    header, edits = flat.header, flat.edits
    summary: TransactionSummary | None = None
    if edits:
        header.operation = PRIVATIZE_OPERATION
        transaction_store.save(
            header, edits, creates=flat.creates, deletes=flat.deletes
        )
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
        warnings=_barrel_warnings(executed, ordered),
    )


__all__ = [
    "PRIVATIZE_OPERATION",
    "PrivatizeOutcome",
    "SkippedSymbol",
    "plan_privatize",
]
