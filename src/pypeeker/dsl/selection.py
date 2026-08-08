"""Selections: a universe, plus stages applied strictly in the order they were written.

Fork #3 of ``dsl-rewrite.md`` resolves to **written-order evaluation, no
optimizer**, and this module is where that promise is either kept or quietly
broken. It is kept literally: :meth:`Selection.rows` walks the stage tuple
front to back, evaluating each against whatever the previous stage produced.
There is no predicate pushdown, no reordering of ``where`` clauses, no
rewriting of a ``follow`` into a join. Two selections that differ only in the
order of their clauses are different selections, and the DSL will run both the
way they were written.

That is not merely a purity commitment. The trait registry is deliberately
last-registration-wins, opaque predicates can have side effects, and both mean
a reordering is observable. It also makes the failure modes teachable: a
``where`` reading a field an earlier ``project`` dropped raises, because at
that point in the written program the field genuinely is not there.

**Reach is derived here and nowhere else.** :attr:`Selection.reach` joins the
reach of every stage — each ``where``'s expression, each ``follow`` step's
declared cost. There is no constructor argument and no setter; the only way to
make a selection reach ``PROJECT`` is to write something that actually leaves
the file. That is the panel's "scope derived from the expression, never
declared", made structural rather than aspirational.

**Bare callables are refused.** Fork #9: ``where(lambda r: ...)`` raises,
because a lambda is a hole in the derivation tree that ``--why`` cannot
describe and reach cannot be derived through. The sanctioned escape is
:func:`pypeeker.dsl.opaque`, which costs the author one mandatory ``reads=``
declaration and buys back both.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from pypeeker.analysis import Trait, get_trait_provider
from pypeeker.dsl.anchors import Anchor
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import DerivedFieldError, OpaquePredicateError, UnknownFieldError
from pypeeker.dsl.evidence import Derivation, meet
from pypeeker.dsl.expr import UNMATCHED, EvalContext, Expr, FieldRead, Opaque
from pypeeker.dsl.facts import Fact, _FactLookup, fact_specs
from pypeeker.dsl.reach import Reach, join
from pypeeker.dsl.universes import _Record, _Universe, _universe
from pypeeker.models import Confidence, FileIndex

_NO_TRAITS: Mapping[str, Trait] = MappingProxyType({})


def _matched(node: Derivation) -> bool:
    """Whether a derivation's value counts as a match.

    :data:`~pypeeker.dsl.UNMATCHED` is checked explicitly rather than relying
    on its falsiness, so the intent survives a future sentinel that is not
    falsy.
    """
    return node.value is not UNMATCHED and bool(node.value)


def _field_reads(expr: Expr) -> frozenset[str]:
    """Field names read through :class:`~pypeeker.dsl.FieldRead` nodes.

    Deliberately **not** :attr:`Expr.field_names`, which also folds in an
    opaque's declared ``reads=`` tokens. Those describe a body the DSL cannot
    see, and they are free-form: an honest ``reads=("the symbol's own name",)``
    must not become an authoring error just because no universe declares that
    string. Opaque tokens are checked separately and much more narrowly by
    :func:`_shadowed_opaque_reads`.
    """
    found: set[str] = set()
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, FieldRead):
            found.add(node.name)
        stack.extend(node.children)
    return frozenset(found)


def _shadowed_opaque_reads(
    expr: Expr, universe: _Universe, visible: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    """Opaque-declared reads naming a real field of ``universe`` that ``visible`` lost.

    The narrowest question worth asking about a declaration the DSL cannot
    verify. A token nobody declares as a field is prose and stays legal; a
    token that *is* a field of this universe but was dropped by an earlier
    ``project()`` is different, because the body will read it as ``None`` and
    go false for every row — the silent empty result fork #12 exists to kill,
    reached through the sanctioned escape hatch. The same predicate spelled as
    an expression already refuses, and :func:`pypeeker.dsl.trait` already
    validates these very tokens, so accepting it here is the outlier.

    Returns:
        ``(opaque name, field name)`` pairs, sorted, for the caller to refuse.
    """
    found: set[tuple[str, str]] = set()
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, Opaque):
            found.update(
                (node.name, name)
                for name in node.field_names
                if name in universe.fields and name not in visible
            )
        stack.extend(node.children)
    return tuple(sorted(found))


class _LazyTraits(Mapping[str, Trait]):
    """Traits for one row's anchor, resolved on first read and cached.

    A selection knows which traits its expressions name before it evaluates
    anything (:attr:`Expr.trait_names`), but resolving one costs an analysis
    pass over the file. Rows rejected by an earlier clause never pay for the
    traits a later clause would have read.

    Only names with a registered provider are members, so an expression naming
    a trait nobody registered raises
    :class:`~pypeeker.dsl.UnknownTraitError` listing the ones that do resolve —
    rather than silently reading ``None`` and going false everywhere.
    """

    def __init__(self, names: Iterable[str], file_index: FileIndex, symbol_id: str) -> None:
        self._names = tuple(sorted(names))
        self._index = file_index
        self._symbol_id = symbol_id
        self._cache: dict[str, Trait] = {}

    def __getitem__(self, name: str) -> Trait:
        if name in self._cache:
            return self._cache[name]
        provider = get_trait_provider(name)
        if provider is None:
            raise KeyError(name)
        resolved = provider(self._index, self._symbol_id)
        self._cache[name] = resolved
        return resolved

    def __iter__(self) -> Iterator[str]:
        return iter(name for name in self._names if get_trait_provider(name) is not None)

    def __len__(self) -> int:
        return sum(1 for _ in iter(self))


@dataclass(frozen=True)
class Match:
    """One row that survived every stage, with the evidence it survived on.

    ``anchor`` — what the row is about, carrying its own evidence.
    ``fields`` — the currently-visible fields, after any ``project``.
    ``confidence`` — the meet of the row's intrinsic evidence and every
    ``where`` derivation that passed. This is the number a rule ported onto the
    DSL would report, and it is order-independent by construction.
    ``derivations`` — one derivation per ``where`` stage, in written order.
    Plural because a selection may filter more than once, and collapsing them
    into a single node would invent a conjunction the author did not write;
    ``--why`` renders the list.
    """

    anchor: Anchor
    fields: Mapping[str, Any]
    confidence: Confidence
    derivations: tuple[Derivation, ...] = ()


@dataclass(frozen=True)
class _Where:
    """A filter stage."""

    expr: Expr


@dataclass(frozen=True)
class _FollowStage:
    """A navigation stage, named by the step it takes."""

    step: str


@dataclass(frozen=True)
class _Project:
    """A stage narrowing which fields are visible from here on."""

    fields: tuple[str, ...]


@dataclass(frozen=True)
class _Field:
    """A stage adding one derived field, computed per row.

    Projection's dual: ``project`` narrows the row's vocabulary, this widens
    it. The point is that a rule's message stays a ``str.format`` template over
    visible fields — fork #9's "opacity must be declared" applies to wording as
    much as to predicates, and a callable message would put rule semantics
    somewhere the derivation tree cannot describe.
    """

    name: str
    expr: Expr


_Stage = _Where | _FollowStage | _Project | _Field


@dataclass(frozen=True)
class _Carried:
    """A record in flight, with the evidence and derivations accumulated so far."""

    record: _Record
    confidence: Confidence
    derivations: tuple[Derivation, ...]


class Selection:
    """A described set of rows: one universe, then stages, in written order.

    Immutable. :meth:`where`, :meth:`follow` and :meth:`project` each return a
    new selection with one more stage, so a selection can be shared, named, and
    extended without any risk of a consumer mutating someone else's query.

    Construct one through :func:`symbols`, :func:`references`, :func:`imports`,
    :func:`modules` or :func:`scopes` rather than directly; the universe name
    is validated either way.
    """

    def __init__(self, universe: str | _Universe, stages: Iterable[_Stage] = ()) -> None:
        # A name for one of the five fork #8 fixes, validated on the way in; or
        # a row source object, which is how the primitive tier hands over a
        # source that is deliberately not registered under a name (see
        # pypeeker.dsl.fact_source).
        self._universe: _Universe = (
            _universe(universe) if isinstance(universe, str) else universe
        )
        self._stages: tuple[_Stage, ...] = tuple(stages)

    # -- description --------------------------------------------------------

    @property
    def universe(self) -> str:
        """Name of the universe rows currently come from, after any ``follow``."""
        return self._terminal.name

    @property
    def fields(self) -> frozenset[str]:
        """Field names visible at the end of the written program.

        Starts as the current universe's declared fields and narrows at each
        ``project``; a ``follow`` resets it to the target universe's fields.
        """
        return self._trace()[1]

    @property
    def reach(self) -> Reach:
        """How far out of one file this selection reads. Derived; there is no setter.

        The join of every stage's reach: a ``where``'s expression reach (an
        opaque declaring a ``project:`` read pushes it up) and each ``follow``
        step's declared cost. A selection is ``FILE`` until something in it
        genuinely leaves the file.
        """
        return join(*(reach for _, reach in self._stage_reaches()))

    @property
    def stages(self) -> tuple[str, ...]:
        """The written program as stage names, for display and for tests."""
        return tuple(_stage_name(stage) for stage in self._stages)

    # -- building -----------------------------------------------------------

    def where(self, predicate: Expr) -> Selection:
        """Keep only rows satisfying ``predicate``.

        Args:
            predicate: an :class:`~pypeeker.dsl.Expr`. A bare callable is
                refused — see :class:`~pypeeker.dsl.OpaquePredicateError` and
                :func:`pypeeker.dsl.opaque`.

        Returns:
            A new selection with the filter appended.

        Raises:
            OpaquePredicateError: ``predicate`` is a bare callable.
            TypeError: ``predicate`` is neither an expression nor a callable.
            UnknownFieldError: the predicate reads a field that is not visible
                at this point in the written program — whether through a
                ``row.<name>`` node or through an opaque's ``reads=``
                declaration naming a field an earlier ``project`` dropped.
        """
        _require_expression("where", predicate)
        universe, visible = self._trace()
        self._validate_reads(predicate, universe, visible)
        return Selection(self._universe, (*self._stages, _Where(predicate)))

    def with_field(self, name: str, expr: Expr) -> Selection:
        """Add a derived field ``name``, computed per row by ``expr``.

        The dual of :meth:`project`. It exists so a rule's wording can stay a
        ``str.format`` template over the row's fields even when what it needs to
        say — which package, which cycle members, which impurities — comes out
        of a primitive fact rather than out of the model. It is also how a row
        source that publishes no ``line`` (the modules universe) acquires one.

        The derivation is evidence like any other: its confidence meets into
        the row's, and it is appended to :attr:`Match.derivations`, so a field
        computed from a ``HEURISTIC`` fact cannot be quoted in a message a
        finding then reports as ``DECLARED``.

        Args:
            name: the new field's name. Must not already be visible.
            expr: an expression over the currently-visible fields. Its value is
                *read*, not filtered on, so a falsy result adds a falsy field
                rather than dropping the row.

        Returns:
            A new selection with the derived field appended, visible to every
            later stage and to the returned :class:`Match` rows.

        Raises:
            DerivedFieldError: ``name`` is already visible here. Written order
                is normative, and a derived field that could displace a model
                field would make ``row.<name>`` mean different things at
                different points in one program.
            OpaquePredicateError: ``expr`` is a bare callable.
            TypeError: ``expr`` is neither an expression nor a callable.
            UnknownFieldError: ``expr`` reads a field that is not visible here.
        """
        _require_expression("with_field", expr)
        universe, visible = self._trace()
        if name in visible:
            raise DerivedFieldError(name, visible, universe=universe.name)
        self._validate_reads(expr, universe, visible)
        return Selection(self._universe, (*self._stages, _Field(name, expr)))

    def follow(self, step: str) -> Selection:
        """Navigate to a related universe along ``step``.

        Args:
            step: a follow step the current universe declares. Use
                :func:`pypeeker.dsl.universe_follows` to enumerate them.

        Returns:
            A new selection whose rows come from the step's target universe,
            with the target's full field set visible again.

        Raises:
            UnknownFollowError: the current universe declares no such step;
                the message lists the ones it does.
        """
        universe, _ = self._trace()
        universe.require_follow(step)
        return Selection(self._universe, (*self._stages, _FollowStage(step)))

    def project(self, *fields: str) -> Selection:
        """Narrow the visible fields to ``fields``.

        Args:
            *fields: field names, each currently visible.

        Returns:
            A new selection exposing only those fields from here on — to later
            ``where`` clauses as well as to the returned :class:`Match` rows.
            That a later clause can no longer read a dropped field is written
            order made observable, not an accident.

        Raises:
            UnknownFieldError: a name is not visible at this point.
        """
        universe, visible = self._trace()
        for name in fields:
            universe.require_field(name, visible)
        return Selection(self._universe, (*self._stages, _Project(tuple(fields))))

    # -- evaluation ---------------------------------------------------------

    def rows(self, corpus: Corpus) -> tuple[Match, ...]:
        """Run the written program against ``corpus``, in order.

        Args:
            corpus: the indexes to quantify over.

        Returns:
            The surviving rows as :class:`Match` values, in the order the
            universe and the follow steps produced them.
        """
        universe = self._universe
        visible = frozenset(universe.fields)
        facts = _FactLookup(corpus, self._fact_specs())
        carried = tuple(
            _Carried(record, record.evidence, ()) for record in universe.rows(corpus)
        )
        for stage in self._stages:
            if isinstance(stage, _Where):
                carried = _apply_where(stage, carried, visible, facts)
            elif isinstance(stage, _FollowStage):
                follow = universe.require_follow(stage.step)
                carried = _apply_follow(follow.expand, carried, corpus)
                universe = _universe(follow.target)
                visible = frozenset(universe.fields)
            elif isinstance(stage, _Field):
                carried = _apply_field(stage, carried, visible, facts)
                visible = visible | {stage.name}
            elif isinstance(stage, _Project):
                visible = frozenset(stage.fields)
            else:
                raise TypeError(f"unhandled selection stage: {stage!r}")
        return tuple(
            Match(
                anchor=item.record.anchor,
                fields={
                    name: value
                    for name, value in item.record.fields.items()
                    if name in visible
                },
                confidence=item.confidence,
                derivations=item.derivations,
            )
            for item in carried
        )

    # -- internals ----------------------------------------------------------

    def _fact_specs(self) -> tuple[Fact, ...]:
        """Every primitive fact any stage of this program reads, deduplicated by name."""
        found: dict[str, Fact] = {}
        for stage in self._stages:
            expr = getattr(stage, "expr", None)
            if expr is None:
                continue
            for spec in fact_specs(expr):
                found.setdefault(spec.name, spec)
        return tuple(found[name] for name in sorted(found))

    @staticmethod
    def _validate_reads(expr: Expr, universe: _Universe, visible: frozenset[str]) -> None:
        """Refuse an expression reading a field that is not visible at this point."""
        for name in sorted(_field_reads(expr)):
            universe.require_field(name, visible)
        for opaque_name, name in _shadowed_opaque_reads(expr, universe, visible):
            raise UnknownFieldError(
                name, visible, universe=universe.name, declared_by=opaque_name
            )

    @property
    def _terminal(self) -> _Universe:
        """The universe rows come from after every follow in the program."""
        return self._trace()[0]

    def _trace(self) -> tuple[_Universe, frozenset[str]]:
        """Replay the stage list to get the current universe and visible fields.

        Cheap (stage lists are short) and stateless, which keeps a selection
        genuinely immutable: nothing is cached that a copy could disagree with.
        """
        universe = self._universe
        visible = frozenset(universe.fields)
        for stage in self._stages:
            if isinstance(stage, _FollowStage):
                universe = _universe(universe.require_follow(stage.step).target)
                visible = frozenset(universe.fields)
            elif isinstance(stage, _Project):
                visible = frozenset(stage.fields)
            elif isinstance(stage, _Field):
                visible = visible | {stage.name}
        return universe, visible

    def _stage_reaches(self) -> tuple[tuple[str, Reach], ...]:
        """Each stage's contribution to the derived reach, in written order.

        Every stage kind is matched **explicitly**, and an unrecognized one
        raises. A trailing ``else`` reading "must be a project, so FILE" is how
        a stage carrying a ``PROJECT``-reach expression silently reports
        ``FILE`` — the exact drift "reach is declared next to the code that
        consults the resolver" exists to prevent, and invisible when it happens.
        """
        universe = self._universe
        contributions: list[tuple[str, Reach]] = []
        for stage in self._stages:
            if isinstance(stage, _Where):
                contributions.append(("where", stage.expr.reach))
            elif isinstance(stage, _FollowStage):
                follow = universe.require_follow(stage.step)
                contributions.append((f"follow:{stage.step}", follow.reach))
                universe = _universe(follow.target)
            elif isinstance(stage, _Field):
                contributions.append((f"with_field:{stage.name}", stage.expr.reach))
            elif isinstance(stage, _Project):
                contributions.append(("project", Reach.FILE))
            else:
                raise TypeError(f"unhandled selection stage: {stage!r}")
        return tuple(contributions)

    def __repr__(self) -> str:
        program = "".join(f".{stage}" for stage in self.stages)
        return f"<Selection {self._universe.name}(){program} reach={self.reach.value}>"


def _stage_name(stage: _Stage) -> str:
    """One stage rendered as its display name, matched exhaustively."""
    if isinstance(stage, _Where):
        return "where"
    if isinstance(stage, _FollowStage):
        return f"follow:{stage.step}"
    if isinstance(stage, _Field):
        return f"with_field:{stage.name}"
    if isinstance(stage, _Project):
        return f"project:{','.join(stage.fields)}"
    raise TypeError(f"unhandled selection stage: {stage!r}")


def _require_expression(caller: str, expr: Any) -> None:
    """Refuse a bare callable, or anything that is not an expression at all.

    Fork #9, applied identically wherever a stage takes an expression: a lambda
    is a hole ``--why`` cannot describe and reach cannot be derived through, and
    that is as true of a stage that *computes a field* as of one that filters.
    """
    if isinstance(expr, Expr):
        return
    if callable(expr):
        raise OpaquePredicateError(
            f"{caller}() takes an expression, not a bare callable "
            f"({getattr(expr, '__name__', type(expr).__name__)!r}). A raw "
            f"callable is a hole the derivation tree cannot describe and reach cannot "
            f"be derived through. Build the predicate from row/trait_of, or wrap it "
            f"with @opaque(name, reads=(...)) to declare what it looks at."
        )
    raise TypeError(
        f"{caller}() takes an expression, got {type(expr).__name__}; "
        f"build one from pypeeker.dsl.row or pypeeker.dsl.trait_of"
    )


def _row_context(
    record: _Record,
    visible: frozenset[str],
    trait_names: frozenset[str],
    facts: _FactLookup,
    *,
    discards_falsity: bool,
) -> EvalContext:
    """Build the evaluation context for one row.

    Traits degrade to an empty mapping for a row with no ``env``. A row source
    in the primitive tier (:func:`pypeeker.dsl.fact_source`) quantifies over
    something no file holds, so there is no :class:`~pypeeker.models.FileIndex`
    to resolve a provider against; an expression naming a trait there raises
    :class:`~pypeeker.dsl.UnknownTraitError` listing nothing, which is the
    honest answer rather than an ``AttributeError`` from inside the evaluator.
    """
    return EvalContext(
        fields={name: value for name, value in record.fields.items() if name in visible},
        traits=(
            _NO_TRAITS
            if record.env is None
            else _LazyTraits(trait_names, record.env.index, record.trait_anchor)
        ),
        discards_falsity=discards_falsity,
        facts=facts.for_anchor(record.trait_anchor),
    )


def _apply_where(
    stage: _Where,
    carried: tuple[_Carried, ...],
    visible: frozenset[str],
    facts: _FactLookup,
) -> tuple[_Carried, ...]:
    """Evaluate one filter over the rows currently in flight."""
    trait_names = stage.expr.trait_names
    kept: list[_Carried] = []
    for item in carried:
        context = _row_context(
            item.record,
            visible,
            trait_names,
            facts,
            # This is the one place that can promise it: a predicate going
            # false here drops the row, so its confidence is never reported
            # and a conjunction may stop at its first false operand.
            # Everything that *reads* falsity instead -- not_(), .eq(False)
            # -- withdraws the licence for its own subtree, so no partial
            # meet ever reaches a Match.
            discards_falsity=True,
        )
        node = stage.expr.evaluate(context)
        if _matched(node):
            kept.append(
                _Carried(
                    record=item.record,
                    confidence=meet(item.confidence, node.confidence),
                    derivations=(*item.derivations, node),
                )
            )
    return tuple(kept)


def _apply_field(
    stage: _Field,
    carried: tuple[_Carried, ...],
    visible: frozenset[str],
    facts: _FactLookup,
) -> tuple[_Carried, ...]:
    """Compute one derived field for every row in flight; drops nothing.

    ``discards_falsity`` is **off**: the expression's value is read, not
    filtered on, so a falsy answer is load bearing here exactly as it is under
    :class:`~pypeeker.dsl.Not` and :class:`~pypeeker.dsl.Compare`.

    The record is rebuilt rather than mutated. :class:`_Record` is frozen and
    :meth:`_Record.restated` shares the same ``fields`` mapping between copies,
    so writing into it in place would edit rows nobody asked to change.
    """
    trait_names = stage.expr.trait_names
    out: list[_Carried] = []
    for item in carried:
        context = _row_context(
            item.record, visible, trait_names, facts, discards_falsity=False
        )
        node = stage.expr.evaluate(context)
        widened = replace(
            item.record, fields={**item.record.fields, stage.name: node.value}
        )
        out.append(
            _Carried(
                record=widened,
                confidence=meet(item.confidence, node.confidence),
                derivations=(*item.derivations, node),
            )
        )
    return tuple(out)


def _apply_follow(
    expand: Callable[[_Record, Corpus], Iterator[_Record]],
    carried: tuple[_Carried, ...],
    corpus: Corpus,
) -> tuple[_Carried, ...]:
    """Navigate every row in flight, keeping distinct targets in first-reached order.

    Duplicates are dropped rather than joined: ten symbols in one file followed
    to ``module`` are one module, not ten. Where two paths reach the same
    target on different evidence the first-reached path stands, which is the
    answer consistent with written order being normative — see
    :mod:`pypeeker.dsl.universes`.

    The target row's *anchor* evidence is met against the source **row's**
    intrinsic evidence, not against the accumulated match confidence: how
    well-founded an identification is depends on the path taken to it, not on
    how confident an unrelated predicate happened to be. The accumulated
    confidence is met separately into :attr:`Match.confidence`, where it
    belongs.
    """
    seen: set[str] = set()
    out: list[_Carried] = []
    for item in carried:
        for produced in expand(item.record, corpus):
            if produced.anchor.id in seen:
                continue
            seen.add(produced.anchor.id)
            weakened = produced.restated(item.record.evidence)
            out.append(
                _Carried(
                    record=weakened,
                    confidence=meet(item.confidence, weakened.evidence),
                    derivations=item.derivations,
                )
            )
    return tuple(out)


def symbols() -> Selection:
    """Every symbol the corpus declares — functions, classes, variables, imports, modules."""
    return Selection("symbols")


def references() -> Selection:
    """Every recorded use of a name."""
    return Selection("references")


def imports() -> Selection:
    """Every import binding, carrying ``HEURISTIC`` evidence when dynamically recovered."""
    return Selection("imports")


def modules() -> Selection:
    """One row per indexed file."""
    return Selection("modules")


def scopes() -> Selection:
    """Every lexical scope: modules, classes, functions, comprehensions, lambdas."""
    return Selection("scopes")
