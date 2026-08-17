"""The impurity pair: ``pure-decorator-contracts`` and ``import-time-side-effects``.

Phase 3f of the rewrite recorded in ``dsl-rewrite.md``, and the last two rules
of the port. Both drive :func:`pypeeker.analysis.impurities` through the same
tier the already-ported ``no-impure-functions`` uses — the lazy
:data:`pypeeker.dsl.sweeps.IMPURITY` fact — so every word of the impurity
summary and every confidence tier comes from helpers
:mod:`pypeeker.dsl.sweeps` already documents as faithful copies of the frozen
ones. Nothing about the *rendering* of an impurity is decided here.

**``pure-decorator-contracts`` is one selection over symbols.** The frozen rule
is a single pass over every FUNCTION / METHOD symbol with four ``continue``
guards; the port is the same four clauses in the same order, with the contract
name computed as a row field because the message quotes it.

**``import-time-side-effects`` is three selections over references**, one per
arm of the frozen ``_describe_call``, written in its order. That function is a
*fall-through partition* rather than three independent tests, and the port
carries the exclusions that make it one:

* Shape 1 computes ``bare = _bare_call_name(ref)`` and **returns
  unconditionally when ``bare is not None``** — either the finding or ``None``.
  So parts B and C both begin by excluding a non-``None`` bare name.
* Shape 2 fires only when ``qualified in policy.module_impure_names``, which
  implies ``qualified is not None``. A qualified name that misses the table
  falls through, so part C excludes membership rather than the name itself —
  one clause covering both of the frozen ``elif``'s exits.

No row can therefore be worded twice, and none is dropped. Part order in
:data:`pypeeker.dsl.rules.RULES` is the frozen ``if``/``elif``/``else`` order
for readability only: ``scripts/differential-check.py`` sorts findings before
comparing, so a three-part split cannot diverge on emission order.

**The scope walk is pointwise here.** The frozen rule materializes
``_import_time_scope_ids(index)`` — the set of scopes that execute during the
import — and tests membership. The port reads ``row.runs_at_import``, the same
predicate asked of one scope at a time, published by
:func:`pypeeker.dsl.universes._reference_record`. Deliberately **not** a
project-wide set: scope ids are ``<module>:<chain>``, two files that share a
module id (the harness's ``boundaries`` corpus has such twins) collide, and a
union would admit one file's function body because its twin's scope of the same
id runs at import.

One frozen clause is deliberately **not** ported, in the style
:mod:`pypeeker.dsl.mutation` records for ``local_symbol_ids``:
``_qualified_call_name``'s ``ref.receiver_chain is None`` guard.
``binder.references.receiver_metadata`` returns a non-``None`` receiver root
only together with a non-empty chain, so the guard can never decide anything
the root test has not already decided; porting it would claim the rule asks a
question it never asks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pypeeker.dsl.columns import DEFINITION_ID, DEFINITION_KIND, column_of
from pypeeker.dsl.expr import Const, Expr, all_of, any_of, not_, opaque, row
from pypeeker.dsl.facts import fact_of
from pypeeker.dsl.selection import Selection, references, symbols
from pypeeker.dsl.sweeps import (
    IMPURITY,
    PurityParams,
    as_str_list,
    impure_builtin_names,
    module_impure_names,
    purity_params,
)
from pypeeker.models import (
    Confidence,
    ReferenceKind,
    SymbolKind,
    builtin_name,
    is_builtin,
    is_unresolved_attr,
    unresolved_attr_name,
)

# Imports are from concrete sibling modules, never from the ``pypeeker.dsl``
# barrel, for the reason :mod:`pypeeker.dsl.sweeps` records: the barrel imports
# this package's modules, so importing it back yields a partially-initialized
# module. The edge to ``sweeps`` is one-way — nothing there imports this module
# — and it exists so the deep import of :mod:`pypeeker.analysis.purity` (the
# purity policy behind both name tables and the impurity fact) stays in the one
# module that documents why it is allowed.

DEFAULT_DECORATORS: tuple[str, ...] = (
    "cache",
    "lru_cache",
    "cached_property",
    "property",
)
"""Decorators that promise the decorated function has no side effects.

``check.builtin.pure_decorator_contracts.DEFAULT_DECORATORS``, copied verbatim.
Matched against the decorator's leading dotted name **or** its last segment, so
a bare ``cache`` covers ``functools.cache`` while a configured
``functools.cache`` matches only the prefixed spelling.
"""

DEFAULT_DUNDERS: tuple[str, ...] = (
    "__eq__",
    "__hash__",
    "__repr__",
    "__str__",
    "__len__",
)
"""Dunder methods callers assume are free of side effects.

``check.builtin.pure_decorator_contracts.DEFAULT_DUNDERS``, copied verbatim.
"""

DEFAULT_ALLOW: tuple[str, ...] = (
    "logging.getLogger",
    "warnings.filterwarnings",
    "warnings.simplefilter",
)
"""Conventional import-time calls ``import-time-side-effects`` never flags.

``check.builtin.import_time_side_effects.DEFAULT_ALLOW``, copied verbatim. A
configured ``allow`` **extends** this list rather than replacing it — the frozen
rule's ``list(DEFAULT_ALLOW) + _as_str_list(options.get("allow"))`` — so
``logging.getLogger(__name__)`` stays silent even in a project that configures
its own allowances, and even when ``extra-impure`` would otherwise match it.
"""


# ---------------------------------------------------------------------------
# pure-decorator-contracts
# ---------------------------------------------------------------------------


def _contract_for(decorators: frozenset[str], dunders: frozenset[str]) -> Expr:
    """``check.builtin.pure_decorator_contracts._contract_for``, as an opaque.

    Returns the contract's name — the string the message quotes — or ``None``
    when the symbol is under none. An opaque rather than a disjunction of
    clauses because the answer is a *name*, not a verdict: the rule reports
    which contract was broken, and reconstructing that from a set of parallel
    boolean clauses would put the wording back into the selection.

    Two behaviours the frozen loop gets from its shape and the copy preserves:

    * a decorator contract **wins over** a dunder contract, because the loop
      over ``symbol.decorators`` returns before the dunder test is reached — an
      ``@property``-decorated ``__len__`` is reported once, as ``@property``;
    * the message quotes the decorator head **as written**, so
      ``@functools.lru_cache(maxsize=None)`` words as ``@functools.lru_cache``
      and is not normalized to ``@lru_cache``. Membership is tested on the head
      and on its last dotted segment; the *display* uses the head.

    The dunder arm is reached only for :attr:`~pypeeker.models.SymbolKind.METHOD`
    — a module-level ``def __eq__`` is a FUNCTION and is under no contract.
    """

    @opaque("purity-contract-for", reads=("decorators", "kind", "name"))
    def _contract(record: Any) -> str | None:
        for raw in record.decorators:
            head = raw.split("(", 1)[0].strip()
            if head in decorators or head.rsplit(".", 1)[-1] in decorators:
                return f"@{head}"
        if record.kind is SymbolKind.METHOD and record.name in dunders:
            return f"{record.name}"
        return None

    return _contract


def _allow_by_symbol_id(patterns: tuple[str, ...]) -> Expr:
    """``pure-decorator-contracts``' ``allow``: an fnmatch against the symbol id.

    The frozen ``any(fnmatch.fnmatchcase(symbol.symbol_id, pattern) ...)``
    written in the grammar rather than smuggled into an opaque —
    :meth:`~pypeeker.dsl.Expr.matches` *is* ``fnmatchcase``. With no patterns
    this is an empty disjunction, which is ``False``, and the clause is applied
    unconditionally for that reason: the frozen rule evaluates ``any(...)`` on
    every contract-bearing symbol whether or not anything is configured.
    """
    return any_of(*(row.symbol_id.matches(pattern) for pattern in patterns))


def pure_decorator_contracts(options: Mapping[str, Any]) -> Selection:
    """Functions under a purity contract that the purity analysis finds impure.

    The frozen rule's four guards in its order: the symbol kind, the contract,
    the ``allow`` list, and only then the impurity. The fact read is last
    because :data:`pypeeker.dsl.sweeps.IMPURITY` is a lazy table over a
    transitive call-graph walk — the frozen ``impurities(...)`` call sits below
    the same three ``continue`` statements for the same reason, so the port's
    cost model is the frozen one.

    The reported confidence comes from the fact: ``HEURISTIC`` when every
    observation rests on a receiver the binder could not classify, ``DECLARED``
    otherwise. This rule does not restate it.

    The params are :func:`~pypeeker.dsl.sweeps.purity_params` of an **empty**
    mapping, and that is load-bearing rather than lazy. The frozen rule calls
    ``impurities(...)`` with **no policy argument** at all, so it gets
    ``DEFAULT_POLICY``; ``purity_params({})`` is ``((), ())``, which
    :func:`~pypeeker.dsl.sweeps._policy` returns ``DEFAULT_POLICY`` for by
    identity. Passing this rule's own ``options`` instead would look harmless
    and be silently wrong: ``purity_params`` reads ``allow``, and this rule's
    ``allow`` is a list of fnmatch patterns over *symbol ids*, which
    :meth:`~pypeeker.analysis.purity.PurityPolicy.extended` would subtract from
    every denylist — defanging the purity policy for the whole project. The
    empty params also share the memo key ``no-impure-functions`` uses at its
    defaults, so the two rules share one transitive walk.

    Options (``[tool.pypeeker.pure-decorator-contracts]``):
        ``decorators`` — replace the pure-contract decorator list.
        ``dunders``    — replace the contract-dunder list.
        ``allow``      — fnmatch patterns over the symbol id; matching symbols
                         are never flagged.
    """
    decorators = frozenset(as_str_list(options.get("decorators")) or DEFAULT_DECORATORS)
    dunders = frozenset(as_str_list(options.get("dunders")) or DEFAULT_DUNDERS)
    allow = tuple(as_str_list(options.get("allow")))
    impurity = fact_of(IMPURITY, purity_params({})).value
    return (
        symbols()
        .where(row.kind.is_in(SymbolKind.FUNCTION, SymbolKind.METHOD))
        .with_field("contract", _contract_for(decorators, dunders))
        .where(row.contract.is_true())
        .where(not_(_allow_by_symbol_id(allow)))
        .where(impurity.is_true())
        .with_field("impurities", impurity)
    )


# ---------------------------------------------------------------------------
# import-time-side-effects
# ---------------------------------------------------------------------------


def _policy_params(options: Mapping[str, Any]) -> PurityParams:
    """The purity params ``import-time-side-effects`` runs under.

    ``check.builtin.import_time_side_effects._configured_policy`` reads
    ``extra-impure`` and **nothing else** — its docstring says so explicitly:
    ``allow`` is applied rule-side as an fnmatch over call names and module
    paths, which subsumes the exact-name policy removal and additionally
    supports wildcards. So this view drops ``allow`` before
    :func:`~pypeeker.dsl.sweeps.purity_params` can see it; handing it through
    would let a pattern like ``"*:blessed_setup"`` reach
    :meth:`~pypeeker.analysis.purity.PurityPolicy.extended`'s ``allow=``, whose
    set subtraction would remove names from every denylist and make unrelated
    functions look pure.
    """
    return purity_params({"extra-impure": options.get("extra-impure")})


def _import_time_calls() -> Selection:
    """Every call that executes while the module is being imported.

    The frozen loop's two ``continue`` guards, in its order: ``ref.kind !=
    CALL``, then ``ref.in_scope_id not in import_time_scopes``. The second is
    ``row.runs_at_import`` — see the module docstring for why it is a row field
    and not a project-wide set.
    """
    return (
        references().where(row.kind.eq(ReferenceKind.CALL)).where(row.runs_at_import.is_true())
    )


def _bare_call_name() -> Expr:
    """``check.builtin.import_time_side_effects._bare_call_name``, as an opaque.

    The bare name of a builtin or unresolved-bare call, else ``None``. Builtins
    resolve to ``<builtins>.X``; a star-imported or free name stays unresolved
    without a ``":"``. Anything resolved to a project symbol is a different
    shape and answers ``None`` here — which is precisely what makes this the
    partition key: the frozen shape 1 **returns unconditionally** whenever this
    is not ``None``, so parts B and C exclude it.
    """

    @opaque("import-time-bare-name", reads=("symbol_id", "resolved"))
    def _bare(record: Any) -> str | None:
        sid = record.symbol_id
        if is_builtin(sid):
            return builtin_name(sid)
        if not record.resolved and ":" not in sid and not is_unresolved_attr(sid):
            return sid
        return None

    return _bare


def _is_builtin_call() -> Expr:
    """Whether the call resolved to a real builtin, for the shape-1 tier split.

    The frozen ``Confidence.DECLARED if is_builtin(ref.symbol_id) else
    Confidence.HEURISTIC``: matching ``<builtins>.open`` by name is a structural
    fact, while matching a free name the binder could not resolve is guesswork.
    """

    @opaque("import-time-is-builtin", reads=("symbol_id",))
    def _builtin(record: Any) -> bool:
        return is_builtin(record.symbol_id)

    return _builtin


def _builtin_match_evidence() -> Expr:
    """Always matches; reports ``HEURISTIC`` where the call did not resolve to a builtin.

    The frozen tier for shape 1, and deliberately **not** spelled as a
    :func:`~pypeeker.dsl.weakened_when`. :class:`~pypeeker.dsl.Weaken` is an
    *inventoried* node: :data:`pypeeker.dsl.DYNAMIC_ACCESS_WEAKENED_RULES`
    enumerates the five visibility rules the frozen engine downgrades through
    ``check.rules._dynamic_access_confidence``, and
    ``tests/test_dsl_visibility_rules.py`` asserts that exactly those five
    expressions carry one. This rule reaches a tier of its own, by its own route
    — ``is_builtin(ref.symbol_id)``, inline in the frozen ``_describe_call`` —
    so claiming the node would claim membership of a family it is not in. That
    is the same call :func:`pypeeker.dsl.sweeps.unused_import_rows` makes for
    ``unused-imports``, which carries its tier on the row instead of as a sixth
    ``Weaken``.

    What is left is the lattice itself, which needs no special node. A
    disjunction meets over the branches that **hold**, and these two branches
    partition every row, so exactly one holds: the value is unconditionally
    ``True`` — this filters nothing, exactly like ``Weaken`` — and the reported
    evidence is that branch's. The literals are what carry the levels, which is
    what :class:`~pypeeker.dsl.Const`'s own ``confidence`` is for.

    A resolved builtin is ``DECLARED``: ``<builtins>.open`` names the builtin
    structurally. A bare name the binder never resolved is ``HEURISTIC``: a
    star-imported or free name that merely *spells* a denylisted builtin, which
    is a guess and reported as one.
    """
    return any_of(
        all_of(row.is_builtin_call.is_true(), Const(True)),
        all_of(row.is_builtin_call.eq(False), Const(True, Confidence.HEURISTIC)),
    )


def _qualified_call_name() -> Expr:
    """``check.builtin.import_time_side_effects._qualified_call_name``, as an opaque.

    The dotted name of an IMPORT-rooted attribute call —
    ``imported_from + chain[1:] + leaf`` — which is what catches aliased imports
    (``import os as o``; ``o.getcwd()`` words as ``os.getcwd``) and from-imports
    (``from os import path``; ``path.exists(p)`` words as ``os.path.exists``).

    Two frozen guards are subsumed rather than dropped. ``receiver_root_symbol_id
    is None`` and the ``symbols_by_id`` miss both leave
    ``row.receiver_root_kind`` at ``None``, which the ``is not
    SymbolKind.IMPORT`` test rejects — the field comes from the same per-file
    ``{s.symbol_id: s for s in index.symbols}`` lookup the frozen rule builds.
    ``ref.receiver_chain is None`` is unreachable: ``binder.references``'
    ``receiver_metadata`` returns a non-``None`` root only together with a
    non-empty chain, so no row can reach here with a root and no chain.

    The leaf is ``check.builtin.import_time_side_effects._call_leaf``, copied
    rather than shared. The frozen engine carries two copies of it and
    :func:`pypeeker.dsl.mutation._leaf_method` ported the other; lifting one
    into a shared home would rename an opaque that appears in ``--why``
    derivation trees for no behavioural gain.
    """

    @opaque(
        "import-time-qualified-name",
        reads=(
            "receiver_root_kind",
            "receiver_root_imported_from",
            "receiver_chain",
            "symbol_id",
            "is_attribute_access",
        ),
    )
    def _qualified(record: Any) -> str | None:
        if record.receiver_root_kind is not SymbolKind.IMPORT:
            return None
        if not record.receiver_root_imported_from:
            return None
        sid = record.symbol_id
        leaf: str | None = None
        if is_unresolved_attr(sid):
            leaf = unresolved_attr_name(sid)
        elif record.is_attribute_access:
            if "." in sid:
                leaf = sid.rsplit(".", 1)[-1]
            elif ":" in sid:
                leaf = sid.rsplit(":", 1)[-1]
        if leaf is None:
            return None
        return ".".join([record.receiver_root_imported_from, *record.receiver_chain[1:], leaf])

    return _qualified


def _allow_clause(options: Mapping[str, Any]) -> Expr:
    """``check.builtin.import_time_side_effects._allowed``, as a disjunction.

    Each pattern is fnmatched against the **called name** and against the
    **calling module's path**, and the two clauses are interleaved per pattern
    so the written order matches the frozen ``any(...)`` — fork #3 makes written
    order normative even where every clause is pure.

    ``row.call_name`` is whatever the part in hand named the call, matching the
    frozen ``name`` that ``_describe_call`` returned: the bare name in part A,
    the dotted qualified name in part B, the resolved project symbol id in part
    C. ``row.module_scope_id`` is the frozen ``_module_path`` — the file's
    MODULE *scope* id, deliberately not ``row.module``, which falls back to a
    file path where the binder emitted no MODULE symbol.

    Applied **last** in all three parts, which is the frozen order and not an
    optimization to undo: ``_allowed`` runs after ``_describe_call`` has already
    paid for resolution and for the impurity walk.
    """
    patterns = (*DEFAULT_ALLOW, *as_str_list(options.get("allow")))
    return any_of(
        *(
            clause
            for pattern in patterns
            for clause in (
                row.call_name.matches(pattern),
                row.module_scope_id.matches(pattern),
            )
        )
    )


def _not_a_bare_call() -> Selection:
    """The shape-1 exclusion parts B and C share.

    ``_describe_call`` returns from shape 1 unconditionally when the bare name
    is non-``None``, so a row with one never reaches the later arms whatever it
    matched. Written as a field plus a clause rather than folded into each
    part's own opaque so the exclusion is visible in the derivation tree that
    explains why a row was *not* reported.
    """
    return (
        _import_time_calls()
        .with_field("bare_name", _bare_call_name())
        .where(not_(row.bare_name.is_true()))
    )


def import_time_builtin_call(options: Mapping[str, Any]) -> Selection:
    """Shape 1: a bare or builtin call matching the impure-builtin policy.

    ``print(...)``, ``open(...)``, and any bare name a project adds through
    ``extra-impure``. The tier is the frozen split and is a *label*, not a
    filter: a resolved builtin is ``DECLARED``, an unresolved free name matched
    only by its spelling is ``HEURISTIC``, and neither is dropped — see
    :func:`_builtin_match_evidence`.

    Options (``[tool.pypeeker.import-time-side-effects]``):
        ``allow``        — fnmatch patterns over the called name or the calling
                           module path; extends :data:`DEFAULT_ALLOW`.
        ``extra-impure`` — bare names join the builtin denylist, dotted names
                           the module denylist.
    """
    builtins = tuple(sorted(impure_builtin_names(_policy_params(options))))
    return (
        _import_time_calls()
        .with_field("call_name", _bare_call_name())
        .where(row.call_name.is_in(*builtins))
        .with_field("is_builtin_call", _is_builtin_call())
        .where(_builtin_match_evidence())
        .where(not_(_allow_clause(options)))
    )


def import_time_module_call(options: Mapping[str, Any]) -> Selection:
    """Shape 2: a module-qualified call matching the module-impure policy.

    ``subprocess.run(...)``, ``time.time()``, ``os.path.exists(...)``. Always
    ``DECLARED``: the name was assembled from an import the binder recorded, not
    guessed. Reached only by rows shape 1 did not claim — see
    :func:`_not_a_bare_call`.
    """
    module_impure = tuple(sorted(module_impure_names(_policy_params(options))))
    return (
        _not_a_bare_call()
        .with_field("call_name", _qualified_call_name())
        .where(row.call_name.is_in(*module_impure))
        .where(not_(_allow_clause(options)))
    )


def import_time_project_call(options: Mapping[str, Any]) -> Selection:
    """Shape 3: a call to a project-internal function the purity analysis finds impure.

    The frozen ``else`` arm, and the only one that leaves the file: the call is
    resolved through the cross-module resolver, the target must be a project
    FUNCTION or METHOD, and the finding exists only if
    :func:`pypeeker.analysis.impurities` reports something about it.

    Two exclusions put this arm after the other two: rows with a bare name
    (shape 1 returned), and rows whose qualified name is **in** the module
    denylist (shape 2 reported). A qualified name that misses the table falls
    through in the frozen ``elif`` and must fall through here, which is why the
    clause negates the membership test and not the name.

    ``call_name`` is :data:`~pypeeker.dsl.columns.DEFINITION_ID` — literally the
    frozen ``context.resolver.resolve_reference(ref)`` — and it is what the
    message quotes and what ``allow`` patterns match, matching the frozen
    ``canonical``. The ``definition_kind`` membership test is the frozen
    ``project_functions.get(canonical) is None`` guard, since that table holds
    exactly the project's FUNCTION and METHOD symbols; its
    :attr:`~pypeeker.models.SymbolKind.value` is also the one word of the
    message that varies.

    The impurity is read **about the resolved definition**, not about the row —
    ``fact_of(IMPURITY, ...).about(row.call_name)``. The finding stays anchored
    at the call site, with its own path and line, while the fact is read where
    it is defined; see :meth:`pypeeker.dsl.FactAccess.about`. The read is last
    for the usual reason: the lazy table is paid only by rows the cheap clauses
    already accepted, exactly like the frozen ``impurity_cache`` behind three
    ``return None`` statements.

    Unlike the other two parts this one runs under the configured policy, which
    is the frozen ``impurities(..., policy=policy)`` — an ``extra-impure`` name
    makes a project function impure transitively, not just at the call site.
    """
    params = _policy_params(options)
    module_impure = tuple(sorted(module_impure_names(params)))
    impurity = fact_of(IMPURITY, params).about(row.call_name).value
    return (
        _not_a_bare_call()
        .with_field("qualified_name", _qualified_call_name())
        .where(not_(row.qualified_name.is_in(*module_impure)))
        .with_field("call_name", column_of(DEFINITION_ID))
        .with_field("definition_kind", column_of(DEFINITION_KIND))
        .where(row.definition_kind.is_in(SymbolKind.FUNCTION, SymbolKind.METHOD))
        .where(impurity.is_true())
        .where(not_(_allow_clause(options)))
    )
