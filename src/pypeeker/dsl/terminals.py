"""Mutation terminals: the write half of the DSL, as named top-level values.

Phase 4 of the rewrite recorded in ``dsl-rewrite.md``. The read half answers
*which rows fire*; this module answers *what repair one of those rows names*.
A repair is an :class:`~pypeeker.intents.Intent` and nothing more — the
planner registered for its ``kind`` in ``refactor`` is the only code in the
system that writes bytes, and this package must never import it.

Three forks meet here, and each is settled structurally rather than by
convention:

* **Fork #2 — the confidence floor is an attribute of the mutation value.**
  :attr:`Mutation.floor` is read in exactly one place,
  :meth:`Mutation.decide`, and there is no floor parameter on the application
  operator, on a rule, or on any consumer. To give one operation two floors you
  would have to build two :class:`Mutation` objects; the historical
  demote/privatize drift was exactly that — two implementations of one
  operation, with two floors *and* two intent kinds. One operation is one
  mutation value is one intent kind, and :mod:`pypeeker.dsl.demotion` is the
  proof: two selections, one ``DEMOTE``.

* **Fork #5 — ``intent_id`` is purely derived, with no override.** It is
  ``<origin>:<mutation-name>:<anchor-id>``, computed in one place, and
  ``intent_id`` appearing in a mutation's ``params`` is a construction-time
  refusal rather than a supported escape.

* **Fork #10 — a mutation may only name a planner kind that exists.**
  :data:`PLANNED_KINDS` is the closed set, and :class:`Mutation` refuses
  anything outside it *at construction*. Minting a kind before its planner
  exists is vaporware, so the v1 answer to "can I compose two mutations" is
  the refusal, delivered where the mistake is.

**Why this module is not called ``mutations.py``.**
:mod:`pypeeker.dsl.mutation` already exists and is the *rule* module for
``no-argument-mutation`` and ``no-hidden-global-mutation`` — expressions over
the references universe that detect mutation of arguments and of globals. That
is a different sense of the word, and two modules a letter apart meaning
opposite things (one detects, one repairs) would make the package unreadable.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pypeeker.dsl.errors import MutationPreconditionError, UnplannableMutationError
from pypeeker.dsl.evidence import CONFIDENCE_RANK
from pypeeker.dsl.expr import (
    UNMATCHED,
    EvalContext,
    Expr,
    Opaque,
    _field_reads,
    all_of,
    any_of,
    not_,
    row,
)
from pypeeker.dsl.facts import fact_specs
from pypeeker.dsl.match import Match
from pypeeker.dsl.reach import Reach
from pypeeker.dsl.universes import UNIVERSE_NAMES, universe_fields
from pypeeker.intents import (
    ChangeVisibilityIntent,
    DeleteSymbolIntent,
    Intent,
    RemoveImportIntent,
    RenameDocstringParamIntent,
    RewriteStarImportIntent,
    TuplifyIntent,
)
from pypeeker.models import Confidence, Visibility

PLANNED_KINDS: frozenset[str] = frozenset({
    "change-visibility",
    "delete-symbol",
    "extract-method",
    "extract-variable",
    "inline-variable",
    "move-symbol",
    "remove-import",
    "rename",
    "rename-docstring-param",
    "replace-text",
    "rewrite-star-import",
    "tuplify",
})
"""Every transform kind a registered planner can execute — fork #10's closed set.

A **literal copy** of the keys ``pypeeker.refactor.registry`` holds, written
out rather than read, because ``dsl`` may not import ``refactor``: that
boundary is what keeps planners on the far side of the wall and stops the new
engine from executing old-engine code. A literal copy can rot, so it is kept
honest by ``tests/test_dsl_terminals.py``, which imports both and asserts set
equality in **both** directions — the one place in the tree where ``dsl`` and
``refactor`` meet, and the only thing standing between fork #10 and a mutation
naming a kind nobody can plan.
"""


@dataclass(frozen=True)
class FromRow:
    """An intent parameter taken from a named field of the row.

    The field must be visible where the mutation is applied; both application
    sites validate that at construction rather than reading a missing field as
    ``None`` and handing it to an intent constructor.
    """

    field: str


@dataclass(frozen=True)
class FromAnchor:
    """An intent parameter taken from the row's anchor id.

    Distinct from ``FromRow("symbol_id")`` on purpose: a row source may anchor
    on something finer than its own ``symbol_id`` field (``drift_rows`` anchors
    per ``<function>:<param>``), and a mutation that means "the thing this
    finding is about" should say so rather than name a field that happens to
    agree today.
    """


@dataclass(frozen=True)
class Precondition:
    """A pointwise guard on one row, and the reason code reported when it fails.

    ``name`` is a stable hyphenated code, chosen to match the skip reasons the
    frozen workflows already report (``already-private``, ``dunder-or-main``),
    so a consumer reconstructing those reports reads them off the decision
    rather than re-deriving them.

    ``expr`` is evaluated against the row's fields **and nothing else** — no
    corpus, no trait provider, no fact table. :class:`Mutation` enforces that at
    construction: an expression reaching further would read ``None`` at
    ``UNKNOWN`` here and reject every row silently, which is the same
    silent-empty class fork #12 exists to kill.
    """

    name: str
    expr: Expr


@dataclass(frozen=True)
class MutationDecision:
    """What a mutation decided about one row: an intent, or why there is none.

    Fork #12's discipline applied to the write half. An application that only
    yielded intents would answer "no repair" and "no rows" with the same empty
    tuple, and the frozen ``privatize`` workflow's ``skipped`` report — which
    names ``heuristic-confidence``, ``already-private``, ``dunder-or-main`` per
    submitted id — could not be reconstructed from it. So the decision carries
    the row it was made about and the reason it went the way it did.

    ``reason`` is ``""`` exactly when ``intent`` is not ``None``; otherwise it
    is :data:`BELOW_FLOOR` or the failing :class:`Precondition`'s name.
    """

    match: Match
    intent: Intent | None
    reason: str


BELOW_FLOOR = "below-floor"
"""Reason code for a row whose evidence does not reach its mutation's floor."""


_KNOWN_FIELDS: frozenset[str] = frozenset().union(
    *(frozenset(universe_fields(name)) for name in UNIVERSE_NAMES)
)
"""Every field name any universe declares — the vocabulary an opaque is read against.

:func:`~pypeeker.dsl.expr._field_reads` deliberately ignores an opaque's
``reads=`` tokens, because they are free-form: an honest
``reads=("the symbol's own name",)`` must not become an authoring error just
because no universe declares that string. But an opaque *precondition* whose
declared read is a real field name is the silent-failure case — the body reads
``record.name``, the selection does not expose ``name``, the read is ``None``,
the guard goes false, and the repair vanishes. Intersecting with this set
validates the tokens that can fail that way and leaves the prose ones alone.
"""


def _opaque_field_reads(expr: Expr) -> frozenset[str]:
    """An expression's opaque-declared reads that name a real universe field."""
    found: set[str] = set()
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, Opaque):
            found.update(token for token in node.reads if token in _KNOWN_FIELDS)
        stack.extend(node.children)
    return frozenset(found)


def _required_params(intent: type[Intent]) -> frozenset[str]:
    """The intent's constructor keywords that have no default, minus ``intent_id``.

    ``intent_id`` is excluded because :meth:`Mutation.intent_id` supplies it —
    fork #5 derives it and forbids an override, so a mutation naming it is a
    different error, raised above this one.
    """
    return frozenset(
        f.name
        for f in dataclasses.fields(intent)
        if f.name != "intent_id"
        and f.default is dataclasses.MISSING
        and f.default_factory is dataclasses.MISSING
    )


def _concrete_intent(intent: Any) -> None:
    """Refuse anything that is not a concrete :class:`~pypeeker.intents.Intent` class."""
    if not (isinstance(intent, type) and issubclass(intent, Intent)):
        raise UnplannableMutationError(
            f"a mutation's intent must be a concrete pypeeker.intents.Intent "
            f"subclass, got {intent!r}",
            kind=str(intent),
            valid=PLANNED_KINDS,
        )
    if inspect.isabstract(intent):
        raise UnplannableMutationError(
            f"{intent.__name__} is abstract, so no planner can execute it",
            kind=intent.__name__,
            valid=PLANNED_KINDS,
        )


@dataclass(frozen=True)
class Mutation:
    """A named repair: one intent kind, its parameters, its guards, its floor.

    ``name`` is the middle segment of the derived ``intent_id``
    (``<origin>:<name>:<anchor>``), so it is part of a user-visible contract:
    renaming a mutation renames every fix id it produces.

    ``intent`` is the concrete :class:`~pypeeker.intents.Intent` subclass to
    build. Its ``kind`` must be in :data:`PLANNED_KINDS` (fork #10).

    ``params`` maps intent keyword to a source: :class:`FromRow`,
    :class:`FromAnchor`, or any other value, which is passed through as a
    literal. ``intent_id`` is not a legal key — fork #5 derives it and offers no
    override.

    ``floor`` is the confidence floor, **an attribute of this value** (fork #2).
    A row whose evidence is weaker produces no intent at all. That is stronger
    than the frozen arrangement, where a rule attaches a remedy and a consumer
    later discards it: deciding once, here, is what makes a second floor
    unwritable.

    ``preconditions`` are pointwise guards, evaluated in written order, over the
    row this mutation would act on. They live on the shared value rather than on
    each application site for the same reason the floor does — ``already-private``
    is a property of the demote *operation*, and two sites each restating it is
    how the demote/privatize drift happened in the first place.
    """

    name: str
    intent: type[Intent]
    params: Mapping[str, Any]
    floor: Confidence
    preconditions: tuple[Precondition, ...] = ()

    def __post_init__(self) -> None:
        """Refuse an unplannable kind, an illegal param table, or a non-pointwise guard.

        Raises:
            UnplannableMutationError: ``intent`` is not a concrete
                :class:`~pypeeker.intents.Intent`; its ``kind`` has no
                registered planner (fork #10); ``params`` carries ``intent_id``
                (fork #5); or ``params`` names a keyword the intent does not
                declare.
            MutationPreconditionError: a precondition reaches beyond the row —
                ``PROJECT`` reach, a trait read, or a primitive-fact read.
        """
        _concrete_intent(self.intent)
        if self.intent.kind not in PLANNED_KINDS:
            raise UnplannableMutationError(
                f"mutation {self.name!r} names intent kind {self.intent.kind!r}, "
                f"which no registered planner can execute. Fork #10 of "
                f"dsl-rewrite.md: a v1 mutation resolves to an existing planner "
                f"kind, and minting a kind before its planner exists is "
                f"vaporware. Planner kinds are: "
                f"{', '.join(sorted(PLANNED_KINDS))}.",
                kind=self.intent.kind,
                valid=PLANNED_KINDS,
            )
        declared = {
            f.name for f in dataclasses.fields(self.intent)
        } - {"intent_id"}
        if "intent_id" in self.params:
            raise UnplannableMutationError(
                f"mutation {self.name!r} sets 'intent_id' in params. Fork #5 "
                f"derives it as '<origin>:{self.name}:<anchor>' and offers no "
                f"override — the override existed to preserve contracts nobody "
                f"consumes.",
                kind=self.intent.kind,
                valid=sorted(declared),
            )
        for key in self.params:
            if key not in declared:
                raise UnplannableMutationError(
                    f"mutation {self.name!r} passes {key!r}, which "
                    f"{self.intent.__name__} does not declare; its parameters "
                    f"are: {', '.join(sorted(declared))}.",
                    kind=self.intent.kind,
                    valid=sorted(declared),
                )
        missing = sorted(_required_params(self.intent) - set(self.params))
        if missing:
            raise UnplannableMutationError(
                f"mutation {self.name!r} omits {', '.join(repr(k) for k in missing)}, "
                f"which {self.intent.__name__} requires. A mutation is a module-level "
                f"value whose contract is that construction proves it can be built; "
                f"an omitted parameter is otherwise a TypeError on the first row that "
                f"reaches it, at which point the value has already been imported and "
                f"published.",
                kind=self.intent.kind,
                valid=sorted(declared),
            )
        for guard in self.preconditions:
            self._validate_precondition(guard)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))

    def _validate_precondition(self, guard: Precondition) -> None:
        """Refuse a guard that cannot be answered from the row's fields alone."""
        if guard.expr.reach is not Reach.FILE:
            raise MutationPreconditionError(
                f"precondition {guard.name!r} of mutation {self.name!r} reaches "
                f"project, but a precondition is evaluated pointwise against one "
                f"row's fields with no corpus in scope. Move the clause into the "
                f"selection, where it has one.",
                mutation=self.name,
                detail=f"reach={guard.expr.reach.value}",
            )
        if guard.expr.trait_names:
            raise MutationPreconditionError(
                f"precondition {guard.name!r} of mutation {self.name!r} reads the "
                f"traits {', '.join(sorted(guard.expr.trait_names))}, which need a "
                f"FileIndex the terminal does not have. Move the clause into the "
                f"selection.",
                mutation=self.name,
                detail="trait-read",
            )
        if fact_specs(guard.expr):
            raise MutationPreconditionError(
                f"precondition {guard.name!r} of mutation {self.name!r} reads the "
                f"primitive facts "
                f"{', '.join(sorted(spec.name for spec in fact_specs(guard.expr)))}, "
                f"whose tables are built per selection run. Move the clause into "
                f"the selection.",
                mutation=self.name,
                detail="fact-read",
            )

    @property
    def field_reads(self) -> frozenset[str]:
        """Every row field this mutation reads — params and preconditions alike.

        What an application site validates against the fields a selection
        actually exposes. Without it a mismatch is invisible: a missing field
        reads as ``None`` at ``UNKNOWN``, the guard goes false, and the repair
        disappears with nothing raising.
        """
        params = frozenset(
            source.field for source in self.params.values() if isinstance(source, FromRow)
        )
        guards = frozenset().union(
            frozenset(), *(_field_reads(guard.expr) for guard in self.preconditions)
        )
        declared = frozenset().union(
            frozenset(), *(_opaque_field_reads(guard.expr) for guard in self.preconditions)
        )
        return params | guards | declared

    def intent_id(self, origin: str, anchor_id: str) -> str:
        """The derived id: ``<origin>:<name>:<anchor>``. Fork #5, one place.

        ``origin`` is the rule id when a rule attaches this repair, so the three
        frozen check remedies come out character for character
        (``unused-imports:remove:<id>``, ``star-imports:rewrite:<id>``,
        ``docstring-drift:rename-param:<id>:<ghost>``), and a workflow name when
        a CLI-typed anchor drives it.
        """
        return f"{origin}:{self.name}:{anchor_id}"

    def decide(self, origin: str, match: Match) -> MutationDecision:
        """Decide this mutation's repair for one row: an intent, or a reason.

        The floor is checked first, then each precondition in written order, so
        the reported reason is the *first* thing that stopped the repair — which
        is what makes ``dunder-or-main`` more precise than ``already-private``
        rather than merely different, matching the frozen pre-filter's own
        ordering comment.

        Args:
            origin: the rule id or workflow name the derived id starts with.
            match: the row, carrying its anchor, its fields and the meet of
                every contribution that got it here.

        Returns:
            A :class:`MutationDecision`. Its ``intent`` is ``None`` exactly when
            ``reason`` is non-empty.
        """
        if CONFIDENCE_RANK[match.confidence] < CONFIDENCE_RANK[self.floor]:
            return MutationDecision(match=match, intent=None, reason=BELOW_FLOOR)
        context = EvalContext(fields=match.fields)
        for guard in self.preconditions:
            node = guard.expr.evaluate(context)
            if node.value is UNMATCHED or not node.value:
                return MutationDecision(match=match, intent=None, reason=guard.name)
        resolved = {
            key: _resolve(source, match) for key, source in self.params.items()
        }
        intent = self.intent(self.intent_id(origin, match.anchor.id), **resolved)
        return MutationDecision(match=match, intent=intent, reason="")


def _resolve(source: Any, match: Match) -> Any:
    """One intent parameter's value for this row.

    A :class:`FromRow` naming a field the row does not carry raises
    ``KeyError`` rather than degrading to ``None``: evaluation is total, but
    *construction* is not, and an intent silently built with a ``None`` anchor
    would fail much later and much less legibly. The application operators
    validate the field set up front so this is unreachable in practice.
    """
    if isinstance(source, FromRow):
        return match.fields[source.field]
    if isinstance(source, FromAnchor):
        return match.anchor.id
    return source


# ── the named mutation values ───────────────────────────────────────────────
# One per operation. A rule names one of these; so does a workflow. There is no
# second table and no per-site variant: that is fork #2's whole content.


REMOVE_IMPORT = Mutation(
    name="remove",
    intent=RemoveImportIntent,
    params={"symbol_id": FromRow("symbol_id"), "name": FromRow("name")},
    floor=Confidence.DECLARED,
)
"""Delete an unused import binding. The ``unused-imports`` repair.

No preconditions: the frozen rule attaches this remedy to **every** finding
unconditionally, and the only thing that ever withheld it was the
``DECLARED`` gate a consumer applied afterwards — which is the floor, here.
"""

REWRITE_STAR_IMPORT = Mutation(
    name="rewrite",
    intent=RewriteStarImportIntent,
    params={"symbol_id": FromAnchor(), "module": FromRow("imported_from")},
    floor=Confidence.DECLARED,
    preconditions=(
        Precondition("target-not-indexed", row.indexed.is_true()),
        Precondition("no-used-names", not_(row.name_count.eq(0))),
    ),
)
"""Replace ``from m import *`` with the names the file actually uses.

The frozen guard is ``names and file_confidence is DECLARED``, and it splits
cleanly in two. ``file_confidence`` is the row's own intrinsic evidence
(``DECLARED`` for a lone star, ``HEURISTIC`` for one of several or an
unindexed target), so the floor rejects the multi-star and unindexed rows with
no clause of its own; ``names`` is the two preconditions, which is why all four
of the rule's message partitions can carry this one mutation and none of them
needs to know which partition it is in.
"""

RENAME_DOCSTRING_PARAM = Mutation(
    name="rename-param",
    intent=RenameDocstringParamIntent,
    params={
        "symbol_id": FromRow("symbol_id"),
        "old_param": FromRow("param"),
        "new_param": FromRow("counterpart"),
        "style": FromRow("detected_style"),
    },
    floor=Confidence.DECLARED,
    preconditions=(
        Precondition("not-a-ghost", row.drift.eq("ghost")),
        Precondition("not-the-unambiguous-rename", all_of(
            row.ghost_count.eq(1), row.missing_count.eq(1)
        )),
    ),
)
"""Rewrite one stale documented parameter name. The ``docstring-drift`` repair.

The frozen ``renameable`` guard is ``len(ghosts) == 1 and len(missing) == 1``,
and only the ghost loop consults it — the ``require-complete`` missing loop
attaches nothing. Both are preconditions rather than clauses on the ghost
part's selection, because ``docstring-drift`` declares **one** repair and the
mutation is what carries it; silencing the missing part by simply not giving it
a mutation would spell one operation as two half-declarations, which is the
shape fork #2 forbids.

``style`` is the **detected** style — ``section.style``, as the frozen rule
passes it — and not the configured ``[tool.pypeeker.docstring-drift].style``
option, which is ``None`` under autodetection.
"""

TUPLIFY = Mutation(
    name="tuplify",
    intent=TuplifyIntent,
    params={"symbol_id": FromAnchor(), "name": FromRow("name")},
    floor=Confidence.DECLARED,
)
"""Rewrite a never-mutated list literal as a tuple. The ``prefer-tuple`` repair.

Unconditional in the frozen rule — every ``prefer-tuple`` finding carries a
``TuplifyIntent``, because the safety question (is the binding mutated? does it
escape?) is asked by the *selection*, in ``TUPLE_CANDIDATE``, before a finding
exists at all. So there is nothing left for a precondition to ask, and the
floor is the only gate: the rule reports at ``DECLARED`` through the meta-read
law (fork #4), which is exactly the property the ledger's own spec note says a
port meeting to ``INFERRED`` would break — and breaking it would silently kill
this autofix rather than merely mislabel a finding.
"""

DELETE_SYMBOL = Mutation(
    name="delete",
    intent=DeleteSymbolIntent,
    params={"symbol_id": FromRow("symbol_id")},
    floor=Confidence.DECLARED,
    preconditions=(
        Precondition("public-api", not_(row.visibility.eq(Visibility.PUBLIC))),
    ),
)
"""Delete a dead non-public symbol. The ``unused-public-symbol`` repair.

The frozen guard is ``symbol.visibility is not Visibility.PUBLIC`` — dead
private code is safe to delete, dead public API is somebody else's contract —
and it is a pointwise property of the row, so it is a precondition. The rule
only reaches a non-public symbol at all under its ``also-private`` option; with
that option off (the default, and every differential corpus) this mutation
yields nothing, which is why no target grades it.

The floor is ``DECLARED``, which is where the dynamic-access weakening lands:
a module reached through ``getattr`` downgrades its rows to ``HEURISTIC``, and
the frozen consumer's ``auto_fixable`` discards their remedies. Same gate, one
place.

Its derived id is the one frozen id fork #5 genuinely changes:
``unused-public-symbol:delete:<sid>`` where the frozen literal reads
``unused-symbol:delete:<sid>``, naming a rule that does not exist. Recorded in
``dsl-rewrite.md``'s ledger, and unobservable on every differential target.
"""

DEMOTE = Mutation(
    name="demote",
    intent=ChangeVisibilityIntent,
    params={"symbol_id": FromAnchor(), "direction": "demote"},
    floor=Confidence.INFERRED,
    preconditions=(
        Precondition(
            "dunder-or-main",
            not_(any_of(
                row.name.eq("main"),
                all_of(row.name.startswith("__"), row.name.matches("*__")),
            )),
        ),
        Precondition("already-private", not_(row.name.startswith("_"))),
    ),
)
"""Make a public symbol private (``name -> _name``).

**The shared value fork #2 is about.** ``pypeeker.dsl.demotion`` builds two
selections over it — a CLI-typed anchor and the three privatize nomination
rules — and there is no second demote mutation for either of them to reach
for. The frozen pair had two of everything: ``cli.py``'s single-symbol demote
built a ``ChangeVisibilityIntent`` while ``refactor.privatize`` built a
``RenameIntent`` with a hand-computed ``"_" + name``, and the two filtered on
different confidence levels.

``ChangeVisibilityIntent`` is the surviving kind because the operation *is* its
identity: ``direction="demote"`` names the transform, and the intent derives the
new name and the export handling itself, so a row needs no name arithmetic. The
``RenameIntent`` spelling had to recompute both at every site, which is where a
divergence gets written.

``floor=INFERRED`` is privatize's ``skip_heuristic``, restated on the lattice.
It is **not identical**: the frozen ``_is_heuristic`` is ``== "heuristic"``, so
an ``UNKNOWN`` nomination passes it and a rank floor rejects it. Ledgered in
``dsl-rewrite.md``; a rank floor is what fork #2 asks for, and admitting the
bottom of the lattice into an automatic rewrite was never the intent.

The preconditions are the two the frozen ``_demote_candidates`` pre-filter can
ask of a name alone, in its order — ``dunder-or-main`` before
``already-private``, which the frozen code comments on explicitly ("checked
before already-private so dunders report the more precise reason"), and which
is the whole reason a decision carries a reason code rather than a bool. Its
other five skips are not pointwise properties of a name (they need the query
engine, the class hierarchy, the project's library-mode configuration, or the
rest of the batch) and are recorded in the ledger as landing at the flip. The
dunder clause is the two-clause ``startswith("__") and matches("*__")`` spelling
the phase-3d ledger note requires, never ``matches("__*__")``: names of two and
three underscores reach it, and the four-character glob would let them through.
"""

MUTATIONS: Mapping[str, Mutation] = MappingProxyType({
    mutation.name: mutation
    for mutation in (
        REMOVE_IMPORT,
        REWRITE_STAR_IMPORT,
        RENAME_DOCSTRING_PARAM,
        TUPLIFY,
        DELETE_SYMBOL,
        DEMOTE,
    )
})
"""Every named mutation, by name — the write half's answer to ``EXPRESSIONS``.

Keyed on :attr:`Mutation.name` rather than on the intent kind, because the name
is what a derived ``intent_id`` spells and therefore what a consumer reading a
``fix_id`` has in hand.
"""
