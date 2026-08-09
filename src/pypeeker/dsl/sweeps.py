"""The library of primitive-tier sweeps: hand-written passes the grammar cannot express.

:mod:`pypeeker.dsl.facts` is the *mechanism* for fork #11's tier — ``Fact``,
``mapping_table``, ``lazy_table``, ``fact_source``. This module is the
*library* that mechanism exists for: the concrete sweeps, each declaring its
own confidence, each memoized on the corpus, each exposed to the DSL as a fact
or as a row source so the rule that consumes it stays a selection.

Why these computations are not expressions
------------------------------------------

Fork #11 settles that a fixpoint- or graph-shaped computation stays
hand-written Python. The expression grammar evaluates **one row at a time**:
:class:`~pypeeker.dsl.Expr` sees a single :class:`~pypeeker.dsl.EvalContext`
holding that row's fields, traits and facts, and there is no aggregate, no
join and no recursion in it — deliberately, because fork #3 forbids an
optimizer and an aggregate would need one to be affordable. Every sweep here
answers a question about a row that can only be settled by looking at *every
other* row first:

* the judged-import rows charge an import against the package that **actually
  defines** the imported name, which means resolving through the whole
  project's re-export chains before any single import can be judged.
* ``undeclared-unit`` reports one finding per top-level package, anchored on a
  representative file chosen by comparing every file of that package against
  every other. A per-row predicate cannot choose "the first ``__init__.py`` in
  path-sorted order" — by the time it sees a row, the comparison is over.
* the unused-allowance rows are not about code at all (see below).
* ``import-cycle`` is the case fork #11 names outright: strongly-connected
  components are a graph fixpoint, and no per-row expression converges.
* ``impurity`` is the other one: purity propagates transitively along the call
  graph, so whether *this* function is impure depends on a walk over everything
  it can reach.

The boundary is therefore not "hard code goes here": it is that the sweep does
the **quantifier-collapsing** work, and the DSL keeps the ∀. What each rule
*selects* and how it *words* a finding stays in :mod:`pypeeker.dsl.rules`,
where it is inspectable; only the collapse is opaque, and it is opaque behind a
named fact with a declared reach.

Confidence is declared once, by whoever knows
---------------------------------------------

Almost every verdict a sweep here reaches is ``DECLARED``: a boundary verdict,
and a cycle, rest on the model plus the resolver, both of which record what the
binder actually saw. The weaker tier a finding may end up at is **carried, never
judged here** — an import recovered from a dynamic ``importlib.import_module``
call is intrinsically ``HEURISTIC``, that level joins the meet in
:meth:`pypeeker.dsl.Selection.rows`, and the finding reports it. That is exactly
the old rule's ``symbol.import_confidence or DECLARED``, arrived at without any
sweep forming an opinion about it.

A boundary row comes from :func:`import_rows` rather than from the ``imports``
universe, so this module is the party that has to state that tier: the row's
``evidence`` is ``symbol.import_confidence or Confidence.DECLARED``, the same
expression :mod:`pypeeker.dsl.universes` applies to an imports row. That is a
copy of the symbol's own tier onto the row that stands for it, not a second
opinion about it — a sweep that decided ``HEURISTIC`` on its own here would be
double-counting one piece of evidence.

:data:`IMPURITY` is the single exception, and it is the exception precisely
because it is the only party that *knows*: an impurity verdict resting entirely
on observations whose receiver the binder could not classify is guesswork, and
nothing on the symbols row it hangs off records that. So the purity sweep
declares :class:`~pypeeker.models.Confidence.HEURISTIC` on those verdicts and
``DECLARED`` on the rest, which is the old rule's ``_impurity_confidence``
verbatim. The meet then only ever weakens it further, never strengthens it.

The AnchorKind decision for row sources
---------------------------------------

:func:`~pypeeker.dsl.fact_source` takes ``anchor_kind`` as a **required**
argument and refuses to choose, because its rows are none of the five universes
fork #8 fixes. This module holds all nine call sites, so the choice is made and
defended here. Three answer **``AnchorKind.MODULE``**, four answer
**``AnchorKind.IMPORT``**, and two answer **``AnchorKind.SYMBOL``**.

The ``IMPORT`` ones are :func:`import_rows`, :func:`unused_import_rows`,
:func:`star_import_rows` and :func:`barrel_rows`, and they are the easy case:
their rows *are* import symbols, one per occurrence,
each carrying that symbol's own id — the very anchor the ``imports`` universe
hands the same symbol. Producing the row here rather than there moves no
anchor; what it moves is *what identifies the row*, from a lookup key to the
occurrence itself.

Two more are straightforward. :func:`unit_rows` anchors a top-level package on
its representative file's module id, and :func:`cycle_rows` anchors a component
on its reporting module id: both are real dotted module names that
:meth:`pypeeker.dsl.Corpus.locate` resolves, and both are the anchor the
corresponding modules row would have carried, so re-shaping those two rules
around a row source moved no anchor either.

:func:`drift_rows` is the harder ``SYMBOL``, and it is the case where the id is
*more* specific than the row's subject rather than less: a drift row is about a
**parameter** of a function, so it carries the parameter's own symbol id,
``<function id>:<param>``, which the grammar in
:mod:`pypeeker.models.symbol_id` already spells that way. See that function for
why the parameter and not the function, and for the two fork consequences that
choice settles. :func:`unused_return_rows` is the easy one: its row *is* a
function and its finding is about that function, one per function, so the
anchor is the function's own symbol id.

The remaining one is :func:`allowance_rows`, whose rows are configuration, with
the anchor id ``allowance:<importer>-><dep>``.

* Both halves of the id name **module namespaces** — an allowance is a
  statement about which package may import which package — and ``MODULE`` is
  the one kind whose ids are dotted namespace names rather than symbol, scope
  or reference ids. Nothing else in the taxonomy is even close.
* The ``allowance:`` prefix and the ``->`` separator make the id structurally
  impossible to mistake for a locatable module: no module name contains
  either, so a consumer that fed it to a module-shaped API gets a loud miss
  rather than a silent wrong answer.

Rejected: **minting a sixth ``AnchorKind``**, for the reason
:mod:`pypeeker.dsl.facts` gives at length — the kinds are documented as "one
member per universe", so a sixth member concedes by the code's own taxonomy
that configuration is a sixth universe, which is precisely what fork #8
settles against; it would also break
``tests/test_dsl_anchors.py::test_anchor_kinds_cover_the_five_universes``, a
test outside this task's sanctioned edits and one whose whole job is to make
that concession expensive. Rejected: **``AnchorKind.IMPORT``**, which reads
plausibly and is actively misleading — an ``IMPORT`` anchor everywhere else is
an import *symbol* id that :meth:`pypeeker.dsl.Corpus.locate` resolves to a
real row, and an allowance resolves to nothing.

Why these are row sources and not facts
---------------------------------------

A :class:`~pypeeker.dsl.Fact` answers *per row of an existing universe*: the
table is keyed on the row's anchor id, and the rule quantifies over the
universe. That works for :data:`IMPURITY`, keyed on a symbol id, because a
symbol id identifies one row of the symbols universe.

It does **not** work when the key is not injective over the rows the rule
quantifies over — and **a module id is not injective over indexed files**.
``paths.module_path_from`` collapses ``proj/dup.py`` and
``proj/dup/__init__.py`` onto ``proj.dup``, and collapses ``src/dup/mod.py`` and
``lib/dup/mod.py`` the same way under a multi-root ``src``. That has two
consequences here, and the second is the one that hides:

* **A finding whose domain is not a file cannot be keyed on a representative
  file.** The frozen ``import-boundaries`` strict census quantifies over
  *top-level packages* and the frozen ``no-import-cycles`` over
  *strongly-connected components*; neither is a universe fork #8 fixes, and the
  nearest one — ``modules`` — emits a row per indexed **file**. Electing a
  representative and keying a fact on its module id therefore emits one finding
  per colliding file, each extra one anchored on a file the sweep never chose.
* **A symbol id inherits the collision.** Symbol ids are ``<module>:<local>``,
  so ``from x import go`` in ``proj/dup.py`` and in ``proj/dup/__init__.py``
  bind two different symbols under the one id ``proj.dup:go``. A verdict table
  keyed on that id holds one entry where the ``imports`` universe yields two
  rows, and the second row reads the first's verdict: a violation charged to a
  file whose imports are all permitted, naming a package that file does not
  import — or, where both halves violate, the right *count* with the wrong
  package named in both messages, which no count-based check catches. The
  boundary verdict was a :class:`~pypeeker.dsl.Fact` for one round on the false
  premise that an import symbol id identifies one row. It is :func:`import_rows`
  now, one row per judged **occurrence**, for exactly this reason.

The unused-allowance pass is a row source for a different reason again: its
domain is the project's *configuration*, which no index records at all.

:func:`unused_import_rows` is the fifth, and it is the plainest demonstration
of the collision above. Measured on ``tests/fixtures/parity/boundaries``: the
frozen ``unused-imports`` rule fires on ``src/app/twin/one.py`` and
``src/app/twin/two.py``, whose ``__init__.py`` twins bind the *same* symbol ids
— so a table of unused-import verdicts keyed on ``symbol_id`` holds one entry
where two files each have an import to judge, and the package half's verdict
(skipped, because a barrel re-exports by design) would silently answer for the
module half. It is also the one sweep whose inputs are genuinely file-local
aggregates the grammar cannot reach: "which ids does *this file* reference" and
"which identifiers appear inside *this file's* quoted annotations" are neither
corpus-wide (which is all :func:`~pypeeker.dsl.projected_set` offers) nor
per-row (an ``opaque`` body is handed the row, never the index), and
``follow("references")`` drops exactly the rows the rule is about — the ones
with no references at all.

:func:`drift_rows` is the sixth, and it is the only one here whose row source
exists for a reason of **cardinality** rather than of keying. The frozen
``docstring-drift`` emits one finding per drifted parameter, so one function
with two ghosts is two findings at one line, and a selection over the symbols
universe can only ever produce one match per symbol. The fan-out is not a
missing stage in :mod:`pypeeker.dsl.selection`: it is the observation that the
rule quantifies over *drifted parameters*, which are neither a universe nor a
fact keyed on one, so they are rows. Everything the one-row-one-finding law
buys — one anchor, one evidence value and one derivation chain per emitted
finding — survives unchanged.

:func:`unused_return_rows` is the seventh, and its reason is a third one again:
the frozen ``unused-return-value`` reads ``type_annotation.confidence``, which
the symbols universe does not publish, and its message quotes an aggregate over
the call sites the sweep had to collect anyway. See that function for why
carrying a count here is consistent with the ledger's decision to drop one in
``test-only-production-code``.

:func:`star_import_rows` and :func:`barrel_rows` are the eighth and ninth, and
both are cross-file joins rather than keying or cardinality problems.
``star-imports`` asks which of *this* file's unresolved bare names the *target*
module publicly defines, attributed across the file's stars in order, so what
one row reports depends on what the rows before it consumed. ``barrel-only``
asks whether some **other** package's ``__init__.py`` re-exports this import's
canonical definition — a test on the *pair* ``(barrel module, definition id)``,
which one projected id column cannot express and which the grammar cannot
assemble row-side without string arithmetic it deliberately lacks.

So all nine are :func:`~pypeeker.dsl.fact_source` row sources: one row per
judged import occurrence, per package, per component, per allowance pair, per
import binding, per drifted parameter, per value-promising function, per star
import, per barrel-judged import. The rule still does the rejecting —
``imported_from``, ``violates``, ``declared``, ``is_cycle``, ``allowed``,
``exercised``, ``drift``, the nine booleans on an unused-import row and the six
on a barrel row are carried as fields precisely so the frozen rules'
``continue`` statements stay visible clauses in :mod:`pypeeker.dsl.rules`.

Not everything here is a sweep
------------------------------

One section is not. ``naming-conventions`` needs no pass over the corpus at
all — kind plus name settles it per row — but it does need three compiled
regexes, two name converters and two option coercions, because the grammar has
no regex operator and no string arithmetic. That is fork #9's tier (declared
opacity behind an ``opaque``) rather than fork #11's (a quantifier-collapsing
pass), and it lives here for the same reason the sweeps do: it is the
hand-written Python a rule's expression reaches for, kept out of
:mod:`pypeeker.dsl.rules` so that module stays selections and templates. The
section is marked as such where it starts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pypeeker.analysis import (
    DOCSTRING_STYLES,
    Observations,
    ReceiverKind,
    Trait,
    impurities,
    param_drift,
    parse_documented_params,
    signature_params,
)
from pypeeker.analysis.purity import DEFAULT_POLICY, PurityPolicy
from pypeeker.dsl.anchors import AnchorKind
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.facts import Fact, FactRow, FactTable, fact_source, lazy_table
from pypeeker.dsl.reach import Reach
from pypeeker.dsl.universes import _Universe
from pypeeker.dsl.visibility import _DYNAMIC_ACCESS_BUILTIN_IDS
from pypeeker.models import (
    Confidence,
    FileIndex,
    ReferenceKind,
    Scope,
    ScopeKind,
    Symbol,
    SymbolKind,
    is_unresolved_attr,
    module_of,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.resolve import CrossModuleResolver

# Imports are from concrete sibling modules, never from the ``pypeeker.dsl``
# barrel: the barrel imports this package's modules, so a submodule importing
# it back gets a partially-initialized module and an ImportError at import
# time. The package's own ``barrel-only`` rule polices *cross-package* imports
# and has nothing to say about a sibling, so this is not a boundary escape.
#
# ``pypeeker.analysis.purity`` is deep-imported for ``DEFAULT_POLICY`` /
# ``PurityPolicy`` — the same deep import ``check.rules`` makes. ``barrel-only``
# resolves through re-export chains and fires on a deep import of a name the
# target package's ``__init__`` re-exports; the analysis barrel exports
# ``impurities`` (imported above from it) but not the policy pair, so there is
# no barrel to route these two through.
#
# ``_DYNAMIC_ACCESS_BUILTIN_IDS`` is imported from the sibling
# :mod:`pypeeker.dsl.visibility` rather than re-derived here. It is one frozen
# constant (``check.rules._DYNAMIC_ACCESS_BUILTIN_IDS``) that two families
# happen to need, and two independent copies of it inside one package would be
# two places for it to drift from the spec. The edge is one-way — ``visibility``
# imports nothing from this module — so ``no-import-cycles`` has nothing to say
# about it.


def as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings (``''`` / ``None`` / ``[]`` -> ``[]``).

    A faithful copy of ``check.rules._as_str_list``, kept here because the
    primitive tier normalizes option tables into a fact's ``params`` and every
    family in this module needs the same coercion. Copied rather than imported:
    ``dsl`` may not import ``check`` at all, and ``check`` is frozen.

    Public because :mod:`pypeeker.dsl.rules` needs it too: ``no-impure-functions``
    coerces its ``include`` / ``exclude`` lists there, since *which rows are in
    scope* is the selection's business rather than the sweep's.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


# ── import-boundaries ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _ImportRow:
    """One import **occurrence** in a policed package, and what the sweep made of it.

    Identified by where it is, never by the symbol id alone: two indexed files
    can share a module id, so two different import symbols can share the id
    ``<module>:<local>``. Everything a finding needs is therefore carried on the
    occurrence — ``file_path`` and ``line`` say where it is reported,
    ``imported_from`` is the literal the message quotes, and ``confidence`` is
    the symbol's own ``import_confidence`` tier — rather than looked up against
    a key that two occurrences answer to. See this module's docstring.

    ``violates`` is the whole of the frozen rule's judgement collapsed: the
    origin resolved through re-export chains, and found to be neither the
    importer's own package nor one its allow-list permits. It is carried as
    data rather than applied here so the rule can spell the rejection.
    ``exercises`` is its counterpart for the allowance census — this occurrence
    used a declared pair — and is the one field the row source does not publish,
    because no rule quantifies over it; part (c) reads the pairs, not the
    imports.
    """

    symbol_id: str
    file_path: str
    line: int
    imported_from: str | None
    importer_pkg: str
    dep_pkg: str | None
    detail: str
    violates: bool
    exercises: bool
    confidence: Confidence


@dataclass(frozen=True)
class _UnitRow:
    """One top-level unit of the strict census: the package, and where it answers.

    ``declared`` is the old rule's ``unit in allow or unit in unconstrained``,
    kept as data rather than applied here so the rule can spell it as a visible
    clause. ``file_path`` and ``module_id`` are the representative's, chosen by
    :func:`_representative`.
    """

    unit: str
    declared: bool
    file_path: str
    module_id: str


@dataclass(frozen=True)
class _BoundaryTables:
    """The one pass's three answers, kept together because one pass produces them.

    ``imports`` is one entry per import occurrence in a policed package, in
    index-then-symbol order — the frozen rule's own iteration order, so the
    violations among them come out in the order it emits them; ``units`` is the
    strict census, one entry per top-level unit in the old rule's
    ``sorted(units.items())`` emission order; ``allowances`` is the configured
    allow table flattened, in the old rule's emission order, each pair tagged
    with whether any real import exercised it.
    """

    imports: tuple[_ImportRow, ...]
    units: tuple[_UnitRow, ...]
    allowances: tuple[tuple[str, str, bool], ...]


BoundaryParams = tuple[tuple[tuple[str, tuple[str, ...]], ...], bool, tuple[str, ...], Any]
"""The hashable normalization of ``[tool.pypeeker.import-boundaries]``.

``(allow pairs, strict, unconstrained, root)``. Hashable because it is half of
the per-corpus memo key and because :class:`~pypeeker.dsl.FactRead` refuses an
unhashable ``params`` at construction.
"""


def boundary_params(options: Mapping[str, Any]) -> BoundaryParams:
    """Normalize the rule's option table into the sweep's hashable parameters.

    Order-insensitive by construction: the allow mapping is sorted by importer
    and each dependency list is de-duplicated and sorted, so two configs that
    differ only in ordering share one memoized sweep. The de-duplication
    mirrors the old rule's ``set(deps)`` exactly, quirks included — a string
    value explodes into its characters there and here alike.
    """
    allow_raw = options.get("allow")
    allow = (
        tuple((pkg, tuple(sorted(set(deps)))) for pkg, deps in sorted(allow_raw.items()))
        if isinstance(allow_raw, Mapping)
        else ()
    )
    return (
        allow,
        bool(options.get("strict")),
        tuple(sorted(set(as_str_list(options.get("unconstrained"))))),
        options.get("root"),
    )


def _package_under(module_path: str, root: str) -> str | None:
    """The first package segment of ``module_path`` beneath ``root``.

    ``None`` when ``module_path`` is outside ``root`` or is the root itself.
    A faithful copy of ``check.rules._package_under``.
    """
    parts = module_path.split(".")
    root_parts = root.split(".")
    if parts[: len(root_parts)] != root_parts:
        return None
    rest = parts[len(root_parts):]
    return rest[0] if rest else None


def _origin_package(
    symbol: Symbol, resolver: CrossModuleResolver, root: str
) -> tuple[str | None, bool]:
    """The package that defines an import's target, and whether it got there via a re-export.

    A faithful copy of ``check.rules._import_origin_package``, including the
    two fallbacks to the literal ``imported_from`` package: resolution that
    did not move, and a resolved definition outside ``root``.
    """
    literal_pkg = _package_under(symbol.imported_from or "", root)
    canonical = resolver.resolve_definition(symbol.symbol_id)
    if canonical == symbol.symbol_id:
        return literal_pkg, False
    origin_pkg = _package_under(module_of(canonical), root)
    if origin_pkg is None:
        return literal_pkg, False
    return origin_pkg, origin_pkg != literal_pkg


def _representative(entries: list[tuple[str, str]]) -> tuple[str, str]:
    """The ``(file_path, module_id)`` a package-level finding anchors on.

    Prefers the package ``__init__.py``; otherwise the path-sorted-first module
    file, so the anchor is deterministic across runs. The old rule
    (``check.rules._representative_file``) returns only the path; the module id
    comes back too because it is what the census row's **anchor id** is, and an
    anchor wants a namespace name rather than a path. The reported
    ``file_path`` is the path, exactly as the old rule reports it.
    """
    for entry in sorted(entries):
        if entry[0].endswith("__init__.py"):
            return entry
    return sorted(entries)[0]


def _judge_import(
    symbol: Symbol,
    importer_pkg: str,
    allowed: set[str],
    resolver: CrossModuleResolver,
    root: str,
) -> _ImportRow:
    """Judge one import occurrence, keeping the frozen rule's ``continue`` chain readable.

    Each ``continue`` in ``check.rules.import_boundaries``' inner loop becomes a
    ``return`` of an *unjudged* row here rather than a dropped one, so the
    occurrence still reaches the DSL and the rule spells the rejection. The
    chain in the frozen order: a bare ``import x`` binds no ``imported_from`` to
    charge; an origin the resolver cannot place under ``root``, or that is the
    importer's own package, is nothing to enforce against; and an origin the
    allow-list permits marks the pair exercised for part (c) instead of firing.

    Returns:
        The row for this occurrence, with at most one of ``violates`` and
        ``exercises`` set.
    """

    def row(
        dep_pkg: str | None, detail: str, *, violates: bool = False, exercises: bool = False
    ) -> _ImportRow:
        return _ImportRow(
            symbol_id=symbol.symbol_id,
            file_path=symbol.location.file_path,
            line=symbol.location.span.start.line + 1,
            imported_from=symbol.imported_from,
            importer_pkg=importer_pkg,
            dep_pkg=dep_pkg,
            detail=detail,
            violates=violates,
            exercises=exercises,
            confidence=symbol.import_confidence or Confidence.DECLARED,
        )

    if not symbol.imported_from:
        return row(None, "via")
    dep_pkg, via_reexport = _origin_package(symbol, resolver, root)
    detail = "via re-export" if via_reexport else "via"
    if dep_pkg is None or dep_pkg == importer_pkg:
        return row(dep_pkg, detail)
    if dep_pkg in allowed:
        return row(dep_pkg, detail, exercises=True)
    return row(dep_pkg, detail, violates=True)


def _boundary_sweep(corpus: Corpus, params: BoundaryParams) -> _BoundaryTables:
    """One pass over every index, reproducing the frozen rule's three quantifications.

    Written to follow ``check.rules.import_boundaries`` clause by clause so the
    two can be diffed by eye: the early no-op return, the module-id map, the
    per-file root fallback, the unit census, the origin-resolved verdict, the
    exercised-pair set, then the strict and allowance passes in that order.

    Four of the frozen rule's own tests move out of here and into the rule as
    visible clauses, because they are statements about *which rows fire* rather
    than quantifier collapse: the ``__``-prefix exclusion (``__main__`` and
    friends are not layered units), ``unit in allow or unit in unconstrained``,
    ``not symbol.imported_from``, and the whole origin judgement. The census
    therefore carries every top-level unit it saw, each tagged with
    ``declared``; the import table carries every import occurrence in a policed
    package, each tagged with ``violates``; and :func:`pypeeker.dsl.rules` does
    the rejecting. Under ``strict = false`` the census is empty — the frozen
    rule does not run that pass at all.
    """
    allow_pairs, strict, unconstrained, configured_root = params
    allow: dict[str, set[str]] = {pkg: set(deps) for pkg, deps in allow_pairs}
    if not allow and not strict:
        return _BoundaryTables((), (), ())  # no configuration → no-op

    resolver = corpus.resolver
    module_ids: dict[str, str] = {}
    for index in corpus.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE), None
        )
        if module_id is not None:
            module_ids[index.file_path] = module_id

    judged: list[_ImportRow] = []
    exercised: set[tuple[str, str]] = set()
    units: dict[str, list[tuple[str, str]]] = {}
    for index in corpus.indexes:
        module_id = module_ids.get(index.file_path)
        if module_id is None:
            continue
        # No configured root → each file falls back to its own top-level
        # segment, so every root of a multi-root tree stays policed.
        root = configured_root or module_id.split(".")[0]
        importer_pkg = _package_under(module_id, root)
        if importer_pkg is None:
            continue
        units.setdefault(importer_pkg, []).append((index.file_path, module_id))
        if importer_pkg not in allow:
            continue
        allowed = allow[importer_pkg]
        for symbol in index.symbols:
            if symbol.kind != SymbolKind.IMPORT:
                continue
            occurrence = _judge_import(symbol, importer_pkg, allowed, resolver, root)
            judged.append(occurrence)
            if occurrence.exercises and occurrence.dep_pkg:
                exercised.add((importer_pkg, occurrence.dep_pkg))

    census: tuple[_UnitRow, ...] = ()
    if strict:
        census = tuple(
            _UnitRow(
                unit=unit,
                declared=unit in allow or unit in unconstrained,
                file_path=rep_path,
                module_id=rep_module,
            )
            for unit, entries in sorted(units.items())
            for rep_path, rep_module in (_representative(entries),)
        )

    allowances = tuple(
        (importer_pkg, dep_pkg, (importer_pkg, dep_pkg) in exercised)
        for importer_pkg in sorted(allow)
        for dep_pkg in sorted(allow[importer_pkg])
    )
    return _BoundaryTables(tuple(judged), census, allowances)


def _boundary_tables(corpus: Corpus, params: BoundaryParams) -> _BoundaryTables:
    """The boundary sweep, run at most once per ``(corpus, params)``.

    :class:`~pypeeker.dsl.facts._FactLookup` already memoizes per
    ``(fact name, params)``, which is not enough here: ``import-boundaries``
    reads *three* products of one pass, under one fact name and two row
    sources, so without this second memo the same pass would run three times.
    This is the case :meth:`pypeeker.dsl.Corpus.memo` is documented for.
    """
    return corpus.memo(
        ("sweep", "import-boundaries", params), lambda: _boundary_sweep(corpus, params)
    )


def import_rows(params: BoundaryParams) -> _Universe:
    """A row source over judged imports: one row per import occurrence in a policed package.

    **An import symbol id does not identify an import occurrence.** Symbol ids
    are ``<module>:<local>`` and a module id is not injective over indexed files
    (see :func:`unit_rows`), so ``from x import go`` in ``proj/dup.py`` and in
    ``proj/dup/__init__.py`` bind two symbols under the one id
    ``proj.dup:go``. A verdict table keyed on that id — which is what this was
    for one round — holds one entry where the ``imports`` universe yields two
    rows, so the second row reads the first's verdict and the rule fires on a
    file whose imports are all permitted. Where both halves happen to violate,
    the count matches and only the *wording* is wrong, which is worse. Producing
    the row here removes the key, and with it the failure mode.

    The rows carry ``AnchorKind.IMPORT`` with the import symbol's own id — the
    same anchor an ``imports`` row for that symbol carries, so nothing
    downstream of the anchor moved. The id stays non-unique under the collision;
    that is now only a fact about anchors (two occurrences that report
    separately may be *named* the same for ``--why``), not about which rows
    exist or what they say.

    Each row's ``evidence`` is the symbol's own ``import_confidence``, defaulted
    to ``DECLARED`` exactly as :mod:`pypeeker.dsl.universes` defaults an imports
    row's — so a dynamically recovered binding still reports ``HEURISTIC``
    through the meet, which is the frozen rule's
    ``symbol.import_confidence or Confidence.DECLARED``.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        for entry in _boundary_tables(corpus, params).imports:
            yield FactRow(
                anchor_id=entry.symbol_id,
                fields={
                    "imported_from": entry.imported_from,
                    "importer_pkg": entry.importer_pkg,
                    "dep_pkg": entry.dep_pkg,
                    "detail": entry.detail,
                    "violates": entry.violates,
                    "file_path": entry.file_path,
                    "line": entry.line,
                },
                evidence=entry.confidence,
            )

    return fact_source(
        "import-boundary-imports",
        ("imported_from", "importer_pkg", "dep_pkg", "detail", "violates", "file_path", "line"),
        rows,
        anchor_kind=AnchorKind.IMPORT,
    )


def unit_rows(params: BoundaryParams) -> _Universe:
    """A row source over the strict census: one row per indexed top-level unit.

    **A package is not a file, and this is why the rows come from here rather
    than from the modules universe.** The frozen rule quantifies over
    ``units`` — a dict keyed by top-level package name — and emits at most one
    finding per key. The modules universe emits one row per indexed *file*, and
    a module id is not injective over files: ``paths.module_path_from``
    collapses ``proj/dup.py`` and ``proj/dup/__init__.py`` to the same
    ``proj.dup``, and a multi-root ``src`` collapses ``src/dup/mod.py`` and
    ``lib/dup/mod.py`` the same way. A census keyed on the representative's
    module id and quantified over modules therefore fires once per *colliding
    file*, with every extra finding anchored on a file the sweep never chose.
    One row per unit removes the failure mode by construction instead of
    filtering it back out.

    The rows carry ``AnchorKind.MODULE`` with the representative's module id as
    the anchor: unlike an allowance pair (see this module's docstring) that id
    is a real, locatable module, and it is the same anchor a modules row for
    that file would have carried — so nothing downstream of the anchor moves.

    Every row reports at ``line = 1``, the constant the frozen rule hard-codes:
    a package has no line, and the representative file's first line is the
    least arbitrary place to point.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        for entry in _boundary_tables(corpus, params).units:
            yield FactRow(
                anchor_id=entry.module_id,
                fields={
                    "unit": entry.unit,
                    "declared": entry.declared,
                    "module": entry.module_id,
                    "file_path": entry.file_path,
                    "line": 1,
                },
            )

    return fact_source(
        "import-boundary-units",
        ("unit", "declared", "module", "file_path", "line"),
        rows,
        anchor_kind=AnchorKind.MODULE,
    )


def allowance_rows(params: BoundaryParams) -> _Universe:
    """A row source over the configured allow table: one row per declared pair.

    The rule part built on this is the only one in the family that quantifies
    over **configuration** rather than over code, which is what
    :func:`pypeeker.dsl.fact_source` exists for: "this allow pair is never
    exercised" is a true statement about the project with no model row behind
    it. Every row reports at ``pyproject.toml:1`` unconditionally, matching the
    frozen rule — the finding is about the configuration, and a source-file
    anchor would churn whenever files move.

    See this module's docstring for why the rows carry ``AnchorKind.MODULE``.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        for importer_pkg, dep_pkg, used in _boundary_tables(corpus, params).allowances:
            yield FactRow(
                anchor_id=f"allowance:{importer_pkg}->{dep_pkg}",
                fields={
                    "importer_pkg": importer_pkg,
                    "dep_pkg": dep_pkg,
                    "exercised": used,
                    "file_path": "pyproject.toml",
                    "line": 1,
                },
            )

    return fact_source(
        "import-boundary-allowances",
        ("importer_pkg", "dep_pkg", "exercised", "file_path", "line"),
        rows,
        anchor_kind=AnchorKind.MODULE,
    )


# ── no-import-cycles ────────────────────────────────────────────────────────

_DEFERRED_SCOPE_KINDS = frozenset(
    {ScopeKind.FUNCTION, ScopeKind.LAMBDA, ScopeKind.COMPREHENSION}
)
"""Scope kinds that do **not** run at module load. Copied from the frozen rule.

``dsl-rewrite.md``'s ledger carries this as a binding spec note: an import is
load-time iff **every** enclosing scope up to the module is a module or class
body, and a ``function``, ``lambda`` **or** ``comprehension`` anywhere on the
chain defers it. All three members are load-bearing — a comprehension gets its
own scope in Python 3, so ``[import_module(m) for m in names]`` at module level
is *not* a module-load import, and the ledger records that the strongest design
proposal got all three points wrong.
"""


@dataclass(frozen=True)
class _Cycle:
    """One strongly-connected component of the module-load import graph.

    ``members`` is the **rendered** member list, joined with ``", "`` in sorted
    order, not the tuple: the expression grammar has no join and
    ``"{members}".format`` over a tuple would print Python's repr. The same
    decision the frozen engine makes one line earlier, and the same class of
    decision as the purity summary below — a value that is a string because the
    finding quotes it verbatim.

    ``is_cycle`` is the frozen rule's ``len(component) < 2`` test and
    ``allowed`` its ``frozenset(members) in allowed`` test, both kept as data so
    the rule can spell them as visible clauses rather than have them silently
    applied here.

    ``reporter`` is the module the finding is charged to, and ``file_path`` /
    ``line`` are where it points: the first in-component edge site in the frozen
    rule's iteration order, else that module's own file at line ``1``.
    """

    members: str
    is_cycle: bool
    allowed: bool
    reporter: str
    file_path: str
    line: int


CycleParams = tuple[tuple[str, ...], ...]
"""The hashable normalization of ``[tool.pypeeker.no-import-cycles]``'s ``allow``.

A sorted tuple of sorted member tuples. Hashable because it is half of the
per-corpus memo key and because :class:`~pypeeker.dsl.FactRead` refuses an
unhashable ``params`` at construction.
"""


def cycle_params(options: Mapping[str, Any]) -> CycleParams:
    """Normalize the rule's ``allow`` option into the sweep's hashable parameters.

    The frozen rule (``check.builtin.no_import_cycles._allowed_cycles``) builds
    a ``set[frozenset[str]]``: a non-list ``allow`` and any non-list entry
    inside it are dropped silently, and both the entries and the members are
    unordered. Sorting both levels and de-duplicating both levels reproduces
    that set exactly while staying hashable — so two configs that differ only
    in how the cycles or their members are ordered share one memoized sweep.
    """
    raw = options.get("allow")
    if not isinstance(raw, list):
        return ()
    return tuple(
        sorted(
            {
                tuple(sorted({str(module) for module in entry}))
                for entry in raw
                if isinstance(entry, list)
            }
        )
    )


def _runs_at_import_time(
    parent_scope_id: str | None, scope_by_id: Mapping[str, Scope]
) -> bool:
    """Whether an import in ``parent_scope_id`` executes at module load.

    A faithful copy of ``check.builtin.no_import_cycles._runs_at_import_time``,
    including the branch that treats a **missing** scope as load-time: that is
    the conservative choice, and it still reports the edge. See
    :data:`_DEFERRED_SCOPE_KINDS` for why the deferred set has exactly three
    members and why the ledger calls it binding.
    """
    scope_id = parent_scope_id
    while scope_id is not None:
        scope = scope_by_id.get(scope_id)
        if scope is None:
            return True
        if scope.kind in _DEFERRED_SCOPE_KINDS:
            return False
        scope_id = scope.parent_scope_id
    return True


def _strongly_connected_components(
    graph: Mapping[str, Iterable[str]],
) -> list[list[str]]:
    """Tarjan's SCC algorithm, iterative — copied verbatim from the frozen rule.

    Copied rather than adapted on purpose. Component *membership* is what the
    finding quotes and what an ``allow`` entry matches, and while any correct
    SCC implementation agrees on membership, the frozen one also fixes the
    iteration order of the returned components — and therefore the order the
    findings come out in. Rewriting it would put that order at risk for no gain.
    """
    counter = 0
    order: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []

    for root in graph:
        if root in order:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        order[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, neighbours = work[-1]
            descended = False
            while neighbours:
                nxt = neighbours[0]
                if nxt not in order:
                    order[nxt] = lowlink[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, sorted(graph.get(nxt, ()))))
                    descended = True
                    break
                if on_stack.get(nxt):
                    lowlink[node] = min(lowlink[node], order[nxt])
                neighbours.pop(0)
            if descended:
                continue
            if lowlink[node] == order[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
    return result


def _cycle_sweep(corpus: Corpus, params: CycleParams) -> tuple[_Cycle, ...]:
    """One pass building the module-load import graph and describing its components.

    Follows ``check.builtin.no_import_cycles._no_import_cycles`` clause by
    clause: the module-id census, the ``module_to_file`` inverse, the load-time
    edge filter, origin resolution through re-export chains, edges dropped to
    non-project targets and to self, one representative import site kept per
    ``(importer, origin)`` edge, then Tarjan and the reporter election.

    One :class:`_Cycle` per component, in Tarjan's order — including the
    single-module components and the ``allow``-suppressed ones, tagged rather
    than dropped, so the rule can spell the frozen rule's two ``continue``
    statements as visible clauses. Electing a reporter for a component that the
    rule will reject costs one dictionary lookup and buys the rule the two
    tests, which is the trade fork #11 asks for: the sweep collapses the
    quantifier, the DSL keeps the ∀.

    ``file_path`` is carried out of the sweep rather than read off a model row.
    That is the correction: the reporting module id is **not** a unique key over
    indexed files (``proj/dup.py`` and ``proj/dup/__init__.py`` share one, and
    so do two roots of a multi-root ``src``), so a per-file universe keyed on it
    fires once per colliding file — and every extra finding carries a line
    copied from the *other* file's edge site, pointing at whatever happens to
    be on that line. The frozen rule takes ``(file_path, line)`` from
    ``edge_site`` for exactly this reason, and so does this.
    """
    resolver = corpus.resolver
    module_of_file: dict[str, str] = {}
    for index in corpus.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE), None
        )
        if module_id is not None:
            module_of_file[index.file_path] = module_id
    project_modules = set(module_of_file.values())
    # Last path wins on a collision, exactly as the frozen rule's inverse does.
    module_to_file = {module: path for path, module in module_of_file.items()}

    edges: dict[str, set[str]] = {module: set() for module in project_modules}
    edge_site: dict[tuple[str, str], tuple[str, int]] = {}
    for index in corpus.indexes:
        importer = module_of_file.get(index.file_path)
        if importer is None:
            continue
        scope_by_id = {scope.scope_id: scope for scope in index.scopes}
        for symbol in index.symbols:
            if symbol.kind is not SymbolKind.IMPORT:
                continue
            if not _runs_at_import_time(symbol.parent_scope_id, scope_by_id):
                continue
            origin = module_of(resolver.resolve_definition(symbol.symbol_id))
            if origin == importer or origin not in project_modules:
                continue
            edges[importer].add(origin)
            edge_site.setdefault(
                (importer, origin),
                (symbol.location.file_path, symbol.location.span.start.line + 1),
            )

    allowed = {frozenset(entry) for entry in params}
    found: list[_Cycle] = []
    for component in _strongly_connected_components(edges):
        members = sorted(component)
        component_set = set(component)
        reporter, (file_path, line) = next(
            (
                (importer, edge_site[(importer, origin)])
                for importer in members
                for origin in sorted(edges[importer])
                if origin in component_set and (importer, origin) in edge_site
            ),
            (members[0], (module_to_file.get(members[0], members[0]), 1)),
        )
        found.append(
            _Cycle(
                members=", ".join(members),
                is_cycle=len(component) >= 2,
                allowed=frozenset(members) in allowed,
                reporter=reporter,
                file_path=file_path,
                line=line,
            )
        )
    return tuple(found)


def cycle_rows(params: CycleParams) -> _Universe:
    """A row source over the import graph's components: one row per component.

    **A strongly-connected component is not a file.** The frozen rule quantifies
    over ``_strongly_connected_components(edges)`` and emits at most one finding
    per component; the modules universe emits one row per indexed *file*, and
    two files can share a module id (see :func:`unit_rows`). Quantifying the
    cycle over modules and keying the answer on the reporting module id
    therefore fires once per colliding file. One row per component is the shape
    the frozen rule actually has.

    The rows carry ``AnchorKind.MODULE`` with the reporting module's id: a real,
    locatable module, and the same anchor a modules row for the reporter would
    have carried.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        component_list = corpus.memo(
            ("sweep", "import-cycle", params), lambda: _cycle_sweep(corpus, params)
        )
        for found in component_list:
            yield FactRow(
                anchor_id=found.reporter,
                fields={
                    "members": found.members,
                    "is_cycle": found.is_cycle,
                    "allowed": found.allowed,
                    "module": found.reporter,
                    "file_path": found.file_path,
                    "line": found.line,
                },
            )

    return fact_source(
        "import-cycle-components",
        ("members", "is_cycle", "allowed", "module", "file_path", "line"),
        rows,
        anchor_kind=AnchorKind.MODULE,
    )


# ── no-impure-functions ─────────────────────────────────────────────────────

_MAX_OBSERVATIONS_IN_MESSAGE = 3


def _describe_observation(obs: Any) -> str:
    """Render one impurity observation as ``Kind 'name' (line N)``, line 1-indexed.

    A faithful copy of ``check.rules._describe_observation``, attribute ladder
    and all: the observation types are unrelated dataclasses that name the thing
    they observed differently, so the first attribute present out of ``name``,
    ``qualified_name``, ``method``, ``target``, ``attribute``, ``callee`` wins.
    An observation with no ``line`` (a transitive impure call is about a callee,
    not a site) renders without the suffix.
    """
    name = None
    for attr in ("name", "qualified_name", "method", "target", "attribute", "callee"):
        value = getattr(obs, attr, None)
        if value is not None:
            name = value
            break
    label = type(obs).__name__
    if name is not None:
        label = f"{label} '{name}'"
    line = getattr(obs, "line", None)
    if line is not None:
        label = f"{label} (line {line + 1})"
    return label


def _summarize_observations(found: Observations) -> str:
    """The first few observations, ``; ``-joined, then ``+N more``.

    A faithful copy of ``check.rules._summarize_observations``. The summary is
    computed here rather than in the message template for the same reason
    :class:`_Cycle` pre-joins its members: the grammar has no aggregate, so a
    list of observations cannot become a sentence in a ``str.format`` template.
    The template still owns every word around it.
    """
    shown = list(found)[:_MAX_OBSERVATIONS_IN_MESSAGE]
    parts = [_describe_observation(obs) for obs in shown]
    remaining = len(found) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return "; ".join(parts)


def _impurity_confidence(found: Observations) -> Confidence:
    """``HEURISTIC`` when every observation rests on an ``UNKNOWN`` receiver, else ``DECLARED``.

    A faithful copy of ``check.rules._impurity_confidence``, and the one place
    in this module that declares a tier below ``DECLARED`` — see the module
    docstring for why this sweep is allowed to and the others are not. Name
    matching against a receiver the binder could not classify is guesswork; any
    structurally-grounded observation (a builtin or module call, a parameter or
    import receiver, a transitive impure call) keeps the verdict ``DECLARED``,
    because the impurity holds regardless of the weak ones.
    """
    weak = [
        obs
        for obs in found
        if getattr(obs, "receiver_kind", None) is ReceiverKind.UNKNOWN
    ]
    if found and len(weak) == len(found):
        return Confidence.HEURISTIC
    return Confidence.DECLARED


PurityParams = tuple[tuple[str, ...], tuple[str, ...]]
"""The hashable normalization of the purity options that reach the analysis.

``(extra-impure, allow)``. ``include`` / ``exclude`` are deliberately **not**
here: they scope which rows the rule asks about, which is the DSL's job, and
folding them into the params would fragment the memo per configuration for no
change in any answer.
"""


def purity_params(options: Mapping[str, Any]) -> PurityParams:
    """Normalize the policy-shaping options into the sweep's hashable parameters.

    Order is preserved rather than sorted, unlike the other two families: these
    lists feed :meth:`pypeeker.analysis.purity.PurityPolicy.extended`, and
    keeping the configured order keeps the params a faithful record of what the
    user wrote. Two differently-ordered configs therefore get two memo entries
    and the same answers, which is the safe direction to be wrong in.
    """
    return (
        tuple(as_str_list(options.get("extra-impure"))),
        tuple(as_str_list(options.get("allow"))),
    )


def _policy(params: PurityParams) -> PurityPolicy:
    """Build the purity policy from the normalized options.

    A faithful copy of ``check.rules._configured_policy``: dotted
    ``extra-impure`` names extend the module denylist, bare names extend the
    builtin denylist, ``allow`` names are removed from every denylist, and with
    neither option the shared default policy is returned unchanged (identity,
    not a copy — so the analysis layer's own caches still hit).
    """
    extra, allow = params
    if not extra and not allow:
        return DEFAULT_POLICY
    return DEFAULT_POLICY.extended(
        extra_impure_builtins=[name for name in extra if "." not in name],
        extra_module_impure=[name for name in extra if "." in name],
        allow=list(allow),
    )


def _impurity_table(corpus: Corpus, params: PurityParams) -> FactTable:
    """The purity verdict for one symbol at a time — computed only when asked.

    :func:`~pypeeker.dsl.lazy_table` rather than
    :func:`~pypeeker.dsl.mapping_table` because
    :func:`~pypeeker.analysis.impurities` walks the transitive call graph from
    the symbol it is given, and eagerly walking it from *every* function in the
    project would cost orders of magnitude more than the rule needs. The old
    rule pays it only for symbols that survived the kind / include / exclude
    filters; a lazy table reproduces that cost exactly, because the rule's cheap
    clauses run before the fact read and a row that fails them never asks.

    The one :class:`~pypeeker.query.SemanticQueryEngine` is built once and
    shared across every symbol, matching the old rule's single ``engine=``
    argument — it is the call-graph cache, and rebuilding it per symbol would
    turn a shared walk into a quadratic one.
    """
    engine = SemanticQueryEngine(corpus.store)
    policy = _policy(params)

    def compute(symbol_id: str) -> Trait | None:
        found = impurities(corpus.store, symbol_id, engine=engine, policy=policy)
        if not found:  # None (unanalyzable) or empty (pure) — the old rule's guard
            return None
        return Trait(
            value=_summarize_observations(found),
            confidence=_impurity_confidence(found),
            provenance=f"purity sweep: {symbol_id}",
        )

    return lazy_table(compute)


IMPURITY = Fact(name="impurity", reach=Reach.PROJECT, compute=_impurity_table)
"""Per function/method symbol id: the rendered impurity summary, if it is impure.

``PROJECT`` because the transitive walk leaves the file: a pure-looking function
is impure when something it calls in another module is. ``None`` covers both of
the old rule's skip cases — the symbol is pure, or the analysis could not read
it at all — because neither produces a finding and the rule has nothing
different to say about them.
"""


# ── unused-imports ──────────────────────────────────────────────────────────

_QUOTED = re.compile(r"""(['"])(.*?)\1""")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
"""``check.builtin.unused_imports``'s two regexes, copied verbatim."""


@dataclass(frozen=True)
class _ImportBindingRow:
    """One import binding, and every question the frozen rule asks about it.

    Three of the fields — ``in_barrel``, ``has_all`` and ``dynamic_access`` —
    are properties of the *file*, identical across every row it produces. They
    are carried per row on purpose: the frozen rule spends two of them on
    early ``return []`` statements before it looks at a single symbol, and a
    sweep that dropped those files' rows instead would turn two of the rule's
    documented exclusions into rows that silently do not exist. Carried as
    fields, they stay rejections :mod:`pypeeker.dsl.rules` performs and
    ``--why`` can show.

    ``used`` and ``forward_ref`` are the two genuinely file-local aggregates:
    "does anything in this file reference this binding" and "does this name
    appear inside a quoted annotation here". Neither is expressible in the
    grammar — see this module's docstring.
    """

    symbol_id: str
    name: str
    file_path: str
    line: int
    in_barrel: bool
    has_all: bool
    is_star: bool
    dynamic: bool
    dotted: bool
    future: bool
    forward_ref: bool
    used: bool
    dynamic_access: bool


def _forward_ref_identifiers(index: FileIndex) -> set[str]:
    """Identifiers appearing inside quoted (forward-reference) type annotations.

    A faithful copy of ``check.builtin.unused_imports._forward_ref_names``,
    over-collection included: a name used only in ``x: "Node | None"`` or in
    the nested ``x: list["Foo"]`` is invisible to reference analysis, because
    the binder does not descend into string literals, so its import would
    otherwise read as unused. Collecting an identifier out of a plain string
    value (``Literal["x"]``) is harmless — it can only *spare* an import.
    """
    names: set[str] = set()
    for symbol in index.symbols:
        annotation = symbol.type_annotation
        if annotation is None or not annotation.raw:
            continue
        for _quote, inner in _QUOTED.findall(annotation.raw):
            names.update(_IDENTIFIER.findall(inner))
    return names


def _unused_import_sweep(corpus: Corpus) -> tuple[_ImportBindingRow, ...]:
    """One row per IMPORT symbol in the corpus, in index-then-symbol order.

    The per-file aggregates are computed once per file and copied onto that
    file's rows, which is exactly what the frozen rule does — it is invoked
    once per file and computes ``used``, the forward-reference set and the
    file's confidence tier before its symbol loop.
    """
    rows: list[_ImportBindingRow] = []
    for index in corpus.indexes:
        in_barrel = index.file_path.endswith("__init__.py")
        has_all = any(symbol.name == "__all__" for symbol in index.symbols)
        used = {ref.symbol_id for ref in index.references}
        forward_refs = _forward_ref_identifiers(index)
        dynamic_access = any(
            ref.symbol_id in _DYNAMIC_ACCESS_BUILTIN_IDS for ref in index.references
        )
        for symbol in index.symbols:
            if symbol.kind is not SymbolKind.IMPORT:
                continue
            origin = symbol.imported_from
            rows.append(
                _ImportBindingRow(
                    symbol_id=symbol.symbol_id,
                    name=symbol.name,
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    in_barrel=in_barrel,
                    has_all=has_all,
                    is_star=symbol.name == "*",
                    dynamic=symbol.import_confidence is not None,
                    dotted="." in symbol.name,
                    future=bool(origin) and origin.split(".", 1)[0] == "__future__",
                    forward_ref=symbol.name in forward_refs,
                    used=symbol.symbol_id in used,
                    dynamic_access=dynamic_access,
                )
            )
    return tuple(rows)


def unused_import_rows() -> _Universe:
    """A row source over import bindings: one row per IMPORT symbol per file.

    **An import symbol id does not identify an import binding**, for the reason
    :func:`import_rows` gives at length, and this rule is where the collision is
    measurable rather than hypothetical: on ``tests/fixtures/parity/boundaries``
    the frozen rule reports two unused imports, in ``src/app/twin/one.py`` and
    ``src/app/twin/two.py``, and each of those files has an ``__init__.py`` twin
    binding the *same* ids. A verdict table keyed on ``symbol_id`` holds one
    entry per pair, and the barrel half — skipped outright by the frozen rule,
    because a package ``__init__`` re-exports by design — would answer for the
    module half. One row per binding removes the key, and with it the failure
    mode.

    The rows carry ``AnchorKind.IMPORT`` with the import symbol's own id, the
    same anchor an ``imports`` row for that symbol carries, so nothing
    downstream of the anchor moves.

    A row's ``evidence`` is ``HEURISTIC`` when its file references
    ``getattr``/``globals``/``vars``/``locals`` and ``DECLARED`` otherwise,
    which is the frozen rule's whole-file confidence computed once per file and
    copied onto that file's rows. It is stated here rather than restated as a
    clause for two reasons. The idiom is :func:`import_rows`'s — a row source
    copies the tier of the thing the row stands for, the meet carries it, and no
    rule has to mention it. And ``Weaken`` is *inventoried*:
    :data:`pypeeker.dsl.DYNAMIC_ACCESS_WEAKENED_RULES` enumerates the five
    visibility rules whose findings the frozen engine downgrades through
    ``check.rules._dynamic_access_confidence``, and
    ``tests/test_dsl_visibility_rules.py`` asserts that exactly those five
    expressions carry the node. ``unused-imports`` reaches the same tier by its
    own route (``check.builtin.unused_imports`` computes it inline), so
    spelling it as a sixth ``Weaken`` would claim membership of a family it is
    not in.

    This sweep takes no parameters because the frozen rule takes no options.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        table = corpus.memo(("sweep", "unused-imports"), lambda: _unused_import_sweep(corpus))
        for entry in table:
            yield FactRow(
                anchor_id=entry.symbol_id,
                fields={
                    "symbol_id": entry.symbol_id,
                    "name": entry.name,
                    "file_path": entry.file_path,
                    "line": entry.line,
                    "in_barrel": entry.in_barrel,
                    "has_all": entry.has_all,
                    "is_star": entry.is_star,
                    "dynamic": entry.dynamic,
                    "dotted": entry.dotted,
                    "future": entry.future,
                    "forward_ref": entry.forward_ref,
                    "used": entry.used,
                },
                evidence=(
                    Confidence.HEURISTIC if entry.dynamic_access else Confidence.DECLARED
                ),
            )

    return fact_source(
        "unused-import-bindings",
        (
            "symbol_id",
            "name",
            "file_path",
            "line",
            "in_barrel",
            "has_all",
            "is_star",
            "dynamic",
            "dotted",
            "future",
            "forward_ref",
            "used",
        ),
        rows,
        anchor_kind=AnchorKind.IMPORT,
    )


# ── naming-conventions (not a sweep — see the module docstring) ──────────────

_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_SNAKE_OR_UPPER_RE = re.compile(r"^(?:[a-z][a-z0-9_]*|[A-Z][A-Z0-9_]*)$")

_CAMEL_BOUNDARY_BEFORE_WORD = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_BOUNDARY_AFTER_LOWER = re.compile(r"([a-z0-9])([A-Z])")

_NAMING_KINDS_DEFAULT: tuple[str, ...] = ("function", "method", "class")
"""Kinds ``naming-conventions`` checks by default; variable/parameter are opt-in."""


def _to_snake_case(name: str) -> str:
    """Best-effort ``snake_case`` form of ``name``. Copied from the frozen rule.

    The three edge cases the frozen converter documents, all load-bearing
    because the result is quoted in a message the oracle compares:
    consecutive capitals split before the last of the run (``HTTPServer`` ->
    ``http_server``), digits stick to the word they follow (``parseHTML2Text``
    -> ``parse_html2_text``), and underscore runs collapse (``get_Value`` ->
    ``get_value``). Leading underscores are the caller's concern.
    """
    result = _CAMEL_BOUNDARY_BEFORE_WORD.sub(r"\1_\2", name)
    result = _CAMEL_BOUNDARY_AFTER_LOWER.sub(r"\1_\2", result)
    return re.sub(r"_+", "_", result).lower()


def _to_pascal_case(name: str) -> str:
    """Best-effort ``PascalCase`` form of ``name``. Copied from the frozen rule.

    Splits on underscores and upper-cases each part's first letter, leaving the
    rest alone, so acronym parts survive (``HTTP_server`` -> ``HTTPServer``)
    while ordinary ones capitalize (``bad_class`` -> ``BadClass``) and a part
    starting with a digit is kept as-is (``foo_2d`` -> ``Foo2d``).
    """
    parts = [part for part in name.split("_") if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


@dataclass(frozen=True)
class _Convention:
    """One kind's naming contract: a label, a pattern, and a suggester."""

    label: str
    pattern: re.Pattern[str]
    suggest: Callable[[str], str]


_DEFAULT_CONVENTIONS: Mapping[SymbolKind, _Convention] = {
    SymbolKind.FUNCTION: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.METHOD: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.PROPERTY: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.PARAMETER: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.VARIABLE: _Convention(
        # Constants are not statically distinguishable from variables, so
        # UPPER_SNAKE is tolerated rather than mis-flagged. Frozen contract.
        "snake_case (or UPPER_SNAKE_CASE)",
        _SNAKE_OR_UPPER_RE,
        _to_snake_case,
    ),
    SymbolKind.CLASS: _Convention("PascalCase", _PASCAL_RE, _to_pascal_case),
}
"""The frozen rule's ``_DEFAULT_CONVENTIONS``, verbatim: six kinds, four labels."""

_KIND_CHOICES = frozenset(_DEFAULT_CONVENTIONS)
"""Kinds the ``kinds`` option may select; anything else is ignored."""

NamingParams = tuple[tuple[SymbolKind, ...], Mapping[SymbolKind, _Convention], tuple[str, ...]]
"""``(selected kinds, per-kind conventions, allow patterns)``, as the rule reads them."""


def _naming_kinds(raw: Any) -> tuple[SymbolKind, ...]:
    """The ``kinds`` option as SymbolKinds, reproducing the frozen fallback **asymmetry**.

    ``check.builtin.naming_conventions._selected_kinds`` is
    ``for value in _as_str_list(raw) or list(_DEFAULT_KINDS)``, and the ``or``
    is where the asymmetry lives: an absent or empty option falls back to the
    default three, but a *non-empty* option whose every entry fails
    ``SymbolKind(...)`` — ``kinds = ["bogus"]`` — falls back to nothing and
    yields the **empty** set, so the rule checks no symbol at all. A port that
    "sensibly" fell back to the default there would over-fire on every
    misconfigured project, which is the same class of bug
    :func:`pypeeker.dsl.rules._enum_set` documents for ``require-docstrings``.

    Returns a tuple rather than the frozen ``frozenset`` for the reason
    :func:`pypeeker.dsl.rules._enum_set` gives: the only consumer is
    :meth:`~pypeeker.dsl.Expr.is_in`, whose membership test is the same either
    way, and written order stays inspectable in a derivation's ``rhs``.
    """
    out: list[SymbolKind] = []
    for value in as_str_list(raw) or list(_NAMING_KINDS_DEFAULT):
        try:
            kind = SymbolKind(value)
        except ValueError:
            continue
        if kind in _KIND_CHOICES and kind not in out:
            out.append(kind)
    return tuple(out)


def _naming_conventions(raw: Any) -> Mapping[SymbolKind, _Convention]:
    """The defaults overlaid with the ``conventions`` option's per-kind regexes.

    ``check.builtin.naming_conventions._configured_conventions``, silences
    included: a non-Mapping value is ignored wholesale, and so is any entry
    whose key is not a ``SymbolKind`` or whose value is not a compilable regex.
    An override **keeps the kind's default suggester** — the regex redefines
    what conforms, never what shape to convert to — and takes the label
    ``pattern '<value>'``, which is the fourth of the four label shapes the
    message can carry.
    """
    conventions = dict(_DEFAULT_CONVENTIONS)
    if not isinstance(raw, Mapping):
        return conventions
    for key, value in raw.items():
        try:
            kind = SymbolKind(str(key))
            pattern = re.compile(str(value))
        except (ValueError, re.error):
            continue
        if kind in _KIND_CHOICES:
            conventions[kind] = _Convention(
                f"pattern '{value}'", pattern, conventions[kind].suggest
            )
    return conventions


def naming_params(options: Mapping[str, Any]) -> NamingParams:
    """Normalize ``[tool.pypeeker.naming-conventions]`` into what the rule reads.

    One call site for the three coercions so the rule's two parts cannot
    disagree about which kinds are selected or which pattern a kind is held to.
    """
    return (
        _naming_kinds(options.get("kinds")),
        _naming_conventions(options.get("conventions")),
        tuple(as_str_list(options.get("allow"))),
    )


def convention_label(conventions: Mapping[SymbolKind, _Convention], kind: SymbolKind) -> str:
    """The label the message quotes for ``kind`` — one of the four shapes."""
    return conventions[kind].label


def conforms(
    conventions: Mapping[SymbolKind, _Convention], kind: SymbolKind, stripped: str
) -> bool:
    """True when ``stripped`` matches ``kind``'s convention pattern.

    ``re.Pattern.match`` rather than ``fullmatch``: every pattern here is
    anchored at both ends already, and an override is used exactly as the
    frozen rule uses it, unanchored ends and all.
    """
    return conventions[kind].pattern.match(stripped) is not None


def suggested_name(
    conventions: Mapping[SymbolKind, _Convention],
    kind: SymbolKind,
    name: str,
    stripped: str,
) -> str:
    """The suggestion the message appends, or ``""`` when there is none.

    One value carries both halves of the frozen rule's ``if suggestion and
    suggestion != stripped`` test *and* the text it would append, so the two
    :class:`~pypeeker.dsl.RulePart`\\ s that split on it cannot disagree about
    which case a row is in. Leading underscores are stripped before suggesting
    and re-attached afterwards, exactly as the frozen rule does, so a
    visibility prefix survives into the suggestion (``_helperName`` suggests
    ``_helper_name``).
    """
    suggestion = conventions[kind].suggest(stripped)
    if not suggestion or suggestion == stripped:
        return ""
    return name[: len(name) - len(stripped)] + suggestion


# ── docstring-drift ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _DriftRow:
    """One drifted parameter of one function: which name, and which direction.

    ``drift`` is ``"ghost"`` (documented, not in the signature) or
    ``"missing"`` (in the signature, not documented) — a field rather than two
    row sources, so the two message shapes partition one row source on a
    visible value the way ``naming-conventions``' two parts partition on
    ``suggested_name``.

    ``file_path`` and ``line`` are the *function's* own location, not the
    parameter's: the frozen rule points every finding at the function it is
    about, and two ghosts on one function therefore report the same line.
    """

    symbol_id: str
    name: str
    kind: SymbolKind
    id_module: str
    param: str
    drift: str
    file_path: str
    line: int


DocstringStyle = str | None
"""The hashable normalization of ``[tool.pypeeker.docstring-drift]``'s ``style``.

One of :data:`pypeeker.analysis.DOCSTRING_STYLES` or ``None`` for autodetect.
Hashable because it is half of the per-corpus memo key.
"""


def docstring_params(options: Mapping[str, Any]) -> DocstringStyle:
    """Normalize the rule's ``style`` option into the sweep's parameter.

    The frozen rule's ``style_opt if style_opt in DOCSTRING_STYLES else None``:
    an unrecognized value is not an error, it falls back to autodetection. Only
    ``style`` reaches the sweep — ``allow`` and ``require-complete`` select
    among rows the sweep already produced, so two projects differing only in
    those share one memoized pass.
    """
    style = options.get("style")
    return style if style in DOCSTRING_STYLES else None


def _docstring_drift_sweep(
    corpus: Corpus, style: DocstringStyle
) -> tuple[_DriftRow, ...]:
    """One row per drifted parameter, ghosts before missing, per function.

    The frozen rule's loop with its ``continue`` statements intact — kind, an
    empty docstring, an unrecognized params section — except the ``allow``
    check, which is a rule clause here rather than a sweep filter so a
    configured exemption stays visible in ``--why``. Parsing a docstring the
    rule will exempt costs a parse and changes no output.

    The three parsers come from :mod:`pypeeker.analysis`, which is where the
    frozen rule gets them too: the planner that repairs a drift has to
    re-derive it with the identical parser and lives in ``refactor``, so the
    parsers were never in ``check`` to begin with.
    """
    found: list[_DriftRow] = []
    for index in corpus.indexes:
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            if not symbol.docstring:
                continue
            section = parse_documented_params(symbol.docstring, style)
            if section is None:
                continue
            ghosts, missing = param_drift(section, signature_params(index, symbol))
            for drift, names in (("ghost", ghosts), ("missing", missing)):
                found.extend(
                    _DriftRow(
                        symbol_id=symbol.symbol_id,
                        name=symbol.name,
                        kind=symbol.kind,
                        id_module=module_of(symbol.symbol_id),
                        param=param,
                        drift=drift,
                        file_path=symbol.location.file_path,
                        line=symbol.location.span.start.line + 1,
                    )
                    for param in names
                )
    return tuple(found)


def drift_rows(style: DocstringStyle) -> _Universe:
    """A row source over drifted parameters: one row per (function, parameter).

    **This is the fan-out**, and it is the whole of it. ``docstring-drift`` is
    the one frozen rule that emits several findings from one symbol — one per
    ghost parameter, all at the same line — and
    :meth:`pypeeker.dsl.Selection.rows` still yields exactly one match per row.
    Nothing in :mod:`pypeeker.dsl.selection` needed a fan-out stage, because the
    cardinality is a property of the *domain* being quantified over: the frozen
    rule's inner loop runs over drifted parameters, so drifted parameters are
    the rows. Each one carries its own anchor, its own evidence and its own
    derivation chain, so ``--why`` answers per emitted finding rather than per
    function.

    The rows carry ``AnchorKind.SYMBOL``, and the id
    ``<function symbol id>:<parameter>`` is a real symbol id under the grammar
    in :mod:`pypeeker.models.symbol_id` (``module:Scope.Chain:local``) rather
    than a synthetic key like :func:`allowance_rows`'s. It is the id of the
    **parameter**, which is exactly right in both directions: for a ``missing``
    row that symbol exists and :meth:`pypeeker.dsl.Corpus.locate` resolves it,
    and for a ``ghost`` row it is the id the documented parameter *would* have
    — which is what a rename intent targets. Two consequences fall out for
    free. Fork #6 keys the baseline on ``(rule_id, anchor_id)``, and two ghosts
    on one function must not collapse to one key; ghost and missing names are
    disjoint by construction, so every finding gets its own. And fork #5
    derives ``fix_id`` as ``<rule>:<mutation>:<anchor>``, which spells
    ``docstring-drift:rename-param:<symbol_id>:<ghost>`` — the frozen
    ``RenameDocstringParamIntent`` id, character for character, with nothing to
    port at phase 4.

    Rejected: anchoring on the function's own symbol id. It reads as the
    subject of the finding, but an anchor identifies the *finding*, and three
    ghosts would then share one baseline key and one derived ``fix_id``. It
    would also break an invariant :mod:`pypeeker.dsl.selection` states outright:
    a follow step dedupes on ``(anchor id, source file)`` and falls back to a
    bare anchor id for fact-source rows, which carry no ``_Env`` — safe only
    "because every fact-source row already carries a distinct anchor id".
    Sharing one id across a function's ghosts would make that fallback silently
    drop findings the moment this rule grew a follow.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        table = corpus.memo(
            ("sweep", "docstring-drift", style),
            lambda: _docstring_drift_sweep(corpus, style),
        )
        for entry in table:
            yield FactRow(
                anchor_id=f"{entry.symbol_id}:{entry.param}",
                fields={
                    "symbol_id": entry.symbol_id,
                    "name": entry.name,
                    "kind": entry.kind,
                    "id_module": entry.id_module,
                    "param": entry.param,
                    "drift": entry.drift,
                    "file_path": entry.file_path,
                    "line": entry.line,
                },
            )

    return fact_source(
        "docstring-drift-params",
        (
            "symbol_id",
            "name",
            "kind",
            "id_module",
            "param",
            "drift",
            "file_path",
            "line",
        ),
        rows,
        anchor_kind=AnchorKind.SYMBOL,
    )


# ── unused-return-value ─────────────────────────────────────────────────────

_MAX_CALL_SITES_IN_MESSAGE = 3
"""``check.builtin.unused_return_value._MAX_CALL_SITES_IN_MESSAGE``, verbatim."""

_VALUE_ESCAPE_KINDS: tuple[ReferenceKind, ...] = (
    ReferenceKind.READ,
    ReferenceKind.DECORATOR,
)
"""Reference kinds meaning "the function escapes as a value". Frozen contract."""


@dataclass(frozen=True)
class _ReturnRow:
    """One function that declares a non-``None`` return type, with its call sites.

    ``call_count`` and ``call_sites`` are the two aggregates the frozen
    message quotes. They are fields here, and that is deliberately the
    **opposite** of the ledger's ``test-only-production-code`` decision to drop
    a count — see :func:`unused_return_rows` for why the two are reconcilable.
    """

    symbol_id: str
    id_module: str
    kind: SymbolKind
    annotation: str
    file_path: str
    line: int
    escapes: bool
    has_calls: bool
    any_used: bool
    call_count: int
    call_sites: str


def _is_none_annotation(raw: str) -> bool:
    """``-> None`` including both quoted spellings. Frozen ``_is_none_annotation``."""
    return raw.strip() in ("None", '"None"', "'None'")


def _is_return_candidate(symbol: Symbol) -> bool:
    """The frozen ``_is_candidate``: FUNCTION/METHOD, declared non-None return, not a dunder.

    ``ann.confidence is not Confidence.DECLARED`` is the clause that cannot be
    a rule-side predicate: the symbols universe publishes ``type_annotation``'s
    text but not its tier, so an inferred annotation is indistinguishable from
    a written one on the row. That, plus the aggregate the message needs, is
    what makes this a row source rather than a selection over ``symbols()``.
    """
    if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
        return False
    ann = symbol.type_annotation
    if ann is None or ann.confidence is not Confidence.DECLARED:
        return False
    if _is_none_annotation(ann.raw):
        return False
    name = symbol.name
    return not (name.startswith("__") and name.endswith("__"))


def _summarize_sites(sites: list[tuple[str, int, bool]]) -> str:
    """First few call sites as ``file:line``, plus a ``+N more`` tail. Frozen, verbatim."""
    shown = sites[:_MAX_CALL_SITES_IN_MESSAGE]
    parts = [f"{file_path}:{line}" for file_path, line, _ in shown]
    remaining = len(sites) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return ", ".join(parts)


def _unused_return_sweep(corpus: Corpus) -> tuple[_ReturnRow, ...]:
    """The frozen rule's two passes, in order: attribute calls, then judge candidates.

    Pass one walks every reference once, keyed on the canonical definition the
    resolver lands it on: ``CALL`` references accumulate ``(file, line,
    result_used)`` in occurrence order, and ``READ``/``DECORATOR`` references
    record that the definition escapes as a value. Pass two emits one row per
    candidate function, carrying the frozen rule's remaining ``continue``
    statements as fields rather than performing them — ``allow``, the escape,
    the empty site list and "some site used the result" are all clauses in
    :func:`pypeeker.dsl.rules._unused_return_value`.

    ``_is_return_candidate`` is not carried, because it decides what a row
    **is**: a procedure returning ``None`` is not a row of "functions that
    promise a value", the way a function with no drift is not a row of
    :func:`drift_rows`.
    """
    resolver = corpus.resolver
    call_sites: dict[str, list[tuple[str, int, bool]]] = {}
    escapes: set[str] = set()
    for index in corpus.indexes:
        for ref in index.references:
            if ref.kind == ReferenceKind.CALL:
                canonical = resolver.resolve_reference(ref)
                call_sites.setdefault(canonical, []).append((
                    ref.location.file_path,
                    ref.location.span.start.line + 1,
                    ref.result_used,
                ))
            elif ref.kind in _VALUE_ESCAPE_KINDS:
                escapes.add(resolver.resolve_reference(ref))

    found: list[_ReturnRow] = []
    for index in corpus.indexes:
        for symbol in index.symbols:
            if not _is_return_candidate(symbol):
                continue
            canonical = resolver.resolve_definition(symbol.symbol_id)
            sites = call_sites.get(canonical, [])
            annotation = symbol.type_annotation
            found.append(
                _ReturnRow(
                    symbol_id=symbol.symbol_id,
                    id_module=module_of(symbol.symbol_id),
                    kind=symbol.kind,
                    annotation="" if annotation is None else annotation.raw,
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    escapes=canonical in escapes,
                    has_calls=bool(sites),
                    any_used=any(used for _, _, used in sites),
                    call_count=len(sites),
                    call_sites=_summarize_sites(sites),
                )
            )
    return tuple(found)


def unused_return_rows() -> _Universe:
    """A row source over functions promising a value: one row per candidate.

    Two things keep this out of the ``symbols()`` universe, and only one of
    them is the aggregate. The first is that the frozen ``_is_candidate``
    reads ``symbol.type_annotation.confidence``, which no symbols row
    publishes — the row carries the annotation's *text*, so an inferred
    annotation and a written one are the same row. The second is the message,
    which quotes ``len(sites)`` and a three-item ``file:line`` summary of the
    call sites that discard the result.

    On that second point this is deliberately the **opposite** of the ledger's
    ``test-only-production-code`` decision, and the two are reconcilable
    rather than in tension. There, the count was an aggregate over a reference
    set the rule quantified over from the symbols universe: there was no row
    holding it, and buying one would have meant a new stage type for one
    string. Here the sweep that produces the row already holds the site list —
    it had to, to answer ``any(used)`` at all — so the count and the summary
    are ordinary fields of a row that exists anyway, and the frozen message is
    reproduced character for character with no new machinery. The test is
    whether the value is a property of the row, not whether it happens to be
    an integer.

    The rows carry ``AnchorKind.SYMBOL`` with the function's own symbol id: a
    finding here is about the function, one per function, so the anchor is the
    subject and :meth:`pypeeker.dsl.Corpus.locate` resolves it.

    Evidence is left at the row default (``DECLARED``): the frozen rule passes
    no confidence, so its findings land on ``Violation``'s default, and this
    rule is not in :data:`pypeeker.dsl.DYNAMIC_ACCESS_WEAKENED_RULES`.

    Takes no parameters. The ``allow`` option selects among rows the sweep
    already produced, so two projects differing only in it share one memoized
    pass — and a configured exemption stays a visible clause in ``--why``.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        table = corpus.memo(
            ("sweep", "unused-return-value"), lambda: _unused_return_sweep(corpus)
        )
        for entry in table:
            yield FactRow(
                anchor_id=entry.symbol_id,
                fields={
                    "symbol_id": entry.symbol_id,
                    "id_module": entry.id_module,
                    "kind": entry.kind,
                    "annotation": entry.annotation,
                    "file_path": entry.file_path,
                    "line": entry.line,
                    "escapes": entry.escapes,
                    "has_calls": entry.has_calls,
                    "any_used": entry.any_used,
                    "call_count": entry.call_count,
                    "call_sites": entry.call_sites,
                },
            )

    return fact_source(
        "unused-return-candidates",
        (
            "symbol_id",
            "id_module",
            "kind",
            "annotation",
            "file_path",
            "line",
            "escapes",
            "has_calls",
            "any_used",
            "call_count",
            "call_sites",
        ),
        rows,
        anchor_kind=AnchorKind.SYMBOL,
    )


# ── star-imports ────────────────────────────────────────────────────────────


def _module_id_of(index: FileIndex) -> str | None:
    """The index's MODULE symbol id (its dotted module path), or ``None``.

    ``check.builtin.star_imports._module_indexes`` and
    ``check.builtin.barrel_only._module_id_of`` both spell this ``next((s.symbol_id
    for s in index.symbols if s.kind is SymbolKind.MODULE), None)``; the two
    sweeps below share one copy.
    """
    return next(
        (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE),
        None,
    )


@dataclass(frozen=True)
class _StarRow:
    """One ``from m import *`` occurrence, with the names it actually supplies.

    ``indexed`` and ``name_count`` are the two fields the rule partitions its
    four message shapes on — see :data:`pypeeker.dsl.rules.RULES`. They are
    fields rather than an interpolated ``{plural}`` because the frozen
    ``_message`` writes three literal lines and computes the pluralization, and
    a template carrying half a message would put that derivation where fork #9
    says the derivation tree can no longer describe it.

    ``used_names`` is the frozen ``', '.join(names)`` — the join happens in the
    sweep because the grammar has no string arithmetic, and the list itself is
    an aggregate over the *file's* unresolved references that no per-row
    predicate could rebuild.

    ``imported_from`` is carried exactly as the binder recorded it, ``None``
    included: the frozen message interpolates ``star.imported_from`` into an
    f-string, which renders a missing target as ``'None'``, and
    :meth:`str.format` over the same value does the same thing.
    """

    symbol_id: str
    imported_from: str | None
    file_path: str
    line: int
    indexed: bool
    used_names: str
    name_count: int
    evidence: Confidence


def _star_symbols(index: FileIndex) -> list[Symbol]:
    """The file's ``"*"`` IMPORT symbols, in file order. Frozen ``_star_symbols``."""
    stars = [s for s in index.symbols if s.kind is SymbolKind.IMPORT and s.name == "*"]
    stars.sort(key=lambda s: (s.location.span.start.line, s.location.span.start.column))
    return stars


def _module_indexes(corpus: Corpus) -> dict[str, FileIndex]:
    """Dotted module path -> its :class:`FileIndex`. Frozen ``_module_indexes``.

    A plain dict assignment, so **the last index wins** when two indexed files
    collapse onto one module id (``proj/dup.py`` and ``proj/dup/__init__.py``
    both answer ``proj.dup``). That is a quirk of the frozen rule and it is
    copied rather than fixed: :meth:`pypeeker.dsl.Corpus.locate` elects the
    *first* such file by design, so a port that reached for it would resolve a
    star's target to a different module's public surface on exactly the shapes
    ``tests/fixtures/parity/boundaries`` and ``.../cycles`` exist to produce.
    """
    out: dict[str, FileIndex] = {}
    for index in corpus.indexes:
        module_id = _module_id_of(index)
        if module_id is not None:
            out[module_id] = index
    return out


def _public_surface(index: FileIndex) -> frozenset[str]:
    """Public module-level names of ``index`` — what ``import *`` can supply.

    Frozen ``_public_surface``, including its two documented approximations:
    ``__all__``'s contents are unavailable (the index records only that the
    name is bound), so every non-underscore module-level symbol counts, and
    imports count too because star semantics re-export them.
    """
    module_id = _module_id_of(index)
    if module_id is None:
        return frozenset()
    return frozenset(
        s.name
        for s in index.symbols
        if s.parent_scope_id == module_id
        and s.kind is not SymbolKind.MODULE
        and s.name != "*"
        and not s.name.startswith("_")
    )


def _unresolved_bare_names(index: FileIndex) -> set[str]:
    """Bare unresolved reference names in ``index``. Frozen ``_unresolved_bare_names``.

    A name a star supplies binds to nothing the binder can see, so it surfaces
    as an unresolved reference whose id is the bare name. ``<unresolved>.attr``
    sentinels and underscore-prefixed names are excluded — a star never
    supplies the latter absent ``__all__``, which the frozen rule ignores.
    """
    return {
        ref.symbol_id
        for ref in index.references
        if not ref.resolved
        and not is_unresolved_attr(ref.symbol_id)
        and ref.symbol_id.isidentifier()
        and not ref.symbol_id.startswith("_")
    }


def _attribute_star_names(
    stars: Sequence[Symbol],
    unresolved: set[str],
    modules: Mapping[str, FileIndex],
) -> dict[str, list[str]]:
    """Attribute unresolved names to star imports, first-star-wins.

    Frozen ``_attribute_names``, minus the ``unattributed`` residue the frozen
    rule discards into ``_unattributed`` (only the remedy consults it, and the
    read half has no remedies). Walks ``stars`` in file order; each remaining
    name goes to the first star whose *indexed* target publicly defines it, and
    a star with an unindexed target gets **no entry at all** rather than an
    empty one — which is why the rule's unindexed branch is a partition on
    ``indexed`` and not on ``name_count == 0``.

    Keyed on ``symbol_id``, so two stars sharing one id would have the second
    overwrite the first — copied deliberately, because the frozen rule then
    reads ``used_by.get(star.symbol_id, [])`` per star and would report the
    same overwritten list twice.
    """
    remaining = set(unresolved)
    used_by: dict[str, list[str]] = {}
    for star in stars:
        target = modules.get(star.imported_from)
        if target is None:
            continue
        supplied = sorted(remaining & _public_surface(target))
        used_by[star.symbol_id] = supplied
        remaining.difference_update(supplied)
    return used_by


def _star_import_sweep(corpus: Corpus) -> tuple[_StarRow, ...]:
    """One row per ``"*"`` IMPORT symbol, in index-then-file order.

    The frozen rule's shape exactly: per file, collect the stars, attribute the
    file's unresolved bare names across them, decide the file's confidence
    tier, then emit one finding per star. Files with no star produce no rows,
    which is the frozen ``if not stars: continue`` — a file without a star
    import is not a row of "star imports" the way a function with no drift is
    not a row of :func:`drift_rows`.
    """
    modules = _module_indexes(corpus)
    rows: list[_StarRow] = []
    for index in corpus.indexes:
        stars = _star_symbols(index)
        if not stars:
            continue
        used_by = _attribute_star_names(stars, _unresolved_bare_names(index), modules)
        # First-star-wins attribution differs from Python's last-wins
        # shadowing, so multi-star findings are heuristic by construction.
        file_confidence = (
            Confidence.DECLARED if len(stars) == 1 else Confidence.HEURISTIC
        )
        for star in stars:
            indexed = star.imported_from in modules
            names = used_by.get(star.symbol_id, []) if indexed else []
            rows.append(
                _StarRow(
                    symbol_id=star.symbol_id,
                    imported_from=star.imported_from,
                    file_path=star.location.file_path,
                    line=star.location.span.start.line + 1,
                    indexed=indexed,
                    used_names=", ".join(names),
                    name_count=len(names),
                    evidence=(
                        file_confidence if indexed else Confidence.HEURISTIC
                    ),
                )
            )
    return tuple(rows)


def star_import_rows() -> _Universe:
    """A row source over star imports: one row per ``"*"`` IMPORT symbol.

    Three things put this outside the ``imports`` universe, and none of them is
    a shortcut. The used-name list is a join of *this file's* unresolved bare
    references against the *target module's* public surface — two aggregates
    over two different files, neither reachable from a row. The attribution is
    order-dependent across the file's stars (first-star-wins), so what one row
    reports depends on what the rows before it consumed. And the confidence
    tier is a property of the file's star *count*, not of any one star.

    The rows carry ``AnchorKind.IMPORT`` with the star's own symbol id — the
    same anchor an ``imports`` row for that symbol carries, so nothing
    downstream of the anchor moves.

    A row's ``evidence`` is the frozen ``file_confidence``: ``DECLARED`` for a
    lone star, ``HEURISTIC`` when the file has more than one (first-star-wins
    is not Python's last-wins shadowing), and always ``HEURISTIC`` when the
    target is not indexed. It is intrinsic evidence rather than a
    :class:`~pypeeker.dsl.Weaken` node for :func:`unused_import_rows`'s reason:
    ``Weaken`` is inventoried by
    :data:`pypeeker.dsl.DYNAMIC_ACCESS_WEAKENED_RULES`, which names the five
    visibility rules the frozen engine downgrades through one shared helper,
    and this rule reaches its tier by its own route rather than belonging to
    that family.

    Takes no parameters; the frozen rule takes no options.

    **The remedy is absent.** The frozen rule attaches a
    ``RewriteStarImportIntent`` to single-star ``DECLARED`` findings with at
    least one used name; the read half has no mutation terminals, so the port
    emits the finding without it. Recorded in ``dsl-rewrite.md``'s ledger and
    invisible to the oracle, which compares five fields and no remedy.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        table = corpus.memo(("sweep", "star-imports"), lambda: _star_import_sweep(corpus))
        for entry in table:
            yield FactRow(
                anchor_id=entry.symbol_id,
                fields={
                    "symbol_id": entry.symbol_id,
                    "imported_from": entry.imported_from,
                    "file_path": entry.file_path,
                    "line": entry.line,
                    "indexed": entry.indexed,
                    "used_names": entry.used_names,
                    "name_count": entry.name_count,
                },
                evidence=entry.evidence,
            )

    return fact_source(
        "star-imports",
        (
            "symbol_id",
            "imported_from",
            "file_path",
            "line",
            "indexed",
            "used_names",
            "name_count",
        ),
        rows,
        anchor_kind=AnchorKind.IMPORT,
    )


# ── barrel-only ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _BarrelRow:
    """One import binding judged against the target package's curated barrel.

    Six of the fields are the frozen rule's ``continue`` statements carried as
    answers rather than performed here: ``in_barrel`` (a package ``__init__``
    deep-importing its own submodules is how barrels are built), ``dynamic``,
    ``cross_package``, ``deeper_than_barrel``, ``curated`` and ``re_exported``.
    They stay clauses :mod:`pypeeker.dsl.rules` performs so ``--why`` can show
    which one rejected a row.

    ``imported_name`` is the **last dotted segment of ``imported_from``**, not
    ``symbol.name``. The frozen message quotes
    ``symbol.imported_from.rpartition(".")[2]``, so ``from a.b import C as D``
    is worded ``import 'C' via ...`` — the name the barrel actually exports,
    not the consumer's local alias. Publishing this as ``name`` would invite
    exactly that substitution.
    """

    symbol_id: str
    imported_from: str
    imported_name: str
    target_module: str
    barrel_module: str
    file_path: str
    line: int
    in_barrel: bool
    dynamic: bool
    cross_package: bool
    deeper_than_barrel: bool
    curated: bool
    re_exported: bool
    evidence: Confidence


def barrel_params(options: Mapping[str, Any]) -> str | None:
    """The configured root package for ``barrel-only``, or ``None``.

    ``None`` means the frozen fallback: each file uses its own top-level
    segment, mirroring ``import-boundaries``. Hashable by construction, so it
    can key the sweep's memo entry directly instead of through a params object.
    """
    root = options.get("root")
    return None if root is None else str(root)


def _curated_barrels(corpus: Corpus) -> dict[str, set[str]]:
    """Barrel module id -> the canonical definitions its ``__init__`` re-exports.

    The frozen rule's curation test, verbatim: an ``__init__.py`` with a MODULE
    symbol that binds a ``SymbolKind.VARIABLE`` named ``__all__`` — the
    intentional-public-surface signal — mapped to the resolved definitions of
    its IMPORT symbols. ``__all__``'s string contents are not in the index, so
    the re-export set is approximated by the barrel's imports, which is the
    same approximation ``unused-public-symbol`` and ``born-private`` make.

    A plain dict assignment, so **the last index wins** on a colliding module
    id, exactly as in :func:`_module_indexes` and for the same reason.
    """
    resolver = corpus.resolver
    barrels: dict[str, set[str]] = {}
    for index in corpus.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        module_id = _module_id_of(index)
        if module_id is None:
            continue
        if not any(
            s.kind is SymbolKind.VARIABLE and s.name == "__all__" for s in index.symbols
        ):
            continue
        barrels[module_id] = {
            resolver.resolve_definition(s.symbol_id)
            for s in index.symbols
            if s.kind is SymbolKind.IMPORT
        }
    return barrels


def _barrel_sweep(corpus: Corpus, configured_root: str | None) -> tuple[_BarrelRow, ...]:
    """One row per IMPORT symbol in a policed file, with the barrel verdict on it.

    A file produces no rows at all when it has no MODULE symbol or when
    :func:`_package_under` puts it outside the root — the frozen rule's two
    file-level ``continue`` statements, which decide whether the rule has
    *jurisdiction* rather than what it makes of an import. That is
    :func:`_boundary_sweep`'s existing precedent. Its third file-level skip,
    ``__init__.py``, is **not** one of those: it is a documented exemption of
    the rule, so it is carried as ``in_barrel`` and rejected in the selection.

    The derived answers are computed in the frozen order and each guarded by
    the one before it, so a row that fails an early test never pays for a later
    one — in particular ``resolve_definition`` is called only where the frozen
    rule calls it.
    """
    resolver = corpus.resolver
    barrels = _curated_barrels(corpus)
    rows: list[_BarrelRow] = []
    for index in corpus.indexes:
        module_id = _module_id_of(index)
        if module_id is None:
            continue
        root = configured_root or module_id.split(".")[0]
        importer_pkg = _package_under(module_id, root)
        if importer_pkg is None:
            continue
        in_barrel = index.file_path.endswith("__init__.py")
        for symbol in index.symbols:
            if symbol.kind is not SymbolKind.IMPORT:
                continue
            imported_from = symbol.imported_from or ""
            # imported_from for `from a.b import C` is "a.b.C"; the module part
            # is everything before the final dotted segment.
            target_module, _, imported_name = imported_from.rpartition(".")
            target_pkg = _package_under(target_module, root) if target_module else None
            cross_package = target_pkg is not None and target_pkg != importer_pkg
            barrel_module = f"{root}.{target_pkg}" if cross_package else ""
            deeper = cross_package and target_module != barrel_module
            exported = barrels.get(barrel_module) if deeper else None
            curated = bool(exported)
            re_exported = curated and (
                resolver.resolve_definition(symbol.symbol_id) in (exported or set())
            )
            rows.append(
                _BarrelRow(
                    symbol_id=symbol.symbol_id,
                    imported_from=imported_from,
                    imported_name=imported_name,
                    target_module=target_module,
                    barrel_module=barrel_module,
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    in_barrel=in_barrel,
                    dynamic=symbol.import_confidence is not None,
                    cross_package=cross_package,
                    deeper_than_barrel=deeper,
                    curated=curated,
                    re_exported=re_exported,
                    evidence=symbol.import_confidence or Confidence.DECLARED,
                )
            )
    return tuple(rows)


def barrel_rows(configured_root: str | None) -> _Universe:
    """A row source over import bindings judged against curated barrels.

    The curated-barrel map is the collapse: whether *this* import should have
    gone through a barrel depends on what some **other** package's
    ``__init__.py`` re-exports, resolved through the whole project's re-export
    chains. A :class:`~pypeeker.dsl.ProjectedSet` cannot answer it, because the
    test is a *pair* — is this canonical definition in the export set of
    ``root.<target package>`` — and building that composite key row-side would
    need string arithmetic the grammar deliberately does not have.

    An import symbol id is also not injective over import bindings, for the
    reason :func:`unused_import_rows` gives at length: two files sharing a
    module id bind one id for the same local name, and this rule's own
    ``in_barrel`` exemption is precisely the shape where one half of such a
    pair is skipped and the other is not. One row per binding removes the key.

    The rows carry ``AnchorKind.IMPORT`` with the import symbol's own id.

    Evidence is ``symbol.import_confidence or DECLARED``, the idiom
    :func:`import_rows` uses — a row copies the tier of the thing it stands
    for. It is unobservable here by construction: every row whose tier is not
    ``DECLARED`` has ``import_confidence`` set, and the selection's ``dynamic``
    clause rejects exactly those.

    Args:
        configured_root: the ``root`` option, or ``None`` for the per-file
            top-level-segment fallback. Normalized by :func:`barrel_params`.
    """

    def rows(corpus: Corpus) -> Iterator[FactRow]:
        table = corpus.memo(
            ("sweep", "barrel-only", configured_root),
            lambda: _barrel_sweep(corpus, configured_root),
        )
        for entry in table:
            yield FactRow(
                anchor_id=entry.symbol_id,
                fields={
                    "symbol_id": entry.symbol_id,
                    "imported_from": entry.imported_from,
                    "imported_name": entry.imported_name,
                    "target_module": entry.target_module,
                    "barrel_module": entry.barrel_module,
                    "file_path": entry.file_path,
                    "line": entry.line,
                    "in_barrel": entry.in_barrel,
                    "dynamic": entry.dynamic,
                    "cross_package": entry.cross_package,
                    "deeper_than_barrel": entry.deeper_than_barrel,
                    "curated": entry.curated,
                    "re_exported": entry.re_exported,
                },
                evidence=entry.evidence,
            )

    return fact_source(
        "barrel-only-imports",
        (
            "symbol_id",
            "imported_from",
            "imported_name",
            "target_module",
            "barrel_module",
            "file_path",
            "line",
            "in_barrel",
            "dynamic",
            "cross_package",
            "deeper_than_barrel",
            "curated",
            "re_exported",
        ),
        rows,
        anchor_kind=AnchorKind.IMPORT,
    )
