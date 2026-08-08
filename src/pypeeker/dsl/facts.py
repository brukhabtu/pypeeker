"""The primitive fact tier: hand-written sweeps the expression grammar cannot express.

Fork #11 of ``dsl-rewrite.md`` settles that fixpoint- and graph-shaped
computations — cycle detection, purity propagation — stay **hand-written Python
declaring its own confidence**, at the same tier as the model-reading
primitives, and that the DSL rule then quantifies over the result. This module
is the mechanism for that tier. It knows no concrete fact; the library of
actual sweeps lives beside it.

Why not ``analysis.traits``
---------------------------

A trait provider is called as ``(FileIndex, symbol_id) -> Trait``, which is why
:func:`pypeeker.dsl.trait` refuses a ``PROJECT``-reach expression outright:
architecture.md's trait-promotion clause (b) says a fact needing the store or
the cross-module resolver does not fit that signature. The facts here need
exactly those. They are therefore computed from a :class:`~pypeeker.dsl.Corpus`
and memoized on it, and the table is a **private, per-run** structure rather
than an entry in the process-global trait registry:

* Promotion rule (a) reserves that registry for facts a refactor precondition
  verifies pointwise. Nothing verifies "which SCC is this module in".
* The registry is last-registration-wins by design, so publishing a second
  overridable seam would let a consumer silently change what a rule *means*,
  and would make two corpora in one process race for one table.

What a fact is worth
--------------------

The value is a :class:`~pypeeker.analysis.Trait`, so a primitive declares its
own confidence exactly as fork #11 requires — the sweep is the only thing that
knows whether its answer rests on a static import or a recovered one.
:class:`FactRead` lifts out ``.value`` or ``.confidence`` and never puts the
``Trait`` itself into a derivation, which is the same discipline
:class:`~pypeeker.dsl.TraitRead` follows and for the same reason: a trait's
``provenance`` is unstructured debugging prose that must never reach output.

Evaluation stays total. A fact whose table holds no entry for this row's anchor
yields ``None`` at ``UNKNOWN``, so predicates over it go false. The one loud
case is a fact read with no corpus behind it at all
(:class:`~pypeeker.dsl.UnknownFactError`), which is a wiring mistake rather
than a gap in the data.

Row sources
-----------

:func:`fact_source` is the tier's row-producing form, the sibling of a
primitive fact's value-producing form. It exists for the shape the five
universes cannot supply: a rule whose ∀ ranges over something that is **not one
file**. Two kinds of that turn up in the first port:

* the project's **configuration** — ``import-boundaries``'
  ``report-unused-allowances``, where "this allow pair is never exercised" is a
  finding with no model row behind it at all, anchored at ``pyproject.toml``;
* a **derived aggregate over files** — a top-level package
  (``import-boundaries``' strict census) or a strongly-connected component of
  the import graph (``no-import-cycles``). Both look close enough to the
  ``modules`` universe to tempt a fact keyed on a module id, and that is a trap:
  a modules row is one *file*, and ``paths.module_path_from`` maps
  ``proj/dup.py`` and ``proj/dup/__init__.py`` — or two roots of a multi-root
  ``src`` — onto one module id, so such a fact fires once per colliding file.
  One row per aggregate is the only shape that cannot.

Fork #8 fixes the **model** universes at five, and this module adds none: the
five in :mod:`pypeeker.dsl.universes` are untouched, ``UNIVERSE_NAMES`` still
names exactly them, and a fact source is never reachable by name through
:func:`~pypeeker.dsl.universe_fields`. It is a row source the primitive tier
builds and hands straight to a :class:`~pypeeker.dsl.Selection`, so that the
*selection* over those rows still lives in the DSL and only the sweep is
hand-written.

**The kind is the call site's decision, not the mechanism's.** A fact source
must give its rows an :class:`~pypeeker.dsl.AnchorKind`, whose docstring says
"one member per universe". An allowance pair is none of the five. Rather than
mint a sixth member — which would concede by the code's own taxonomy that this
is a sixth universe — :func:`fact_source` takes the kind as a required
argument, so the choice is made and defended at the one call site that needs it
rather than built into the mechanism.

All three call sites in :mod:`pypeeker.dsl.sweeps` answered
``AnchorKind.MODULE``, and two of the three answered it easily:
:func:`~pypeeker.dsl.sweeps.unit_rows` anchors a package on its representative
file's module id and :func:`~pypeeker.dsl.sweeps.cycle_rows` anchors a
component on its reporting module id — both real, locatable modules, and both
the same anchor the corresponding modules row would have carried. The hard one
is :func:`~pypeeker.dsl.sweeps.allowance_rows`, whose anchor id is
``allowance:<importer>-><dep>``: both halves of that id name module namespaces,
and the prefix makes it structurally unmistakable for a locatable module. The
full defence, and the two rejected alternatives, are in
:mod:`pypeeker.dsl.sweeps`' module docstring. The argument stays required: that
three call sites agreed is not a reason to make one of them a default.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from pypeeker.analysis import Trait
from pypeeker.dsl.anchors import Anchor, AnchorKind
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import UnknownFactError
from pypeeker.dsl.evidence import Derivation
from pypeeker.dsl.expr import FACT_READ_PREFIX, PROJECT_READ_PREFIX, EvalContext, Expr
from pypeeker.dsl.reach import Reach
from pypeeker.dsl.universes import _Record, _Universe
from pypeeker.models import Confidence


class FactTable(Protocol):
    """What a fact's sweep produces: an answer per anchor id.

    ``get`` returns ``None`` for an anchor the sweep has nothing to say about,
    which is an answer rather than a failure — a plain ``dict[str, Trait]``
    satisfies this protocol as written.
    """

    def get(self, anchor_id: str) -> Trait | None:
        """The trait this sweep holds for ``anchor_id``, or ``None``."""
        ...


@dataclass(frozen=True)
class Fact:
    """A primitive fact: a named sweep, its honest reach, and how to run it.

    ``name`` is the fact's identity. It is what a :class:`FactRead` names, what
    a derivation reports, and what the per-corpus memo keys on — so two specs
    sharing a name are the same fact as far as everything downstream is
    concerned.

    ``reach`` is declared here, next to the code that consults the substrate,
    for the same reason a follow step declares its own: it is the *only* input
    to :attr:`pypeeker.dsl.Selection.reach` for an expression reading this
    fact, so declaring it anywhere else would let the derived reach and the
    actual substrate use drift apart silently.

    ``compute`` is called at most once per ``(name, params)`` per corpus.
    """

    name: str
    reach: Reach
    compute: Callable[[Corpus, Any], FactTable]


@dataclass(frozen=True)
class _MappingTable:
    """A fact table that was computed eagerly, in full."""

    entries: Mapping[str, Trait]

    def get(self, anchor_id: str) -> Trait | None:
        """The trait held for ``anchor_id``, or ``None``."""
        return self.entries.get(anchor_id)


class _LazyTable:
    """A fact table computed per anchor on first ask, then memoized.

    For a sweep whose per-anchor cost is high and whose anchors are mostly
    filtered out before anyone asks — purity, whose transitive call-graph walk
    is only worth paying for a symbol the cheap clauses already accepted.

    Memoized rather than merely lazy: a rule typically reads the same fact more
    than once for one row (once to filter, once to word the message), and a
    table that recomputed each time would pay the walk twice for every row it
    reports.
    """

    __slots__ = ("_compute", "_cache")

    def __init__(self, compute: Callable[[str], Trait | None]) -> None:
        self._compute = compute
        self._cache: dict[str, Trait | None] = {}

    def get(self, anchor_id: str) -> Trait | None:
        """The trait held for ``anchor_id``, computing it on first ask."""
        if anchor_id not in self._cache:
            self._cache[anchor_id] = self._compute(anchor_id)
        return self._cache[anchor_id]


def mapping_table(entries: Mapping[str, Trait]) -> FactTable:
    """Wrap an eagerly-computed ``{anchor id: Trait}`` mapping as a fact table."""
    return _MappingTable(MappingProxyType(dict(entries)))


def lazy_table(compute: Callable[[str], Trait | None]) -> FactTable:
    """Build a fact table that computes each anchor's answer on first ask, then keeps it."""
    return _LazyTable(compute)


@dataclass(frozen=True)
class FactRead(Expr):
    """A read of one primitive fact: its value, or — the meta-read — its confidence.

    ``projection="value"`` inherits the fact's own confidence, which is the
    sweep's declaration about its own answer. ``projection="confidence"``
    yields that level as a value on ``DECLARED`` evidence: fork #4's meta-read
    law, applied at the leaf that does the reading, exactly as
    :class:`~pypeeker.dsl.TraitRead` applies it.

    A fact with no entry for this row's anchor reads as ``None`` at
    ``UNKNOWN`` under **both** projections. The meta-read law says the level
    the sweep recorded is itself a declared fact; where the sweep recorded no
    level there is nothing declared to report.

    ``params`` must be hashable. Every expression node is a frozen dataclass so
    that nodes are hashable, structurally comparable and safe to share between
    selections; a node holding a ``dict`` or a ``set`` would break that
    invariant everywhere downstream, so it is refused at construction rather
    than discovered as a ``TypeError`` from inside a memo key.
    """

    spec: Fact
    params: Any = None
    projection: str = "value"

    def __post_init__(self) -> None:
        try:
            hash(self.params)
        except TypeError as exc:
            raise TypeError(
                f"fact_of({self.spec.name!r}) params must be hashable, got "
                f"{type(self.params).__name__}: expression nodes are frozen and shared, "
                f"and the params are part of the per-corpus memo key. Normalize a dict "
                f"or a set into a sorted tuple first."
            ) from exc

    @property
    def reach(self) -> Reach:
        """The sweep's declared reach; a fact has no children to derive one from."""
        return self.spec.reach

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Read the fact for this row's anchor, applying the meta-read law to ``confidence``."""
        found = ctx.fact(self.spec.name, self.params)
        meta = self.projection == "confidence"
        if found is None:
            value: Any = None
            confidence = Confidence.UNKNOWN
        elif meta:
            value = found.confidence
            confidence = Confidence.DECLARED
        else:
            value = found.value
            confidence = found.confidence
        return Derivation(
            op="fact.confidence" if meta else "fact.value",
            value=value,
            confidence=confidence,
            reads=frozenset({_read_token(self.spec)}),
            detail=MappingProxyType({
                "fact": self.spec.name,
                "meta_read": meta,
                "present": found is not None,
            }),
        )


def _read_token(spec: Fact) -> str:
    """The ``Derivation.reads`` token for a read of ``spec``.

    ``PROJECT``-reach facts carry the ``project:`` prefix an opaque would have
    had to declare by hand, so a reader of the derivation tree can tell which
    reads left the file without cross-referencing the spec.
    """
    bare = FACT_READ_PREFIX + spec.name
    return PROJECT_READ_PREFIX + bare if spec.reach is Reach.PROJECT else bare


@dataclass(frozen=True)
class FactAccess:
    """What :func:`fact_of` returns: the two ways to read one primitive fact.

    ``.value`` — the sweep's answer, on the evidence the sweep declared.
    ``.confidence`` — that evidence as a value, on ``DECLARED`` evidence.

    There is no ``.at(level)``. :class:`~pypeeker.dsl.TraitAssertion` exists to
    let a rule state and verify a level in one read; a primitive fact declares
    its own confidence from inside the sweep, so an assertion about it would be
    a rule second-guessing the only code that knows.
    """

    spec: Fact
    params: Any = None

    @property
    def value(self) -> FactRead:
        """Read the fact's value, inheriting the confidence the sweep declared."""
        return FactRead(self.spec, self.params, "value")

    @property
    def confidence(self) -> FactRead:
        """Read the fact's confidence level as a value. A DECLARED meta-read."""
        return FactRead(self.spec, self.params, "confidence")


def fact_of(spec: Fact, params: Any = None) -> FactAccess:
    """Open the read projections of the primitive fact ``spec``, computed with ``params``.

    Args:
        spec: the fact's declaration — its name, its reach, and its sweep.
        params: hashable parameters the sweep needs, typically the slice of the
            rule's option table it depends on. Part of the memo key, so two
            reads with equal params share one computation and two with
            different params do not.

    Returns:
        A :class:`FactAccess` exposing ``.value`` and ``.confidence``.
    """
    return FactAccess(spec, params)


def fact_specs(expr: Expr) -> tuple[Fact, ...]:
    """Every :class:`Fact` an expression reads, in name order, deduplicated.

    Walks the node tree the way :mod:`pypeeker.dsl.selection` walks it for
    field reads, rather than adding a ``fact_specs`` property to
    :class:`~pypeeker.dsl.Expr`: the base class lives in a module this one
    imports, so the property would need the spec type it cannot see.
    """
    found: dict[str, Fact] = {}
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, FactRead):
            found.setdefault(node.spec.name, node.spec)
        stack.extend(node.children)
    return tuple(found[name] for name in sorted(found))


class _FactLookup:
    """The fact tables one selection run may consult, memoized on the corpus.

    Built once per :meth:`pypeeker.dsl.Selection.rows` call from the specs the
    selection's expressions name, and narrowed to a single row by
    :meth:`for_anchor`. The narrowing is what lets a :class:`FactRead` stay a
    pure value built at rule-build time while still answering per row.
    """

    __slots__ = ("_corpus", "_specs")

    def __init__(self, corpus: Corpus, specs: Iterable[Fact]) -> None:
        self._corpus = corpus
        self._specs: Mapping[str, Fact] = {spec.name: spec for spec in specs}

    def table(self, name: str, params: Any) -> FactTable:
        """The table for ``(name, params)``, computed once per corpus."""
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownFactError(name, self._specs)
        return self._corpus.memo(
            ("fact", name, params), lambda: spec.compute(self._corpus, params)
        )

    def for_anchor(self, anchor_id: str) -> Callable[[str, Any], Trait | None]:
        """A :class:`~pypeeker.dsl.FactResolver` bound to one row's anchor."""

        def resolve(name: str, params: Any) -> Trait | None:
            return self.table(name, params).get(anchor_id)

        return resolve


@dataclass(frozen=True)
class FactRow:
    """One row of a :func:`fact_source`: what it is about, and what it exposes.

    ``anchor_id`` identifies the row for baseline keying and for ``--why``;
    ``fields`` are what the selection's expressions and the rule's message
    template see; ``evidence`` is what the row is intrinsically worth, joining
    the meet without any expression asking for it.
    """

    anchor_id: str
    fields: Mapping[str, Any]
    evidence: Confidence = field(default=Confidence.DECLARED)


def fact_source(
    name: str,
    fields: tuple[str, ...],
    rows: Callable[[Corpus], Iterable[FactRow]],
    *,
    anchor_kind: AnchorKind,
) -> _Universe:
    """Build a row source over something the model does not hold.

    The primitive tier's row-producing form. Use it only where the rows are
    genuinely not model rows — the motivating case is a finding about the
    project's *configuration*, which no index records. Anything derivable from
    an index belongs in one of the five universes fork #8 fixes.

    The returned row source declares **no follow steps**. Its rows carry no
    :class:`~pypeeker.dsl.universes._Env`, so there is no file to navigate out
    of and no trait to resolve against; a step would have nothing to walk.

    Args:
        name: the row source's name, for ``repr`` and for error messages. It is
            deliberately **not** registered anywhere: ``UNIVERSE_NAMES`` stays
            the five, and this name never resolves through
            :func:`~pypeeker.dsl.universe_fields`.
        fields: the field names a row publishes, declared rather than
            discovered, exactly as a universe declares them.
        rows: called once with the corpus, yielding one :class:`FactRow` per
            row in the order they should be reported.
        anchor_kind: the anchor kind these rows carry. Required, and chosen by
            the caller: see this module's docstring on why the mechanism
            refuses to decide.

    Returns:
        A row source a :class:`~pypeeker.dsl.Selection` can be built over.
    """

    def build(corpus: Corpus) -> Iterator[_Record]:
        for produced in rows(corpus):
            yield _Record(
                universe=name,
                anchor=Anchor(anchor_kind, produced.anchor_id, produced.evidence),
                fields=dict(produced.fields),
                evidence=produced.evidence,
                env=None,
                source=produced,
                trait_anchor=produced.anchor_id,
            )

    return _Universe(
        name=name,
        anchor_kind=anchor_kind,
        fields=tuple(fields),
        follows={},
        corpus_rows=build,
    )
