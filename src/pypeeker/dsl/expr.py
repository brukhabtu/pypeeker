"""The predicate grammar: expression nodes, the authoring surface, and evaluation.

An expression is a small immutable tree. Evaluating one against an
:class:`EvalContext` (the facts of a single row) produces a
:class:`~pypeeker.dsl.evidence.Derivation` tree of the same shape, carrying
both the answer and the evidence behind it. Nothing here mutates anything —
phase 2 of the DSL rewrite is the read half, and there are no mutation
terminals in this package.

Authoring surface
-----------------

::

    from pypeeker.dsl import row, trait_of, opaque, all_of

    row.kind.eq(SymbolKind.VARIABLE)              # a model field read
    trait_of("type-annotation").value.eq("list")  # a trait value read
    trait_of("type-annotation").confidence        # a DECLARED meta-read
    trait_of("type-annotation").at(INFERRED)      # value, asserted at a level
    a & b, a | b, ~a                              # conjunction/disjunction/negation

**No** ``__eq__`` **overloading.** Comparisons are named methods —
:meth:`Expr.eq`, :meth:`Expr.ne`, :meth:`Expr.is_in`, :meth:`Expr.matches`,
:meth:`Expr.startswith`, :meth:`Expr.is_true`, and over dotted names and
collections :meth:`Expr.is_within`, :meth:`Expr.any_other_than`,
:meth:`Expr.any_outside` — and :meth:`Expr.attr` is the one projection. Every
one of them takes either a plain value or another expression as its right-hand
side, and an expression there is a child like any other, so reach and evidence
are derived through it. Building a node out of ``==`` would silently drop
``__hash__``, break every set and dict of nodes, and turn ``assert node ==
expected`` in a test into a truthy expression that asserts nothing. Only
``&``, ``|`` and ``~`` are overloaded; those have no such hazard.

Evaluation semantics
--------------------

*Written order, no optimizer* (fork #3). Clauses run in the order written and
neither combinator ever reorders. :class:`AllOf` short-circuits on its first
falsy operand where a falsy answer is discarded — at a selection's filter
stage, where it is observable through an opaque's side effects — and runs every
operand anywhere the conjunction's falsity can be *read* instead
(:meth:`EvalContext.consuming_falsity`). :class:`AnyOf` never short-circuits.
Both restrictions have one cause: a confidence must be a property of the set of
clauses, and skipping a clause whose result is still load bearing would make it
depend on clause order, which fork #4 forbids.

*Meet over every contribution* (fork #4). Each node reports the evidence it
stands on; combinators meet over their inputs. See
:mod:`pypeeker.dsl.evidence` for the lattice and for why written-order
evaluation and order-independent confidence are not in tension.

*The meta-read law* (fork #4). ``trait_of(T).confidence`` reports
``DECLARED``, unconditionally — the level the index recorded is itself a
declared fact — and :class:`TraitAssertion` (``trait_of(T).at(level)``) is a
primitive leaf reporting ``DECLARED`` for the same reason. That is what makes
the ``prefer-tuple`` shape report ``DECLARED`` rather than ``INFERRED``, which
``dsl-rewrite.md``'s divergence ledger calls out as correctness, not
divergence. The uncertified spelling ``trait_of(T).value.eq("list")`` keeps
the trait's own ``INFERRED``, as it should: "this symbol is a list" is
inferred, whereas "this symbol's annotation is an *inferred* list" is a
declared property of the index.

*Evaluation is total.* No node raises because a row lacks a fact: a missing
field, a missing attribute, or an assertion whose level does not match yields
a falsy result at ``UNKNOWN`` (or a non-match), so downstream predicates go
false. This matches the contract ``pypeeker.analysis``'s builtin providers
already hold to. Loudness belongs to *resolution* and *authoring* —
:mod:`pypeeker.dsl.errors` — not to the evaluator. The one exception is a
trait name with no registered provider, which is an authoring mistake rather
than a gap in the index, and raises :class:`~pypeeker.dsl.errors.UnknownTraitError`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Any, Protocol

from pypeeker.analysis import Trait
from pypeeker.dsl.errors import (
    OpaquePredicateError,
    ReachError,
    UnknownFactError,
    UnknownTraitError,
)
from pypeeker.dsl.evidence import Derivation, meet
from pypeeker.dsl.reach import Reach, join
from pypeeker.models import Confidence

_NO_FIELDS: Mapping[str, Any] = MappingProxyType({})
_NO_TRAITS: Mapping[str, Trait] = MappingProxyType({})

TRAIT_READ_PREFIX = "trait:"
"""Prefix marking a trait read, in ``Derivation.reads`` and in ``opaque(reads=)``."""

FACT_READ_PREFIX = "fact:"
"""Prefix marking a primitive-fact read in ``Derivation.reads``.

The fact tier is fork #11's home for what an expression cannot express. A read
of one is declared in the derivation tree like any other substrate touch, so
``--why`` names the sweep a finding rests on rather than presenting it as an
unexplained constant.
"""

FIELD_READ_PREFIX = "field:"
"""Prefix marking a model field read in ``Derivation.reads``."""

PROJECT_READ_PREFIX = "project:"
"""Prefix an ``opaque(reads=)`` token to declare it reaches beyond the file."""

_COMPARISONS = frozenset({
    "eq",
    "ne",
    "is_in",
    "matches",
    "startswith",
    "is_true",
    "is_within",
    "any_other_than",
    "any_outside",
})

_TUPLE_RHS_COMPARISONS = frozenset({"is_in"})
"""Comparisons whose right-hand side is a fixed tuple, never an expression."""

_STRING_RHS_COMPARISONS = frozenset({"matches", "is_within"})
"""Comparisons whose right-hand side must be a string, or an expression producing one."""

_PREFIX_RHS_COMPARISONS = frozenset({"startswith"})
"""Comparisons whose right-hand side is a string prefix, a tuple of them, or an
expression producing a string."""


def _is_prefix_rhs(rhs: Any) -> bool:
    """True for what ``str.startswith`` accepts: a string or a tuple of strings."""
    if isinstance(rhs, str):
        return True
    return isinstance(rhs, tuple) and all(isinstance(item, str) for item in rhs)


def _within(value: str, prefix: str) -> bool:
    """True when the dotted name ``value`` is ``prefix`` itself or nested under it.

    The dotted-name containment test — ``pkg.mod`` is within ``pkg``,
    ``pkgx.mod`` is not — used by :meth:`Expr.is_within` and
    :meth:`Expr.any_outside`. Written once because getting it wrong by one
    character (``startswith(prefix)`` without the dot) silently accepts every
    sibling package whose name shares a prefix.
    """
    return value == prefix or value.startswith(prefix + ".")


def _is_iterable(value: Any) -> bool:
    """True for a collection the set-valued comparisons can quantify over.

    A string is iterable and is deliberately excluded: ``any_other_than`` over
    a string would quantify over its characters, which is never what an author
    writing it meant.
    """
    return isinstance(value, Iterable) and not isinstance(value, str | bytes)


def _holds_expr(rhs: Any) -> bool:
    """True when ``rhs`` is an expression, or a tuple with one inside.

    ``is_in`` collects its candidates into a tuple, so an expression written
    there hides one level down; refusing only the bare case would let
    ``is_in(row.name)`` through to compare a value against a node object.
    """
    if isinstance(rhs, Expr):
        return True
    return isinstance(rhs, tuple) and any(isinstance(item, Expr) for item in rhs)


class _Unmatched:
    """The value of an assertion whose level did not hold. Distinct from ``None``.

    ``None`` is a legitimate trait value (``type-annotation`` yields it for an
    unannotated symbol), so "the assertion did not hold" needs its own
    sentinel or ``trait_of(T).at(DECLARED).eq(None)`` would be true for a
    trait sitting at ``INFERRED``.
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<unmatched>"


UNMATCHED = _Unmatched()
"""Sentinel value for an assertion whose asserted level did not hold."""


class _RowView:
    """Attribute access over one row's fields, handed to an opaque predicate's body.

    Total by design: a name the row does not carry reads as ``None`` rather
    than raising, matching :class:`FieldRead`. An opaque's ``reads=``
    declaration is *declared*, not verified — that is the price fork #9
    accepts for opacity, and the reason the declaration is mandatory.
    """

    __slots__ = ("_fields",)

    def __init__(self, fields: Mapping[str, Any]) -> None:
        self._fields = fields

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._fields.get(name)

    def __repr__(self) -> str:
        return f"<row {dict(self._fields)!r}>"


class FactResolver(Protocol):
    """Answers one primitive-fact read for the row currently being evaluated.

    Called as ``resolver(name, params)``. Returns the
    :class:`~pypeeker.analysis.Trait` this row's anchor has for that fact, or
    ``None`` when the table holds no entry for it — a normal answer, not a
    failure. Raises :class:`~pypeeker.dsl.UnknownFactError` when the *fact* is
    not something this resolver can compute at all, which is a wiring mistake
    rather than a gap in the data.
    """

    def __call__(self, name: str, params: Any) -> Trait | None:
        """Resolve fact ``name`` with ``params`` for the current row's anchor."""
        ...


class _NoFacts:
    """The default resolver: there is none, so every fact read is a loud refusal.

    A fact is computed from the corpus, which exists only while a selection is
    running. Anywhere else — a hand-built :class:`EvalContext` in a test, an
    expression named as a trait — there is genuinely nothing to read, and
    saying so beats returning ``None`` and letting the predicate go quietly
    false for every row.
    """

    __slots__ = ()

    def __call__(self, name: str, params: Any) -> Trait | None:
        """Refuse: no corpus is in scope, so no fact table exists."""
        del params
        raise UnknownFactError(name, ())

    def __repr__(self) -> str:
        return "<no facts>"


_NO_FACTS: FactResolver = _NoFacts()


@dataclass(frozen=True)
class EvalContext:
    """The facts of one row, as an expression sees them.

    ``fields`` maps a universe's declared field names to this row's values.
    ``traits`` maps trait names to the :class:`~pypeeker.analysis.Trait` this
    row's anchor has for them; it may be lazy, and only needs to carry the
    traits the expression actually reads (see :attr:`Expr.trait_names`).

    ``facts`` resolves primitive-fact reads (fork #11's tier for fixpoints,
    SCCs and project-wide sweeps) against the corpus the selection is running
    over. It is **last** in the field order, and defaults to a resolver that
    refuses, so every existing positional construction and every hand-built
    context in a test keeps meaning what it meant.

    ``discards_falsity`` is the licence :class:`AllOf` needs to short-circuit,
    and it defaults to **off** because short-circuiting is only sound where a
    falsy answer is thrown away. The invariant a caller asserts by setting it:
    *a falsy value produced under this context never reaches a reported
    confidence* — it drops the row instead. Only
    :meth:`pypeeker.dsl.Selection.rows`' filter stage can promise that; naming
    an expression as a trait reports its value either way, and so does
    ``--why``, so both leave it off. :class:`Not`, :class:`Compare` and
    :class:`Attr` read their operand's value rather than filtering on it, so
    each clears the flag for its own subtree (:meth:`consuming_falsity`).

    ``corpus``, ``subject`` and ``universe`` are the project-reach substrate,
    and they are ``None`` everywhere a row is evaluated outside a selection —
    naming an expression as a trait, for instance, where the provider
    signature offers one ``FileIndex`` and no store. A node that needs them
    calls :meth:`require_corpus` and gets a loud
    :class:`~pypeeker.dsl.errors.ReachError` rather than an empty answer.

    ``subject`` is deliberately the row's **model object** — the
    :class:`~pypeeker.models.Symbol` or :class:`~pypeeker.models.Reference` the
    row was built from — and not the row record. Handing over the record would
    let a project column read back a field an earlier ``project()`` dropped,
    quietly undoing the narrowing that
    :meth:`pypeeker.dsl.Selection.project` describes as "written order made
    observable". ``universe`` names which kind of model object it is, so a
    column can dispatch without isinstance-sniffing the model.
    """

    fields: Mapping[str, Any] = field(default=_NO_FIELDS)
    traits: Mapping[str, Trait] = field(default=_NO_TRAITS)
    discards_falsity: bool = False
    corpus: Any = None
    subject: Any = None
    universe: str | None = None
    facts: FactResolver = field(default=_NO_FACTS)

    def consuming_falsity(self) -> EvalContext:
        """This context with the short-circuit licence withdrawn.

        Returned by every node that reads its operand's value instead of
        filtering on it. Under it a conjunction runs every operand even after
        one goes false, so its confidence is the meet over the whole set —
        which is what makes ``not_(all_of(a, b))`` report the same evidence as
        ``not_(all_of(b, a))``.

        Built with :func:`dataclasses.replace` rather than by re-listing the
        fields: a positional reconstruction silently drops any field added
        later, and the fields it would drop are exactly the substrate that
        :class:`Not`, :class:`Compare` and :class:`Attr` sit on top of — the
        project-reach corpus and the ``facts`` resolver alike.
        """
        if not self.discards_falsity:
            return self
        return replace(self, discards_falsity=False)

    def fact(self, name: str, params: Any) -> Trait | None:
        """Return this row's :class:`~pypeeker.analysis.Trait` for a primitive fact.

        Args:
            name: the fact's name, as its :class:`~pypeeker.dsl.Fact` spec
                declares it.
            params: the fact's parameters, hashable, as the read declared them.

        Returns:
            The trait the fact's table holds for this row's anchor, or ``None``
            when it holds none — the total-evaluation answer, which
            :class:`~pypeeker.dsl.FactRead` turns into ``None`` at ``UNKNOWN``.

        Raises:
            UnknownFactError: no fact table can answer ``name`` here, which
                normally means the expression is being evaluated outside a
                selection and so has no corpus to sweep.
        """
        return self.facts(name, params)

    def require_corpus(self, node: str) -> Any:
        """Return the corpus, or refuse loudly because this node cannot run without one.

        Args:
            node: how to name the node in the refusal, e.g. ``"in_set()"``.

        Returns:
            The :class:`~pypeeker.dsl.Corpus` this row is being evaluated
            against.

        Raises:
            ReachError: no corpus is in scope. Evaluation is otherwise total,
                but a project-reach node with no project substrate is an
                authoring mistake in the same family as naming a
                ``PROJECT``-reach expression as a trait — answering ``False``
                would report "not a member" for a set that was never built.
        """
        if self.corpus is None:
            raise ReachError(
                f"{node} reaches project, but no corpus is in scope for this "
                f"evaluation. Project-reach nodes only run inside "
                f"Selection.rows(corpus); a trait provider is called as "
                f"(FileIndex, symbol_id) -> Trait and has no store and no "
                f"cross-module resolver to offer one.",
                node=node,
            )
        return self.corpus

    def trait(self, name: str) -> Trait:
        """Return this row's :class:`~pypeeker.analysis.Trait` for ``name``.

        Raises :class:`~pypeeker.dsl.errors.UnknownTraitError`, naming the
        traits this context does carry, when nothing is registered under
        ``name``.
        """
        found = self.traits.get(name)
        if found is None:
            raise UnknownTraitError(name, self.traits.keys())
        return found

    @property
    def row(self) -> _RowView:
        """This row's fields as an attribute-access view, for opaque bodies."""
        return _RowView(self.fields)


def _truthy(node: Derivation) -> bool:
    """True if ``node``'s value counts as a match."""
    return node.value is not UNMATCHED and bool(node.value)


class Expr:
    """Base class for every expression node.

    Subclasses are frozen dataclasses, so nodes are hashable, structurally
    comparable, and safe to share between selections.
    """

    @property
    def children(self) -> tuple[Expr, ...]:
        """Sub-expressions of this node, in written order."""
        return ()

    @property
    def reach(self) -> Reach:
        """How far out of one file this expression reads. Derived, never declared."""
        return join(*(child.reach for child in self.children))

    @property
    def field_names(self) -> frozenset[str]:
        """Every model field name this expression reads."""
        return frozenset().union(*(child.field_names for child in self.children))

    @property
    def trait_names(self) -> frozenset[str]:
        """Every trait name this expression reads."""
        return frozenset().union(*(child.trait_names for child in self.children))

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Evaluate this node against ``ctx``, returning its derivation."""
        raise NotImplementedError

    # -- comparisons (named, never operator-overloaded) --------------------

    def eq(self, value: Any) -> Compare:
        """True when this expression's value equals ``value``."""
        return Compare(self, "eq", value)

    def ne(self, value: Any) -> Compare:
        """True when this expression's value differs from ``value``."""
        return Compare(self, "ne", value)

    def is_in(self, *values: Any) -> Compare:
        """True when this expression's value is one of ``values``."""
        return Compare(self, "is_in", tuple(values))

    def matches(self, pattern: str | Expr) -> Compare:
        """True when this expression's value is a string matching the glob ``pattern``."""
        return Compare(self, "matches", pattern)

    def startswith(self, prefix: str | tuple[str, ...] | Expr) -> Compare:
        """True when this expression's value is a string starting with ``prefix``.

        ``prefix`` may be a tuple of prefixes, exactly as :meth:`str.startswith`
        reads it: true when the value starts with *any* of them. The tuple is
        a constant the author wrote, so this stays one read of the operand —
        it does not turn into a quantifier over separate reads.
        """
        return Compare(self, "startswith", prefix)

    def is_true(self) -> Compare:
        """True when this expression's value is truthy."""
        return Compare(self, "is_true", None)

    def is_within(self, prefix: Any) -> Compare:
        """True when this expression's dotted name is ``prefix`` or nested under it.

        ``pkg.mod`` is within ``pkg``; ``pkgx.mod`` is not. ``prefix`` may be
        another expression, which is how a definition's module is tested
        against the package the row is about.
        """
        return Compare(self, "is_within", prefix)

    def any_other_than(self, value: Any) -> Compare:
        """True when this expression's collection holds anything that is not ``value``.

        The set-valued form of ``ne``: written over a usage-origins column it
        reads "used from somewhere other than here". ``value`` may be another
        expression.
        """
        return Compare(self, "any_other_than", value)

    def any_outside(self, prefix: Any) -> Compare:
        """True when this expression's collection holds a dotted name outside ``prefix``.

        The set-valued form of :meth:`is_within`'s negation: "used from
        somewhere outside this package". ``prefix`` may be another expression.
        """
        return Compare(self, "any_outside", prefix)

    def attr(self, name: str) -> Attr:
        """Project attribute ``name`` off this expression's value."""
        return Attr(self, name)

    # -- boolean combinators ----------------------------------------------

    def __and__(self, other: Expr) -> AllOf:
        return AllOf((self, other))

    def __or__(self, other: Expr) -> AnyOf:
        return AnyOf((self, other))

    def __invert__(self) -> Not:
        return Not(self)


@dataclass(frozen=True)
class Const(Expr):
    """A literal value, standing on stated evidence (``DECLARED`` by default)."""

    value: Any
    confidence: Confidence = Confidence.DECLARED

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Return the literal and its stated evidence."""
        return Derivation(op="const", value=self.value, confidence=self.confidence)


@dataclass(frozen=True)
class FieldRead(Expr):
    """A read of one declared model field off the current row.

    Model fields report ``DECLARED``: the model is the record of what the
    binder actually saw. A field the row does not carry reads as ``None`` at
    ``UNKNOWN`` rather than raising — evaluation is total.
    """

    name: str

    @property
    def field_names(self) -> frozenset[str]:
        """The single field this node reads."""
        return frozenset({self.name})

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Read ``name`` off the row, DECLARED when present, UNKNOWN when absent."""
        present = self.name in ctx.fields
        return Derivation(
            op="field",
            value=ctx.fields[self.name] if present else None,
            confidence=Confidence.DECLARED if present else Confidence.UNKNOWN,
            reads=frozenset({FIELD_READ_PREFIX + self.name}),
            detail=MappingProxyType({"field": self.name, "present": present}),
        )


@dataclass(frozen=True)
class TraitRead(Expr):
    """A read of a registered trait: either its value or — the meta-read — its confidence.

    ``projection="value"`` inherits the trait's own confidence.
    ``projection="confidence"`` yields the trait's level *as a value*, on
    ``DECLARED`` evidence: fork #4's meta-read law.
    """

    name: str
    projection: str = "value"

    @property
    def trait_names(self) -> frozenset[str]:
        """The single trait this node reads."""
        return frozenset({self.name})

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Read the trait, applying the meta-read law to the ``confidence`` projection."""
        found = ctx.trait(self.name)
        meta = self.projection == "confidence"
        return Derivation(
            op="trait.confidence" if meta else "trait.value",
            value=found.confidence if meta else found.value,
            confidence=Confidence.DECLARED if meta else found.confidence,
            reads=frozenset({TRAIT_READ_PREFIX + self.name}),
            detail=MappingProxyType({"trait": self.name, "meta_read": meta}),
        )


@dataclass(frozen=True)
class TraitAssertion(Expr):
    """A trait's value, asserted to sit at exactly one confidence level.

    ``trait_of(T).at(INFERRED)`` matches only when ``T``'s confidence *is*
    ``INFERRED``, and yields ``T``'s value when it does. Its own evidence is
    ``DECLARED`` — the expression read ``.confidence``, so the meta-read law
    applies to it directly.

    This is a **primitive leaf**, not sugar over
    ``all_of(trait_of(T).confidence.eq(level), trait_of(T).value)``. Both
    spellings are legal and they deliberately differ: the conjunction meets
    the trait's own level in through its value clause and lands at
    ``INFERRED``, while the assertion states and verifies the level in one
    read and lands at ``DECLARED``. Recovering ``DECLARED`` from the
    conjunction instead would require letting one operand retroactively
    upgrade another — action at a distance in a language whose entire selling
    point is inspectability. Fork #4 states the law about reading
    ``.confidence``; applying it at the leaf that does the reading is the
    smallest thing that satisfies the divergence ledger's ``prefer-tuple``
    entry.
    """

    name: str
    level: Confidence

    @property
    def trait_names(self) -> frozenset[str]:
        """The single trait this node reads."""
        return frozenset({self.name})

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Yield the trait's value when it sits at ``level``, else ``UNMATCHED``."""
        found = ctx.trait(self.name)
        matched = found.confidence is self.level
        return Derivation(
            op="trait.at",
            value=found.value if matched else UNMATCHED,
            confidence=Confidence.DECLARED,
            reads=frozenset({TRAIT_READ_PREFIX + self.name}),
            detail=MappingProxyType({
                "trait": self.name,
                "level": self.level.value,
                "matched": matched,
                "meta_read": True,
            }),
        )


@dataclass(frozen=True)
class Attr(Expr):
    """Attribute projection off another expression's value.

    A missing attribute yields ``None`` at ``UNKNOWN`` rather than raising;
    an ``UNMATCHED`` operand stays ``UNMATCHED``.
    """

    operand: Expr
    name: str

    @property
    def children(self) -> tuple[Expr, ...]:
        """The expression whose value is projected."""
        return (self.operand,)

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Project ``name``, degrading to UNKNOWN when the attribute is absent."""
        # consuming_falsity: the operand's value is projected, not filtered on,
        # so a falsy operand is still load bearing here.
        inner = self.operand.evaluate(ctx.consuming_falsity())
        if inner.value is UNMATCHED:
            value: Any = UNMATCHED
            confidence = inner.confidence
            present = False
        else:
            present = hasattr(inner.value, self.name)
            value = getattr(inner.value, self.name) if present else None
            confidence = inner.confidence if present else Confidence.UNKNOWN
        return Derivation(
            op="attr",
            value=value,
            confidence=confidence,
            inputs=(inner,),
            reads=inner.reads,
            detail=MappingProxyType({"attr": self.name, "present": present}),
        )


@dataclass(frozen=True)
class Compare(Expr):
    """A named comparison against a value, or against another expression.

    ``op`` is one of ``eq``, ``ne``, ``is_in``, ``matches``, ``startswith``,
    ``is_true``, ``is_within``, ``any_other_than``, ``any_outside``. The
    comparison carries its operands' evidence through unchanged — comparing a
    fact does not add or remove evidence about it — which for a two-expression
    comparison means the **meet** of both sides: an answer that rests on two
    reads is no better evidenced than the weaker of them. An ``UNMATCHED``
    value on either side compares false against everything.

    ``rhs`` may itself be an :class:`Expr`, which is what lets a row be
    compared against a project column (``column_of(DEFINITION_MODULE)
    .is_within(row.module)``) rather than against a constant. When it is, it
    appears in :attr:`children`, so reach, field names and trait names are
    derived through it like any other operand — a right-hand side that reaches
    ``PROJECT`` makes the whole comparison reach ``PROJECT``. Leaving it out of
    ``children`` would let an expression declare ``FILE`` reach while
    consulting the resolver, which is precisely the "reach declared, not
    derived" failure this DSL exists to prevent.

    ``is_in`` is the one op that refuses an expression right-hand side: its
    ``rhs`` is the fixed tuple of candidates the author wrote, and an
    expression there would read as "is the value a member of this one value".

    ``matches``, ``startswith`` and ``is_within`` refuse a right-hand side
    that is neither a string (or, for ``startswith``, a tuple of strings) nor
    an expression — a bare non-string literal used to compare silently false
    (or, for a tuple rhs to ``startswith``, silently stopped matching once this
    class started requiring ``str``), which is a construction mistake, not a
    runtime condition.
    """

    operand: Expr
    op: str
    rhs: Any = None

    def __post_init__(self) -> None:
        if self.op not in _COMPARISONS:
            raise ValueError(
                f"unknown comparison {self.op!r}; valid comparisons are: "
                f"{', '.join(sorted(_COMPARISONS))}"
            )
        if self.op in _TUPLE_RHS_COMPARISONS and _holds_expr(self.rhs):
            raise ValueError(
                f"{self.op!r} takes a fixed tuple of candidate values, not an "
                f"expression; write .eq(<expression>) to compare against one "
                f"computed value, or list the candidates literally"
            )
        if (
            self.op in _STRING_RHS_COMPARISONS
            and not isinstance(self.rhs, Expr)
            and not isinstance(self.rhs, str)
        ):
            raise ValueError(
                f"{self.op!r} takes a string right-hand side or an expression "
                f"producing one; got {type(self.rhs).__name__}. Write .eq(<value>) "
                f"to compare a non-string value"
            )
        if (
            self.op in _PREFIX_RHS_COMPARISONS
            and not isinstance(self.rhs, Expr)
            and not _is_prefix_rhs(self.rhs)
        ):
            detail = ""
            if isinstance(self.rhs, tuple):
                bad = next((item for item in self.rhs if not isinstance(item, str)), None)
                detail = f" containing {type(bad).__name__}"
            raise ValueError(
                f"{self.op!r} takes a string prefix, a tuple of string prefixes, "
                f"or an expression producing a string; got "
                f"{type(self.rhs).__name__}{detail}. Write .eq(<value>) to compare "
                f"a non-string value"
            )

    @property
    def children(self) -> tuple[Expr, ...]:
        """The expression being compared, and the right-hand side when it is one."""
        if isinstance(self.rhs, Expr):
            return (self.operand, self.rhs)
        return (self.operand,)

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Apply the comparison, meeting both sides' evidence into the result."""
        # consuming_falsity: ``.eq(False)`` and ``.ne(True)`` turn a rejection
        # into the match, so the operand's falsity is read, not discarded.
        consuming = ctx.consuming_falsity()
        inner = self.operand.evaluate(consuming)
        if isinstance(self.rhs, Expr):
            other = self.rhs.evaluate(consuming)
            inputs = (inner, other)
            rhs_value: Any = other.value
            # JSON-shaped: --why renders ``detail`` as data, so name the kind
            # of node rather than storing the node itself. What it computed is
            # already in the tree, as the second entry of ``inputs``.
            rhs_detail: Any = {"expr": type(self.rhs).__name__}
        else:
            inputs = (inner,)
            rhs_value = self.rhs
            rhs_detail = self.rhs
        return Derivation(
            op=f"compare.{self.op}",
            value=self._apply(inner.value, rhs_value),
            confidence=meet(*(node.confidence for node in inputs)),
            inputs=inputs,
            reads=frozenset().union(*(node.reads for node in inputs)),
            detail=MappingProxyType({"comparison": self.op, "rhs": rhs_detail}),
        )

    def _apply(self, value: Any, rhs: Any) -> bool:
        if value is UNMATCHED or rhs is UNMATCHED:
            return False
        if self.op == "eq":
            return bool(value == rhs)
        if self.op == "ne":
            return bool(value != rhs)
        if self.op == "is_in":
            return value in rhs
        if self.op == "matches":
            return isinstance(value, str) and isinstance(rhs, str) and fnmatchcase(value, rhs)
        if self.op == "startswith":
            return isinstance(value, str) and _is_prefix_rhs(rhs) and value.startswith(rhs)
        if self.op == "is_within":
            return isinstance(value, str) and isinstance(rhs, str) and _within(value, rhs)
        if self.op == "any_other_than":
            return _is_iterable(value) and any(item != rhs for item in value)
        if self.op == "any_outside":
            return isinstance(rhs, str) and _is_iterable(value) and any(
                not (isinstance(item, str) and _within(item, rhs)) for item in value
            )
        return bool(value)


@dataclass(frozen=True)
class AllOf(Expr):
    """Conjunction. Written order, meet over every operand.

    Operands run in the order written and never get reordered (fork #3).
    Evaluation stops at the first falsy one **only when the context licenses
    it** — that is, when :attr:`EvalContext.discards_falsity` says a falsy
    answer drops the row rather than being read. That is the case at a
    selection's filter stage, where the short circuit is observable through an
    opaque's side effects and costs nothing, because the confidence of a
    conjunction that went false is never reported there.

    Everywhere else the conjunction runs every operand, and the reason is
    exactly the one :class:`Opaque` gives for resolving its declared trait
    reads unconditionally. :class:`Not` turns a rejection into the match and
    :class:`Compare` reads the value directly, so under either of them the
    operands a short circuit skipped are real contributions to fork #4's meet
    over *every* contribution. Meeting over a proper prefix instead would
    report ``not_(all_of(declared_false, heuristic_true))`` at ``DECLARED``
    while the swapped spelling reported ``HEURISTIC`` — clause order deciding
    how well evidenced an answer is, which fork #4 forbids, and in the
    direction that over-claims.

    So the confidence is a property of the *set* of operands in every context
    where it can be read, and the evaluation schedule stays written-order in
    all of them.
    """

    operands: tuple[Expr, ...]

    @property
    def children(self) -> tuple[Expr, ...]:
        """The conjoined expressions, in written order."""
        return self.operands

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Evaluate operands left to right, stopping early only if licensed to."""
        evaluated: list[Derivation] = []
        matched = True
        for operand in self.operands:
            # ``ctx`` unchanged: if this conjunction's falsity is discarded then
            # so is any operand's, since one false operand makes the whole
            # conjunction false.
            node = operand.evaluate(ctx)
            evaluated.append(node)
            if not _truthy(node):
                matched = False
                if ctx.discards_falsity:
                    break
        return _combined(
            "all_of",
            value=matched,
            evaluated=tuple(evaluated),
            detail={"short_circuited": len(evaluated) < len(self.operands)},
        )


@dataclass(frozen=True)
class AnyOf(Expr):
    """Disjunction. Written order, no short-circuit, meet over the satisfied branches.

    Every branch is evaluated even after one holds. Short-circuiting would
    make the reported confidence depend on which satisfied branch happened to
    be written first, and fork #4 requires it to be order-independent; a
    disjunction's evidence is a property of the *set* of branches that hold.
    Written order still governs the schedule, so an opaque branch's side
    effects fire in the order written.

    When nothing holds, the value is ``False`` and the confidence is the meet
    over every branch.
    """

    operands: tuple[Expr, ...]

    @property
    def children(self) -> tuple[Expr, ...]:
        """The disjoined expressions, in written order."""
        return self.operands

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Evaluate every branch, then meet over the ones that hold."""
        # ``ctx`` unchanged: a branch that goes false either contributes
        # nothing (some other branch held) or leaves this node false, and a
        # false disjunction is discarded wherever a false conjunction is.
        evaluated = tuple(operand.evaluate(ctx) for operand in self.operands)
        satisfied = tuple(node for node in evaluated if _truthy(node))
        return Derivation(
            op="any_of",
            value=bool(satisfied),
            confidence=meet(*(node.confidence for node in (satisfied or evaluated))),
            inputs=evaluated,
            reads=frozenset().union(*(node.reads for node in evaluated)),
            detail=MappingProxyType({"satisfied": len(satisfied)}),
        )


@dataclass(frozen=True)
class Not(Expr):
    """Negation. Passes its operand's evidence and reads through unchanged.

    Knowing a fact is false is exactly as well-evidenced as knowing it is
    true — the same substrate was read either way.
    """

    operand: Expr

    @property
    def children(self) -> tuple[Expr, ...]:
        """The negated expression."""
        return (self.operand,)

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Negate the operand's truth, carrying its evidence through."""
        # consuming_falsity: the operand going false is precisely what makes
        # this node match, so nothing under it may be skipped as discardable.
        inner = self.operand.evaluate(ctx.consuming_falsity())
        return Derivation(
            op="not",
            value=not _truthy(inner),
            confidence=inner.confidence,
            inputs=(inner,),
            reads=inner.reads,
        )


@dataclass(frozen=True)
class Weaken(Expr):
    """Always matches; contributes ``level`` to the meet when ``pred`` holds.

    The lattice-shaped way to say "report this either way, but label it less
    certain here". Its motivating case is the dynamic-access weakening the
    visibility rules apply: a symbol in a module that calls ``getattr`` may be
    reached by a name the index never saw, so the finding still stands but its
    evidence is a guess. Saying that as a filter would be wrong — the row is
    not dropped — and saying it as a per-rule ``confidence=`` callback would
    move rule semantics out of the expression, where ``--why`` and every
    inventory test can see it, and into plumbing where neither can.

    Three laws it keeps, and how:

    * **It never filters.** :attr:`Derivation.value` is ``True``
      unconditionally, so conjoining one into a rule changes which findings are
      *labelled*, never which rows survive.
    * **It contributes to the meet like any other operand.** When ``pred``
      holds the node reports ``level`` met with the predicate's own evidence;
      when it does not, it reports the predicate's evidence alone. Nothing is
      exempted from fork #4's meet over *every* contribution, including the
      evidence behind the question "does this weakening apply".
    * **It is order-independent.** As one more operand of the enclosing
      conjunction, and with :func:`~pypeeker.dsl.evidence.meet` commutative,
      writing it first or last gives the same confidence.

    ``pred`` is evaluated under :meth:`EvalContext.consuming_falsity`, for the
    reason :class:`Not` gives: ``pred`` going false is a load-bearing outcome
    here, not a discarded one, so no operand under it may be skipped.
    """

    pred: Expr
    level: Confidence

    @property
    def children(self) -> tuple[Expr, ...]:
        """The predicate deciding whether the weakening applies."""
        return (self.pred,)

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Match unconditionally, reporting ``level`` when the predicate holds."""
        inner = self.pred.evaluate(ctx.consuming_falsity())
        applied = _truthy(inner)
        return Derivation(
            op="weaken",
            value=True,
            confidence=meet(inner.confidence, self.level) if applied else inner.confidence,
            inputs=(inner,),
            reads=inner.reads,
            detail=MappingProxyType({"weakened_to": self.level.value, "applied": applied}),
        )


@dataclass(frozen=True)
class Opaque(Expr):
    """A named predicate whose body the DSL cannot see, but whose reads are declared.

    Fork #9's escape hatch. ``where()`` rejects a bare callable outright; this
    is the sanctioned way to smuggle arbitrary Python in, and the price is a
    mandatory ``reads=`` declaration. That declaration does three jobs at
    once: it derives the node's reach (a ``project:`` token forces
    ``PROJECT``), it derives the node's evidence (a ``trait:`` token
    contributes that trait's confidence, every other token contributes
    ``DECLARED``), and it is what ``--why`` reports in place of a body it
    cannot inspect.

    Declared trait reads are resolved and met **whether or not the body
    held**. The tempting optimization — skip the traits for a row the body
    rejected, since a conjunction containing it already went false — is only
    sound for a node whose falsity is never consumed, and no node here has
    that property: :class:`Not` turns a rejection into the match, and
    :class:`Compare` reads the value directly. Skipping the resolution would
    drop a real operand out of fork #4's meet over *every* contribution, so
    ``not_(opaque_resting_on_a_HEURISTIC_trait)`` would claim ``DECLARED``
    for a fact that rests on a guess — and the ``--why`` node would name the
    read while reporting evidence as though it had not made it, which is
    undetectable downstream. ``reads=`` declares what the body *may* read,
    and the meet is the pessimistic operation: a declared read counts
    against the evidence on both branches.
    """

    name: str
    fn: Callable[[Any], Any]
    reads: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_reads(self.name, self.reads)

    @property
    def reach(self) -> Reach:
        """``PROJECT`` when any declared read is prefixed ``project:``, else ``FILE``."""
        return Reach.PROJECT if any(
            token.startswith(PROJECT_READ_PREFIX) for token in self.reads
        ) else Reach.FILE

    @property
    def field_names(self) -> frozenset[str]:
        """Declared field reads, with any prefix stripped."""
        return frozenset(
            token.removeprefix(PROJECT_READ_PREFIX)
            for token in self.reads
            if not token.removeprefix(PROJECT_READ_PREFIX).startswith(TRAIT_READ_PREFIX)
        )

    @property
    def trait_names(self) -> frozenset[str]:
        """Declared trait reads, with any prefix stripped."""
        return frozenset(
            token.removeprefix(PROJECT_READ_PREFIX).removeprefix(TRAIT_READ_PREFIX)
            for token in self.reads
            if token.removeprefix(PROJECT_READ_PREFIX).startswith(TRAIT_READ_PREFIX)
        )

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Run the body, then meet every declared read's evidence into the result."""
        value = self.fn(ctx.row)
        # Unconditional: see the class docstring. A rejected opaque's falsity
        # is consumed by not_() and by .eq(False), so its evidence is load
        # bearing on both branches.
        confidence = meet(
            *(ctx.trait(name).confidence for name in sorted(self.trait_names))
        )
        return Derivation(
            op="opaque",
            value=value,
            confidence=confidence,
            reads=frozenset(self.reads),
            detail=MappingProxyType({
                "name": self.name,
                "declared_reads": list(self.reads),
            }),
        )


def _validate_reads(name: str, reads: Sequence[str]) -> None:
    """Reject an opaque whose ``reads=`` declaration is missing or malformed."""
    if not reads:
        raise OpaquePredicateError(
            f"opaque predicate {name!r} declares no reads; reads= must name at least "
            f"one thing the body looks at, as 'field-name', "
            f"'{TRAIT_READ_PREFIX}<trait-name>', or '{PROJECT_READ_PREFIX}<name>' for a "
            f"read that leaves the file. An opaque with no declared reads is not "
            f"inspectable, which is the only thing making the escape hatch acceptable."
        )
    for token in reads:
        if not isinstance(token, str) or not token.strip():
            raise OpaquePredicateError(
                f"opaque predicate {name!r} declares a non-string or empty read "
                f"{token!r}; every reads= token must be a non-empty string."
            )
        bare = token.removeprefix(PROJECT_READ_PREFIX)
        if not bare or (bare.startswith(TRAIT_READ_PREFIX)
                        and not bare.removeprefix(TRAIT_READ_PREFIX)):
            raise OpaquePredicateError(
                f"opaque predicate {name!r} declares the read {token!r} with a prefix "
                f"but no name; valid forms are 'field-name', "
                f"'{TRAIT_READ_PREFIX}<trait-name>', '{PROJECT_READ_PREFIX}<name>'."
            )


def _combined(
    op: str,
    *,
    value: Any,
    evaluated: tuple[Derivation, ...],
    detail: Mapping[str, Any],
) -> Derivation:
    """Build a combinator node meeting over ``evaluated``."""
    return Derivation(
        op=op,
        value=value,
        confidence=meet(*(node.confidence for node in evaluated)),
        inputs=evaluated,
        reads=frozenset().union(*(node.reads for node in evaluated)),
        detail=MappingProxyType(dict(detail)),
    )


class _Row:
    """The field-read proxy. ``row.kind`` is :class:`FieldRead`\\ ``("kind")``.

    Field names are deliberately *not* validated here: the proxy has no idea
    which universe an expression will end up attached to. Validation happens
    where the universe is known — when the expression is handed to a
    selection's ``where()``, or to :func:`pypeeker.dsl.trait` — and names the
    valid fields when it fails.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> FieldRead:
        if name.startswith("_"):
            raise AttributeError(name)
        return FieldRead(name)

    def __repr__(self) -> str:
        return "<pypeeker.dsl.row>"


row = _Row()
"""The field-read proxy: ``row.name``, ``row.kind``, ``row.file_path``."""


@dataclass(frozen=True)
class TraitAccess:
    """What :func:`trait_of` returns: the three ways to read one trait.

    ``.value`` — the trait's value, on the trait's own evidence.
    ``.confidence`` — the trait's level as a value, on ``DECLARED`` evidence
    (the meta-read law).
    ``.at(level)`` — the trait's value, asserted to sit at ``level``, on
    ``DECLARED`` evidence.
    """

    name: str

    @property
    def value(self) -> TraitRead:
        """Read the trait's value, inheriting the trait's own confidence."""
        return TraitRead(self.name, "value")

    @property
    def confidence(self) -> TraitRead:
        """Read the trait's confidence level as a value. A DECLARED meta-read."""
        return TraitRead(self.name, "confidence")

    def at(self, level: Confidence) -> TraitAssertion:
        """Read the trait's value, matching only when its confidence is ``level``."""
        return TraitAssertion(self.name, level)


def trait_of(name: str) -> TraitAccess:
    """Open the three read projections of the trait registered under ``name``."""
    return TraitAccess(name)


def opaque(name: str, *, reads: Iterable[str]) -> Callable[[Callable[[Any], Any]], Opaque]:
    """Wrap a Python predicate into a named, reads-declaring :class:`Opaque` (decorator).

    ``reads`` is keyword-only and has **no default**: omitting it is a
    ``TypeError`` and an empty one is an
    :class:`~pypeeker.dsl.errors.OpaquePredicateError`. Fork #9 permits
    opacity only where it is declared.

    Args:
        name: Stable name for the predicate, reported by ``--why``.
        reads: What the body looks at — ``'field-name'``,
            ``'trait:<trait-name>'``, or a ``'project:'``-prefixed token for a
            read that leaves the file (which forces ``PROJECT`` reach).

    Returns:
        A decorator turning the predicate into an :class:`Opaque` node.
    """
    tokens = tuple(reads)

    def _decorate(fn: Callable[[Any], Any]) -> Opaque:
        return Opaque(name=name, fn=fn, reads=tokens)

    _validate_reads(name, tokens)
    return _decorate


def all_of(*operands: Expr) -> AllOf:
    """Conjoin ``operands``, evaluated in written order."""
    return AllOf(tuple(operands))


def any_of(*operands: Expr) -> AnyOf:
    """Disjoin ``operands``; every branch is evaluated."""
    return AnyOf(tuple(operands))


def not_(operand: Expr) -> Not:
    """Negate ``operand``, carrying its evidence through."""
    return Not(operand)


def weakened_when(pred: Expr, level: Confidence) -> Weaken:
    """Report ``level`` where ``pred`` holds, without filtering anything out.

    Args:
        pred: the condition under which the surrounding answer is less well
            evidenced.
        level: the confidence to meet in where ``pred`` holds.

    Returns:
        A :class:`Weaken` node, always truthy, to conjoin into a selection.
    """
    return Weaken(pred, level)
