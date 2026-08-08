"""Rules as expressions: a selection, plus the message its rows render to.

Phase 3 of the rewrite recorded in ``dsl-rewrite.md``. A rule in the old engine
is a hand-written Python function that walks a :class:`~pypeeker.models.FileIndex`
and appends ``Violation`` objects. A rule here is two pieces of data: the
selection that decides *which* rows are findings, and a ``str.format`` template
that decides how one row is *worded*. Nothing else. The template is data rather
than a callable on purpose — fork #9 says opacity must be declared, and a
callable message would put rule semantics somewhere the derivation tree cannot
describe. A rule whose wording cannot be written as a template over the row's
visible fields is therefore not portable yet, and the honest response is to
leave it unclaimed in ``scripts/parity-manifest.toml`` rather than to smuggle
the computation into a lambda.

One row is one finding. :meth:`pypeeker.dsl.Selection.rows` has no fan-out
stage, so a rule that emits N findings from one anchor (``docstring-drift``
emits one per ghost parameter) is structurally inexpressible today. Building a
bespoke fan-out into this module would move rule semantics outside the DSL,
which is the thing the program exists to stop.

Some rules quantify more than once. ``import-boundaries`` reports over import
symbols, over modules and over the project's configuration, with three
different messages, and the frozen engine literally concatenates three loops.
:class:`MultiPartRule` is that shape and no more: an ordered tuple of
:class:`RulePart`, each still one selection and one template, each still one
finding per row. It is not a fan-out — nothing about which rows fire or how
they are worded moves out of the DSL.

Either shape may declare itself **switched off by configuration** by building
``None`` instead of a selection: one part of ``import-boundaries`` when
``report-unused-allowances`` is false, the whole of ``no-impure-functions``
when it has no ``include`` patterns. Both are cases where the frozen engine
returns before looking at anything, and an always-false predicate would claim
it looked at every row and rejected each one.

**Nothing registers at import time**, for the same two reasons
:mod:`pypeeker.dsl.library` gives: ``import-time-side-effects`` is a gated
self-lint rule, and a module that mutates a shared registry merely because
somebody imported it makes the moment of registration invisible in a stack
trace. :data:`RULES` is pure data construction; the trait providers a rule's
expression may reach for are installed by
:func:`pypeeker.dsl.install_expressions`, explicitly, by the caller.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import UnknownExpressionError
from pypeeker.dsl.expr import Expr, all_of, any_of, not_, row
from pypeeker.dsl.facts import fact_of
from pypeeker.dsl.library import TUPLE_CANDIDATE
from pypeeker.dsl.selection import Selection, references, symbols
from pypeeker.dsl.sweeps import (
    IMPURITY,
    allowance_rows,
    as_str_list,
    boundary_params,
    cycle_params,
    cycle_rows,
    import_rows,
    purity_params,
    unit_rows,
)
from pypeeker.models import UNRESOLVED_PREFIX, Confidence, SymbolKind, Visibility


@dataclass(frozen=True)
class Finding:
    """One reported row: what fired, where, in what words, on what evidence.

    The same five facts the differential oracle compares, and deliberately no
    more — no remedy, no fix id, no baseline key. Those arrive in phase 4 with
    the mutation terminals; a finding in the read half is an observation.
    """

    rule: str
    path: str
    line: int
    message: str
    confidence: Confidence


@dataclass(frozen=True)
class DslRule:
    """A rule: a selection built from options, and the wording of one row.

    ``build`` takes the rule's option table rather than closing over it so
    :data:`RULES` can stay a constant — rule *behaviour* is configuration
    dependent (``require-docstrings`` filters on configured kinds), but rule
    *identity* is not.

    ``message`` is a :meth:`str.format` template over the row's visible fields.
    ``{name}`` reads the field; ``{kind.value}`` unwraps an enum exactly as the
    old engine's f-strings do.

    ``build`` may return ``None``, meaning **this rule is switched off by its
    own configuration** — the same off-switch :class:`RulePart` has, because
    being switched off is a property of a quantification and not of how many a
    rule has. ``no-impure-functions`` with no ``include`` patterns is the
    motivating case: the old rule returns ``[]`` before looking at anything, and
    an always-false predicate would claim it looked at every function in the
    project and rejected each one.
    """

    rule_id: str
    build: Callable[[Mapping[str, Any]], Selection | None]
    message: str

    def findings(self, options: Mapping[str, Any], corpus: Corpus) -> list[Finding]:
        """Run this rule over ``corpus``, one :class:`Finding` per surviving row.

        Args:
            options: the rule's option table, as the old engine's
                ``rule_options[rule_id]`` would have supplied it.
            corpus: the indexes to quantify over.

        Returns:
            The findings in the order the selection produced its rows, which is
            index order and therefore stable across runs — or ``[]`` when
            ``build`` declared the rule switched off.
        """
        selection = self.build(options)
        if selection is None:
            return []
        return _render(self.rule_id, self.message, selection, corpus)


def _render(
    rule_id: str, message: str, selection: Selection, corpus: Corpus
) -> list[Finding]:
    """Run ``selection`` and word each surviving row with ``message``.

    Shared by :class:`DslRule` and :class:`MultiPartRule` because a finding is
    a finding however many parts a rule has: one constructor, one place where
    ``file_path``/``line`` are read off the row, one place the template is
    applied. Two copies would be two places for the finding shape to drift.
    """
    return [
        Finding(
            rule=rule_id,
            path=match.fields["file_path"],
            line=match.fields["line"],
            message=message.format(**match.fields),
            confidence=match.confidence,
        )
        for match in selection.rows(corpus)
    ]


@dataclass(frozen=True)
class RulePart:
    """One quantification of a rule that has more than one: a selection and its wording.

    ``build`` may return ``None``, meaning **this part is switched off by
    configuration**. That is how ``import-boundaries`` expresses
    ``report-unused-allowances = false`` — the old rule simply does not run
    that pass — without inventing an always-false predicate, which would be a
    lie about what the rule looked at and would still cost a sweep.
    """

    build: Callable[[Mapping[str, Any]], Selection | None]
    message: str


@dataclass(frozen=True)
class MultiPartRule:
    """A rule whose findings come from several selections under one rule id.

    :class:`DslRule` is one selection and one template, and
    :meth:`pypeeker.dsl.Selection.rows` has no fan-out — so a rule that reports
    over genuinely different *row shapes* cannot be written as one. The
    motivating case is ``import-boundaries``, which quantifies over import
    symbols, over modules, and over the project's configuration, with three
    different messages. Those are three quantifications, not one selection with
    a branch in its wording, and the old engine literally concatenates three
    loops.

    This is deliberately **not** a general fan-out: each part is still one
    selection producing one finding per row, so every rule stays a ∀-query and
    nothing about *which* rows fire or *how* they are worded moves out of the
    DSL. The parts run and concatenate in written order; a part whose ``build``
    returns ``None`` contributes nothing.

    One rule id across all parts, because the old engine emits one ``rule``
    string and both the baseline and the differential oracle key on it.
    """

    rule_id: str
    parts: tuple[RulePart, ...]

    def findings(self, options: Mapping[str, Any], corpus: Corpus) -> list[Finding]:
        """Run every enabled part over ``corpus``, concatenated in written order.

        Args:
            options: the rule's option table, as the old engine's
                ``rule_options[rule_id]`` would have supplied it. Each part
                reads it independently — that is how one part switches itself
                off while its siblings keep running.
            corpus: the indexes to quantify over.

        Returns:
            Every part's findings, in part order and, within a part, in row
            order.
        """
        found: list[Finding] = []
        for part in self.parts:
            selection = part.build(options)
            if selection is None:
                continue
            found.extend(_render(self.rule_id, part.message, selection, corpus))
        return found


PortedRule = DslRule | MultiPartRule
"""Either rule shape. :data:`RULES` holds both, and everything downstream duck-types.

``pypeeker.dsl.differential`` calls ``rule.findings(options, corpus)`` without
asking what it got, which is why adding a second shape needed no change there.
"""


def _prefer_tuple(options: Mapping[str, Any]) -> Selection:
    """Function-local list bindings that are never mutated and never escape.

    Takes no options: the old rule reads none either. The whole predicate is
    :data:`pypeeker.dsl.TUPLE_CANDIDATE`, whose third clause is a ``.at()``
    meta-read — which is what keeps the reported confidence at ``DECLARED``,
    per the ``dsl-rewrite.md`` ledger's spec note. A port that meets to
    ``INFERRED`` here is wrong, not divergent.
    """
    del options
    return symbols().where(TUPLE_CANDIDATE)


_DOCSTRING_KINDS_DEFAULT: tuple[str, ...] = ("function", "method", "class")
_DOCSTRING_VISIBILITY_DEFAULT: tuple[str, ...] = ("public",)


def _enum_set(raw: Any, enum_cls: type) -> tuple[Any, ...]:
    """Coerce a configured option into enum members, **dropping what will not parse**.

    A faithful re-implementation of ``check.rules._as_enum_set``, silent drop
    included. That silence is load-bearing, not sloppiness, and a "cleanup"
    here breaks differential parity:

    ``check.config`` copies the whole project-wide ``[tool.pypeeker.visibility]``
    table into *every* enabled rule's options under the reserved key
    ``visibility``. ``require-docstrings`` reads its own ``visibility`` option
    through this coercion, so on such a project the raw value is a **dict** of
    unrelated visibility-policy keys (``allow-decorators`` and friends).
    ``list(dict)`` yields those keys, none of them parse as a
    :class:`~pypeeker.models.Visibility`, every one is dropped, and the
    resulting set is **empty** — so the rule reports nothing at all. Measured on
    pypeeker itself: 1 finding under a minimal config, 0 under a config carrying
    that section. A port that "sensibly" fell back to the default on an
    unparseable option would emit that one extra finding and fail the oracle.

    Returns a tuple rather than the old engine's ``frozenset`` because the only
    consumer is :meth:`Expr.is_in`, whose ``in`` test is membership either way;
    a tuple keeps written order inspectable in a derivation's ``rhs``.
    """
    values: Iterable[Any] = [raw] if isinstance(raw, str) else list(raw)
    out: list[Any] = []
    for value in values:
        try:
            member = enum_cls(value)
        except ValueError:
            continue
        if member not in out:
            out.append(member)
    return tuple(out)


def _require_docstrings(options: Mapping[str, Any]) -> Selection:
    """Symbols of the configured kinds and visibilities that carry no docstring.

    Three clauses in the old rule's written order — kind, then visibility, then
    the missing docstring — because fork #3 makes written order normative and
    an opaque-free conjunction is still worth keeping legible against its
    source. ``row.docstring.eq(None)`` is exactly the old rule's
    ``symbol.docstring is not None: continue``: the binder stores ``None`` for
    "no docstring", the field is present on every symbols row, and the read
    therefore reports DECLARED.

    Options:
        ``kinds``      — SymbolKind values (default function/method/class)
        ``visibility`` — Visibility values (default public only)

    Both go through :func:`_enum_set`; read its note before touching either.
    """
    kinds = _enum_set(options.get("kinds", _DOCSTRING_KINDS_DEFAULT), SymbolKind)
    visibilities = _enum_set(
        options.get("visibility", _DOCSTRING_VISIBILITY_DEFAULT), Visibility
    )
    return symbols().where(
        all_of(
            row.kind.is_in(*kinds),
            row.visibility.is_in(*visibilities),
            row.docstring.eq(None),
        )
    )


def _no_unresolved_refs(options: Mapping[str, Any]) -> Selection:
    """References the binder could not bind, minus the attribute-chain noise.

    Takes no options; the old rule reads none either. Two clauses in its
    written order:

    ``row.resolved.eq(False)`` is the old rule's ``if ref.resolved: continue``.
    It also excludes builtins for free — the binder lands those on
    ``<builtins>.*`` with ``resolved=True``.

    ``not_(row.symbol_id.startswith(UNRESOLVED_PREFIX))`` is
    ``models.is_unresolved_attr``, spelled out rather than wrapped in an opaque
    because the whole of that predicate is one ``startswith`` against a public
    constant, and an opaque would trade an inspectable node for a declared
    read that says less. These are attribute chains hanging off a receiver
    already known to be unresolved: reporting them again is noise about a
    problem the first finding already named.

    Both clauses read model fields, which report ``DECLARED``, over references
    rows, which are intrinsically ``DECLARED`` — so the finding lands at
    ``DECLARED``, matching the old rule, which passes no confidence at all.
    """
    del options
    return references().where(
        all_of(
            row.resolved.eq(False),
            not_(row.symbol_id.startswith(UNRESOLVED_PREFIX)),
        )
    )


def _boundary_imports(options: Mapping[str, Any]) -> Selection:
    """Imports whose origin package the importer's allow-list does not permit.

    The old rule's first quantification (``check.rules.import_boundaries``, the
    ``for symbol in index.symbols`` loop), quantified over the thing that loop
    iterates: one row per import **occurrence** in a policed package. That is
    **not** the imports universe keyed by a fact. A symbol id is
    ``<module>:<local>`` and a module id is not injective over indexed files, so
    two files sharing a module id bind two different import symbols under one
    id; a verdict table keyed on it holds one entry where the universe yields
    two rows, and the second row reads the first's verdict — firing on a file
    whose imports are all permitted, or, when both halves violate, naming the
    wrong package in a finding whose count looks right. An earlier shape of this
    part did exactly that. See :func:`pypeeker.dsl.sweeps.import_rows`.

    Two clauses in the frozen rule's written order:

    ``row.imported_from.is_true()`` is ``if ... or not symbol.imported_from:
    continue``. It is as defensive here as it is there — today's binder records
    a dotted name for every import form it recognises, so no row it produces
    fails this — and it is kept for the same reason the frozen rule keeps it:
    an import that names nothing has no package to charge, and a port that
    silently dropped the guard would differ the moment the binder gained a form
    that does.

    ``row.violates.is_true()`` is everything the sweep collapsed: the origin
    resolved through re-export chains, and found to be neither the importer's
    own package nor one its allow-list permits. The sweep carries the
    occurrences it cleared as well as the ones it condemned — a permitted
    import is a row with ``violates`` false, not an absent row — so this clause
    is a rejection the DSL performs rather than one already made for it.

    The reported confidence is **carried, not restated here**: each row is
    intrinsically ``HEURISTIC`` for a dynamically-recovered binding and
    ``DECLARED`` otherwise, which is exactly the old rule's
    ``symbol.import_confidence or Confidence.DECLARED``, and the meet reports it.
    """
    return Selection(import_rows(boundary_params(options))).where(
        all_of(row.imported_from.is_true(), row.violates.is_true())
    )


def _boundary_undeclared(options: Mapping[str, Any]) -> Selection:
    """Top-level packages missing from both ``allow`` and ``unconstrained``, under ``strict``.

    The old rule's ``_strict_undeclared_violations``, quantified over the thing
    that rule quantifies over: its ``units`` census, one entry per top-level
    package. That is **not** the modules universe. A modules row is one indexed
    *file*, and a module id does not identify a file uniquely —
    ``paths.module_path_from`` maps ``proj/dup.py`` and ``proj/dup/__init__.py``
    onto one id, and a multi-root ``src`` maps ``src/dup/mod.py`` and
    ``lib/dup/mod.py`` onto one id. Electing a representative and keying the
    answer on its module id, as an earlier shape of this part did, therefore
    reported the package once per colliding file. One row per package cannot.
    See :func:`pypeeker.dsl.sweeps.unit_rows`.

    Fork #8's "``representative_file`` and friends are primitive traits, not
    language features" is satisfied by putting the *choice* of representative
    in the sweep and the two rejections here, in the old rule's written order:

    ``not_(row.unit.startswith("__"))`` is the census's ``if not
    importer_pkg.startswith("__")`` — ``__main__`` and friends are not layered
    units.

    ``row.declared.eq(False)`` is ``if unit in allow or unit in unconstrained:
    continue``.

    Under ``strict = false`` the sweep produces no census rows at all and this
    part yields nothing, which is the old rule's ``if strict:`` guard arrived at
    through the data.
    """
    return Selection(unit_rows(boundary_params(options))).where(
        all_of(not_(row.unit.startswith("__")), row.declared.eq(False))
    )


def _boundary_unused_allowances(options: Mapping[str, Any]) -> Selection | None:
    """Declared allow pairs no real import in the project exercises.

    The old rule's ``_unused_allowance_violations``, and the one part of the
    family that quantifies over the project's **configuration** — there is no
    model row behind "this pair is never used", which is precisely what
    :func:`pypeeker.dsl.fact_source` exists for.

    Returns ``None`` when ``report-unused-allowances`` is off: the old rule
    does not run this pass at all, and an always-false predicate would both
    misdescribe what the rule looked at and still pay for the sweep.
    """
    if not options.get("report-unused-allowances"):
        return None
    return Selection(allowance_rows(boundary_params(options))).where(row.exercised.eq(False))


def _import_cycles(options: Mapping[str, Any]) -> Selection:
    """Modules that report a module-load import cycle they are part of.

    The old rule (``check.builtin.no_import_cycles``) is one loop over
    strongly-connected components, which fork #11 keeps in Python: an SCC is a
    graph fixpoint and no per-row expression converges on one. What is left for
    the DSL is the quantification — and the domain it quantifies over is
    **components**, not files. Quantifying over the modules universe instead and
    keying the sweep's answer on the reporting module id reported one finding
    per file sharing that id, with the extra findings carrying a line copied
    from another file's import site; see
    :func:`pypeeker.dsl.sweeps.cycle_rows`.

    Two clauses, the frozen rule's two ``continue`` statements in its order:

    ``row.is_cycle.is_true()`` is ``if len(component) < 2: continue`` — a lone
    module is a component of the graph but not a cycle, self-imports having
    already been dropped when the edges were built.

    ``row.allowed.eq(False)`` is ``if frozenset(members) in allowed:
    continue`` — the configured suppression, matched on the whole member set.

    Options (``[tool.pypeeker.no-import-cycles]``):
        ``allow`` — accepted cycles, each a list of the dotted module names in
                    it, normalized by :func:`pypeeker.dsl.sweeps.cycle_params`.
    """
    return Selection(cycle_rows(cycle_params(options))).where(
        all_of(row.is_cycle.is_true(), row.allowed.eq(False))
    )


def _matches_any(patterns: Iterable[str]) -> Expr:
    """The old rule's ``_matches_any``: an fnmatch against the id or its module.

    ``check.rules._matches_any`` tests each pattern against ``symbol_id`` and
    against ``symbol_id.split(":", 1)[0]``, and the clauses are interleaved
    per pattern here so the written order matches — fork #3 makes written order
    normative even where every clause is pure.

    ``row.id_module`` **is** that ``split``, computed on the symbols row rather
    than in the rule because the grammar has no string operator for it. It is
    deliberately not ``row.module``: that field answers "what is this file
    called" and falls back to the file path when the binder emitted no
    ``MODULE`` symbol, which happens whenever a configured source root is
    itself a package. Matching a pattern against that fallback fires on rows
    the frozen engine leaves alone — ``include = ["src*"]`` would flag every
    function in ``src/__init__.py`` — so the two fields are kept apart. See
    :func:`pypeeker.dsl.universes._symbol_record`.

    The alternative — moving the include/exclude filter into the sweep — would
    take the rule's *scoping* out of the DSL, which is the opposite of the
    point.
    """
    return any_of(
        *(
            clause
            for pattern in patterns
            for clause in (row.symbol_id.matches(pattern), row.id_module.matches(pattern))
        )
    )


def _impure_functions(options: Mapping[str, Any]) -> Selection | None:
    """In-scope functions and methods the purity analysis finds impure.

    The old rule's clause order, kept: kind, then ``include``, then ``exclude``,
    then the impurity itself. The order is not cosmetic — the fact read is last
    because :data:`pypeeker.dsl.sweeps.IMPURITY` is a lazy table over a
    transitive call-graph walk, so a row rejected by a cheap clause never pays
    for it. That reproduces the old rule's cost exactly, where the ``continue``
    statements sit above the ``impurities(...)`` call for the same reason.

    Returns ``None`` — the part is switched off — when ``include`` is empty.
    That is the old rule's ``if not include: return []``, and it is a genuine
    law rather than an optimization: purity analysis is heuristic, so enabling
    the rule without scoping it is *deliberately* a no-op. Expressing it as an
    always-false predicate would claim the rule looked at every function and
    rejected each one, which is not what happened.

    The reported confidence comes from the fact: ``HEURISTIC`` when every
    observation rests on a receiver the binder could not classify, ``DECLARED``
    otherwise. This rule does not restate it — see
    :mod:`pypeeker.dsl.sweeps` on why purity is the one sweep that declares its
    own tier.

    Options (``[tool.pypeeker.no-impure-functions]``):
        ``include``      — fnmatch patterns over the symbol id or its module.
                           **Required**; empty means the rule is off.
        ``exclude``      — patterns subtracted from ``include`` (exclude wins).
        ``extra-impure`` — dotted names join the module denylist, bare names
                           the builtin denylist.
        ``allow``        — names removed from every denylist.
    """
    include = as_str_list(options.get("include"))
    if not include:
        return None
    exclude = as_str_list(options.get("exclude"))
    impurity = fact_of(IMPURITY, purity_params(options)).value
    selection = symbols().where(
        all_of(
            row.kind.is_in(SymbolKind.FUNCTION, SymbolKind.METHOD),
            _matches_any(include),
        )
    )
    if exclude:
        selection = selection.where(not_(_matches_any(exclude)))
    return selection.where(impurity.is_true()).with_field("impurities", impurity)


RULES: Mapping[str, PortedRule] = MappingProxyType({
    "prefer-tuple": DslRule(
        rule_id="prefer-tuple",
        build=_prefer_tuple,
        message="list '{name}' is never mutated — consider a tuple",
    ),
    "require-docstrings": DslRule(
        rule_id="require-docstrings",
        build=_require_docstrings,
        message="{visibility.value} {kind.value} '{name}' has no docstring",
    ),
    "no-unresolved-refs": DslRule(
        rule_id="no-unresolved-refs",
        build=_no_unresolved_refs,
        message="unresolved reference: '{symbol_id}'",
    ),
    "import-boundaries": MultiPartRule(
        rule_id="import-boundaries",
        parts=(
            RulePart(
                build=_boundary_imports,
                message=(
                    "package '{importer_pkg}' may not import '{dep_pkg}' "
                    "({detail} '{imported_from}')"
                ),
            ),
            RulePart(
                build=_boundary_undeclared,
                message=(
                    "package '{unit}' is not declared in import-boundaries "
                    "(add it to [tool.pypeeker.import-boundaries.allow] or to "
                    "the 'unconstrained' list)"
                ),
            ),
            RulePart(
                build=_boundary_unused_allowances,
                message=(
                    "unused import-boundaries allowance: '{importer_pkg}' "
                    "is permitted to import '{dep_pkg}' but never does"
                ),
            ),
        ),
    ),
    "no-import-cycles": DslRule(
        rule_id="no-import-cycles",
        build=_import_cycles,
        message=(
            "import cycle among modules: {members} — import the type directly, "
            "or extract the shared contract to a sibling module both can "
            "depend on"
        ),
    ),
    "no-impure-functions": DslRule(
        rule_id="no-impure-functions",
        build=_impure_functions,
        message="{kind.value} '{symbol_id}' is impure: {impurities}",
    ),
})
"""Every rule the new engine implements, by its rule id.

A name appears here only once its expression has been written; it appears in
``scripts/parity-manifest.toml``'s ``claimed`` list only once the differential
oracle grades it at parity with the frozen old engine.
"""


def dsl_rule(name: str) -> PortedRule:
    """Look up a ported rule by its rule id.

    Args:
        name: a key of :data:`RULES`.

    Returns:
        The rule registered under ``name``.

    Raises:
        UnknownExpressionError: no ported rule has that id; the message lists
            the ones that do, which is how a caller discovers what the new
            engine currently implements without a second command.
    """
    found = RULES.get(name)
    if found is None:
        raise UnknownExpressionError(name, RULES)
    return found
