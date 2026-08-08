"""Semi-joins: testing a row's key against one corpus-wide projected column.

``dsl-rewrite.md``'s panel convergences include "**the barrel exemption is a
semi-join on one projected id column, materialized once per run**". This module
is that sentence made literal, and generalized to the whole shape it belongs
to: a set of ids computed once by a selection over the whole corpus, and a
pointwise membership test against it.

Three pieces:

* :class:`ProjectedSet` — a selection projected down to exactly one column,
  whose values are that column over every row. Refused loudly at construction
  if the selection is not projected to one column, because a set built from a
  two-column selection has no defensible key.
* :class:`CorpusSet` — the same right-hand side for a set that does **not**
  come from the model: a baseline file, a configuration list. It declares what
  it reads rather than deriving it, in the same spirit as
  :class:`~pypeeker.dsl.Opaque`'s mandatory ``reads=``.
* :class:`SemiJoin` — the ``in_set(key, rhs)`` node itself.

Materialized once per run
-------------------------

Both set kinds resolve through :meth:`pypeeker.dsl.Corpus.memo`, keyed
**structurally** — on the universe and stage list the set is computed from, not
on its name. That matters in both directions. Five rules sharing one
``BARREL_EXPORTS`` constant pay for one scan, not five. And two sets that
happen to be given the same name but describe different selections get
different cache entries instead of silently sharing one, which is the failure
mode a name-keyed memo has and cannot report. The name is a label for ``--why``
and for the declared read token; it is never the identity.

The evidence law, and why it is this one
----------------------------------------

**A semi-join contributes only the key expression's confidence. The set itself
is ``DECLARED``.**

That is a decision, not a derivation, so here is the argument. Membership in a
projected column is a derived fact about the model *as a whole* — "the project
re-exports this id somewhere" — and the evidence for it is the evidence that
the model records what it records, which is ``DECLARED``, the same level
:class:`~pypeeker.dsl.FieldRead` reports and the same level a
``follow("definition")`` step's target row carries. What the row contributes is
its *key*: if the id being tested was itself read on weak evidence, the answer
is weak, and that flows in through :attr:`SemiJoin.key`.

The alternative — meeting in the evidence of every row that built the set —
was rejected on two grounds. It would make a finding's confidence depend on
unrelated code: one dynamically-recovered ``importlib`` re-export anywhere in
any ``__init__.py`` would drag every unused-public-symbol finding in the
project down to ``HEURISTIC``, including findings about files that import
nothing. And it would diverge from the frozen engine, whose barrel exemption is
a plain set-membership test with no confidence attached. Confidence is supposed
to describe how well founded *this* answer is; smearing a whole corpus's
evidence across every row makes it describe the corpus instead.

The same law applies to :class:`CorpusSet`, for the narrower reason that a
recorded baseline is a fact about a file on disk, and "the file says this id"
is as declared as facts get.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import DslError
from pypeeker.dsl.evidence import Derivation
from pypeeker.dsl.expr import PROJECT_READ_PREFIX, UNMATCHED, EvalContext, Expr
from pypeeker.dsl.reach import Reach
from pypeeker.dsl.selection import Selection

SET_READ_PREFIX = PROJECT_READ_PREFIX + "set:"
"""Prefix of the declared read token a semi-join reports for its right-hand side."""


@dataclass(frozen=True)
class ProjectedSet:
    """One corpus-wide column of values, described by a selection.

    Args:
        name: a stable label, reported by ``--why`` and used in the declared
            read token. Not the cache identity — see the module docstring.
        selection: a selection whose visible fields are exactly one column.

    Raises:
        DslError: ``selection`` is not projected down to a single column. A set
            has one key; a selection still exposing five fields has not said
            which of them it is, and guessing would make the set's meaning
            depend on field ordering.
    """

    name: str
    selection: Selection

    def __post_init__(self) -> None:
        visible = self.selection.fields
        if len(visible) != 1:
            raise DslError(
                f"projected set {self.name!r} needs a selection projected to exactly "
                f"one column, but {len(visible)} are visible: "
                f"{', '.join(sorted(visible)) or '(none)'}. Add "
                f".project('<column>') naming the key this set is keyed on.",
                set=self.name,
                visible=sorted(visible),
            )

    @property
    def column(self) -> str:
        """The single field name this set's values come from."""
        return next(iter(self.selection.fields))

    @property
    def cache_key(self) -> Hashable:
        """Structural identity of this set, for :meth:`pypeeker.dsl.Corpus.memo`.

        Derived from what the set is *computed from* — its universe and its
        stage list, predicates included — rather than from :attr:`name`, so two
        independently-built descriptions of the same column share one scan and
        two different columns never share a cached value.
        """
        return ("dsl.joins.ProjectedSet", _structure(self.selection))

    def values(self, corpus: Corpus) -> frozenset[Any]:
        """Every value of :attr:`column` over ``corpus``, computed once per run.

        Args:
            corpus: the substrate to scan.

        Returns:
            The column as a frozen set. Rows whose column value is ``None``
            contribute nothing — a row with no key is not a key.
        """
        return corpus.memo(self.cache_key, lambda: self._compute(corpus))

    def _compute(self, corpus: Corpus) -> frozenset[Any]:
        """Run the selection and collect its one column."""
        column = self.column
        return frozenset(
            value
            for value in (match.fields.get(column) for match in self.selection.rows(corpus))
            if value is not None
        )


@dataclass(frozen=True)
class CorpusSet:
    """A set of keys loaded from outside the model, declaring what it reads.

    The escape hatch of this module, and priced like
    :class:`~pypeeker.dsl.Opaque`: the loader is arbitrary Python, so it has to
    say what it looks at. The motivating case is a recorded baseline of
    symbol ids on disk, which no selection over the five universes can produce
    because it is not a fact about the code.

    Args:
        name: stable label, reported by ``--why``.
        reads: what the loader looks at, e.g. ``("baseline:symbols",)``.
        load: builds the set from the corpus. Called at most once per run.
    """

    name: str
    reads: tuple[str, ...]
    load: Callable[[Corpus], Iterable[Any]]

    def __post_init__(self) -> None:
        if not self.reads:
            raise DslError(
                f"corpus set {self.name!r} declares no reads; reads= must name at "
                f"least one thing the loader looks at. A set loaded from outside "
                f"the model is opaque, and an opaque with no declared reads is not "
                f"inspectable.",
                set=self.name,
            )

    @property
    def cache_key(self) -> Hashable:
        """Structural identity: the name plus the loader, which *is* the definition."""
        return ("dsl.joins.CorpusSet", self.name, self.load)

    def values(self, corpus: Corpus) -> frozenset[Any]:
        """The loaded set, computed once per run.

        Args:
            corpus: passed to the loader, which may read the store behind it.

        Returns:
            The loaded keys as a frozen set.
        """
        return corpus.memo(self.cache_key, lambda: frozenset(self.load(corpus)))


_SetSource = ProjectedSet | CorpusSet


@dataclass(frozen=True)
class SemiJoin(Expr):
    """Membership of one row's key in a corpus-wide set. ``in_set(key, rhs)``.

    Reach is ``PROJECT`` unconditionally, and not derived from the key: the
    right-hand side is a scan over every index in the corpus, so the node has
    left the file whatever the key happens to read. Declaring it here is not a
    hole in "reach is derived" — the node itself *is* the project-reaching
    operation, in the same way a ``follow`` step declares its cost next to the
    code that pays it.

    Evidence is the key's, and the module docstring argues why.
    """

    key: Expr
    rhs: _SetSource

    @property
    def children(self) -> tuple[Expr, ...]:
        """The key expression being tested."""
        return (self.key,)

    @property
    def reach(self) -> Reach:
        """``PROJECT``, always: the right-hand side is a corpus-wide scan."""
        return Reach.PROJECT

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Test the key against the set, carrying the key's evidence through."""
        corpus = ctx.require_corpus(f"in_set(..., {self.rhs.name!r})")
        # consuming_falsity: a falsy key is still a key — an empty string is
        # simply not a member — so nothing under it may be skipped.
        inner = self.key.evaluate(ctx.consuming_falsity())
        values = self.rhs.values(corpus)
        return Derivation(
            op="in_set",
            value=_member(inner.value, values),
            confidence=inner.confidence,
            inputs=(inner,),
            reads=inner.reads | {SET_READ_PREFIX + self.rhs.name},
            detail=MappingProxyType({"set": self.rhs.name, "size": len(values)}),
        )


def _member(value: Any, values: frozenset[Any]) -> bool:
    """Membership, total: an ``UNMATCHED`` or unhashable key is simply not a member.

    Evaluation is total everywhere else in the grammar, and a key that cannot
    be hashed is a fact about that row rather than an authoring mistake the
    evaluator can name — so it goes false rather than raising.
    """
    if value is UNMATCHED:
        return False
    try:
        return value in values
    except TypeError:
        return False


def _structure(selection: Selection) -> Hashable:
    """The hashable structural identity of ``selection``: its universe and its stages.

    Reaches for the selection's internals rather than its ``repr``, which
    renders stage *names* and would make two selections with different
    predicates look identical — exactly the collision this key exists to
    prevent. Stages are frozen dataclasses holding frozen expression nodes, so
    the tuple is hashable and compares structurally.
    """
    return (selection._universe.name, selection._stages)


def projected_set(name: str, selection: Selection) -> ProjectedSet:
    """Describe a corpus-wide column as a set. See :class:`ProjectedSet`."""
    return ProjectedSet(name=name, selection=selection)


def corpus_set(
    name: str, reads: Iterable[str], load: Callable[[Corpus], Iterable[Any]]
) -> CorpusSet:
    """Describe a set loaded from outside the model. See :class:`CorpusSet`."""
    return CorpusSet(name=name, reads=tuple(reads), load=load)


def in_set(key: Expr, rhs: _SetSource) -> SemiJoin:
    """True when ``key``'s value is in ``rhs``.

    Args:
        key: the expression producing this row's key.
        rhs: a :class:`ProjectedSet` or :class:`CorpusSet`.

    Returns:
        A :class:`SemiJoin` node, reaching ``PROJECT``.
    """
    return SemiJoin(key=key, rhs=rhs)
