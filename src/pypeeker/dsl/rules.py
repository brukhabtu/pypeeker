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

One row is one finding, and that law is not a limit on how many findings a
symbol can produce. ``docstring-drift`` emits one finding per ghost parameter,
so one function can yield several — and the way to say that is **not** a
fan-out stage in :meth:`pypeeker.dsl.Selection.rows`, which would move rule
semantics outside the DSL. It is to notice what the rule quantifies over: not
functions, but *drifted parameters*. Those are rows, produced by the row source
:func:`pypeeker.dsl.sweeps.drift_rows`, and the selection over them is an
ordinary ∀-query yielding exactly one match per row. Each fanned row carries
its own anchor (``<function id>:<param>``, so fork #6's ``(rule_id,
anchor_id)`` baseline key stays unique and fork #5's derived ``fix_id`` comes
out identical to the frozen one), its own evidence, and its own derivation
chain, so ``--why`` answers per emitted finding — per *anchor*, that is: two
fanned rows from one symbol share the clause structure, so their derivation
trees differ in anchor and fields rather than in shape. Nothing in the
selection grammar or the evidence lattice moved to buy that.

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
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import UnknownExpressionError
from pypeeker.dsl.expr import Expr, all_of, any_of, not_, opaque, row
from pypeeker.dsl.facts import fact_of
from pypeeker.dsl.impurity import (
    import_time_builtin_call,
    import_time_module_call,
    import_time_project_call,
    pure_decorator_contracts,
)
from pypeeker.dsl.library import TUPLE_CANDIDATE
from pypeeker.dsl.mutation import (
    argument_attribute_write,
    argument_mutator_call,
    argument_subscript_write,
    global_import_attribute_write,
    global_mutator_call,
    global_outer_scope_write,
    global_rebind,
)
from pypeeker.dsl.selection import (
    Selection,
    references,
    require_mutation_fields,
    symbols,
)
from pypeeker.dsl.sweeps import (
    IMPURITY,
    allowance_rows,
    as_str_list,
    barrel_params,
    barrel_rows,
    boundary_params,
    conforms,
    convention_label,
    cycle_params,
    cycle_rows,
    docstring_params,
    drift_rows,
    import_rows,
    naming_params,
    purity_params,
    star_import_rows,
    suggested_name,
    unit_rows,
    unused_import_rows,
    unused_return_rows,
)
from pypeeker.dsl.terminals import (
    DELETE_SYMBOL,
    REMOVE_IMPORT,
    RENAME_DOCSTRING_PARAM,
    REWRITE_STAR_IMPORT,
    TUPLIFY,
    Mutation,
)
from pypeeker.dsl.visibility import (
    born_private,
    over_exposed_export,
    over_exposed_module_symbol,
    test_only_production_code,
    under_exposed_access_from_tests,
    under_exposed_access_outside,
    unused_public_symbol,
)
from pypeeker.intents import Intent
from pypeeker.models import UNRESOLVED_PREFIX, Confidence, SymbolKind, Visibility


@dataclass(frozen=True)
class Finding:
    """One reported row: what fired, where, in what words, on what evidence — and
    the repair, if any, that its rule's mutation decided the row earns.

    The first five fields are the ones the differential oracle compares. The
    sixth, ``remedy``, is what the phase-3 docstring promised would "arrive in
    phase 4 with the mutation terminals", and it is spelled exactly as the
    frozen engine spells it on :class:`pypeeker.check.models.Violation`::

        remedy: Intent | None = field(default=None, compare=False, repr=False)

    ``compare=False`` is the load-bearing half. A finding is an *observation*,
    and two findings that say the same thing about the same row must compare
    equal whether or not one of them happens to be repairable; the read half's
    whole-object equality assertions and the oracle's five-field payload both
    depend on that, and the frozen ``Violation`` made the same call for the same
    reason. So the remedy rides along without joining the identity — which is
    also why the phase-3d ledger entry's "add a terminal, not a field" is
    honoured rather than contradicted: the terminal
    (:data:`pypeeker.dsl.terminals.REWRITE_STAR_IMPORT` and its two siblings) is
    what *decides* the repair, and this field is only where the decision is
    carried. :class:`Remediation` remains the shape a fix consumer iterates,
    because it is the pairing whose ``intent`` is non-optional.

    Deliberately still absent: a baseline key. Fork #6 keys the baseline on
    ``(rule_id, anchor_id)``, which is derivable from a finding's rule and its
    row's anchor rather than stored on it; the re-key lands at the flip.
    """

    rule: str
    path: str
    line: int
    message: str
    confidence: Confidence
    remedy: Intent | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        """The frozen ``Violation.__str__``, byte for byte.

        ``<path>:<line>: [<rule>] <message>``, with a ``[<tier>]`` marker
        appended only for a non-``DECLARED`` finding. Identical to
        ``check.models.Violation.__str__`` so the two engines' output lines are
        directly comparable — which is what lets the fix-level differential
        grade a repair's *violation* text alongside its fix id.
        """
        marker = (
            "" if self.confidence is Confidence.DECLARED
            else f" [{self.confidence.value}]"
        )
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}{marker}"


@dataclass(frozen=True)
class Remediation:
    """A finding, and the repair its rule's mutation decided that row earns.

    The same pairing :attr:`Finding.remedy` carries, with the optionality
    resolved: ``Remediation.intent`` is an :class:`~pypeeker.intents.Intent`,
    never ``None``. That is the whole of its job. A fix consumer iterating
    :meth:`DslRule.remediations` never writes ``if f.remedy is not None``, and
    never has to decide what a "repair" with no intent would mean; the type says
    it cannot happen. :meth:`DslRule.findings` answers "what fired" and
    :meth:`DslRule.remediations` answers "what can be repaired", so neither
    question has to be asked through the other's answer even though both are
    computed from one pass over the rows.

    A :class:`Remediation` exists **only** for a row that earned an intent. A
    row refused by the mutation's floor or one of its preconditions is simply
    absent, and the reason it was refused is available where reasons belong, on
    :class:`~pypeeker.dsl.MutationDecision` via
    :meth:`~pypeeker.dsl.Application.decisions`. That is not the silent-``[]``
    fork #12 forbids: ``findings`` is the non-empty answer standing next to it,
    so "nothing to repair" and "nothing fired" are never the same observation.
    """

    finding: Finding
    intent: Intent

    @property
    def fix_id(self) -> str:
        """The repair's derived id — fork #5's ``<rule>:<mutation>:<anchor>``.

        Read off the intent rather than stored: the id is computed in exactly
        one place, :meth:`pypeeker.dsl.Mutation.intent_id`, so a second copy
        here would be a second thing for the two to disagree about. "No
        override" is structural, not promised.
        """
        return self.intent.intent_id


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

    ``mutation`` is the repair this rule declares, or ``None`` when it declares
    none. One rule declares at most one, which is fork #2 restated at the rule
    layer: a rule free to name a second repair for one operation is how two
    implementations of that operation get written.
    """

    rule_id: str
    build: Callable[[Mapping[str, Any]], Selection | None]
    message: str
    mutation: Mutation | None = None

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
        return _render(self.rule_id, self.message, selection, corpus, self.mutation)

    def remediations(
        self, options: Mapping[str, Any], corpus: Corpus
    ) -> list[Remediation]:
        """Run this rule over ``corpus`` and collect the repairs its rows earn.

        The write half's counterpart to :meth:`findings`, and a separate call
        rather than a richer return type: a caller that only reports never pays
        for a mutation decision, and a caller that only fixes never renders a
        message it will not print. Running both over one
        :class:`~pypeeker.dsl.Corpus` is cheap — the corpus memoises its sweeps,
        so the second pass re-reads warm rows.

        Args:
            options: the rule's option table, exactly as :meth:`findings` takes
                it. A rule's repairs are a subset of its findings' rows, so the
                two must be asked the same question.
            corpus: the indexes to quantify over.

        Returns:
            One :class:`Remediation` per row that earned an intent, in the
            order the selection produced its rows — ``[]`` when the rule
            declares no mutation, when it is switched off by its options, or
            when every row was refused by the mutation's floor or its
            preconditions.

        Raises:
            UnknownFieldError: the rule's mutation reads a field the rule's own
                selection does not expose. An authoring error, refused rather
                than silently withholding every repair.
        """
        selection = self.build(options)
        if selection is None or self.mutation is None:
            return []
        return [
            Remediation(finding=finding, intent=finding.remedy)
            for finding in _render(
                self.rule_id, self.message, selection, corpus, self.mutation
            )
            if finding.remedy is not None
        ]


def _render(
    rule_id: str,
    message: str,
    selection: Selection,
    corpus: Corpus,
    mutation: Mutation | None,
) -> list[Finding]:
    """Run ``selection``, word each surviving row, and decide the repair it earns.

    Shared by :class:`DslRule` and :class:`MultiPartRule` because a finding is
    a finding however many parts a rule has: one constructor, one place where
    ``file_path``/``line`` are read off the row, one place the template is
    applied, one place a repair is derived. Two copies would be two places for
    the finding shape to drift.

    The finding and the repair a row earns are produced from the **same**
    :class:`Match`, in one pass, and travel together on the finding itself.
    Deriving them in two passes would let a selection whose rows depend on
    mutable state pair a repair with a row that no longer says what it said.
    :meth:`DslRule.findings` hands the list straight back; the fix path filters
    it down to the rows whose ``remedy`` survived.

    The mutation's field reads are validated against the selection **before any
    row is evaluated**, for the reason
    :func:`pypeeker.dsl.selection.require_mutation_fields` gives: a rule
    attaches its repair here rather than through
    :meth:`pypeeker.dsl.Selection.apply`, and without the check a params/field
    mismatch would silently withhold every repair instead of raising.

    ``rule_id`` doubles as the mutation's origin, so a rule's repair is
    identified by fork #5's derived ``<rule>:<mutation>:<anchor>`` — which
    reproduces all three frozen check-remedy ids character for character.
    """
    if mutation is not None:
        require_mutation_fields(mutation, selection)
    found: list[Finding] = []
    for match in selection.rows(corpus):
        found.append(
            Finding(
                rule=rule_id,
                path=match.fields["file_path"],
                line=match.fields["line"],
                message=message.format(**match.fields),
                confidence=match.confidence,
                remedy=(
                    None if mutation is None else mutation.decide(rule_id, match).intent
                ),
            )
        )
    return found


@dataclass(frozen=True)
class RulePart:
    """One quantification of a rule that has more than one: a selection and its wording.

    ``build`` may return ``None``, meaning **this part is switched off by
    configuration**. That is how ``import-boundaries`` expresses
    ``report-unused-allowances = false`` — the old rule simply does not run
    that pass — without inventing an always-false predicate, which would be a
    lie about what the rule looked at and would still cost a sweep.

    ``mutation`` is per part rather than per rule because a part is where a
    selection lives, and a mutation is validated against a selection's visible
    fields. Every part of a rule that declares a repair carries the **same**
    mutation value: ``star-imports`` gives all four of its message partitions
    ``REWRITE_STAR_IMPORT`` and ``docstring-drift`` gives both of its directions
    ``RENAME_DOCSTRING_PARAM``, letting the mutation's own floor and
    preconditions decide which rows earn it. Silencing a part by withholding the
    mutation instead would spell one operation as two half-declarations, which
    is precisely the shape fork #2 forbids.
    """

    build: Callable[[Mapping[str, Any]], Selection | None]
    message: str
    mutation: Mutation | None = None


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

    A second, weaker case is a rule whose frozen body writes **two literal
    message lines** over one row shape: ``naming-conventions`` appends
    ``" — suggested name: '...'"`` only when a different conforming name could
    be produced. That is not three quantifications, but it is not one template
    either, and the two honest spellings are a single template with an
    interpolated ``{suffix}`` — which moves half of one message into Python,
    where fork #9 says the derivation tree can no longer describe it — or two
    parts whose selections partition the rows on a **visible derived field**.
    The second keeps both frozen lines as literal templates and keeps the
    branch itself in the DSL, where ``--why`` reports which side of it a row
    fell on. The partition must be complementary, or a row is worded twice.

    This is deliberately **not** a general fan-out: each part is still one
    selection producing one finding per row, so every rule stays a ∀-query and
    nothing about *which* rows fire or *how* they are worded moves out of the
    DSL. The parts run and concatenate in written order; a part whose ``build``
    returns ``None`` contributes nothing. Concatenation means a multi-part
    rule's findings are **not** in the frozen engine's per-symbol order; the
    differential oracle compares findings as a multiset per rule, so order is
    not part of parity, and ``import-boundaries`` already relies on that.

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
        return self._render_parts(options, corpus)

    def remediations(
        self, options: Mapping[str, Any], corpus: Corpus
    ) -> list[Remediation]:
        """Every enabled part's repairs, concatenated in written order.

        Structurally identical to :meth:`DslRule.remediations`, and it has to
        be: every part of a multi-part rule that declares a repair declares the
        **same** mutation value — ``star-imports`` gives all four of its message
        partitions ``REWRITE_STAR_IMPORT`` — so which part a row fell into
        cannot change which repair it earns. Only the mutation's own floor and
        preconditions can, which is fork #2 holding across a fan-out.

        Args:
            options: the rule's option table, as :meth:`findings` takes it.
            corpus: the indexes to quantify over.

        Returns:
            One :class:`Remediation` per row that earned an intent, in part
            order and, within a part, in row order.
        """
        return [
            Remediation(finding=finding, intent=finding.remedy)
            for finding in self._render_parts(options, corpus)
            if finding.remedy is not None
        ]

    def _render_parts(
        self, options: Mapping[str, Any], corpus: Corpus
    ) -> list[Finding]:
        """Run every enabled part, concatenating each one's findings."""
        found: list[Finding] = []
        for part in self.parts:
            selection = part.build(options)
            if selection is None:
                continue
            found.extend(
                _render(self.rule_id, part.message, selection, corpus, part.mutation)
            )
        return found


PortedRule = DslRule | MultiPartRule
"""Either rule shape. :data:`RULES` holds both, and everything downstream duck-types.

``pypeeker.dsl.differential`` calls ``rule.findings(options, corpus)`` and
``pypeeker.dsl.differential_fix`` calls ``rule.remediations(options, corpus)``,
neither asking what it got — which is why adding a second shape needed no
change in the first, and why phase 4's write half needed none either.
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


def _naming_allow_clause(patterns: tuple[str, ...]) -> Expr:
    """The frozen ``_allowed``: per pattern, an fnmatch against name, id, or module.

    ``check.builtin.naming_conventions._allowed`` tests each pattern against
    the bare ``symbol_name``, then against the ``symbol_id``, then against
    ``module_of(symbol_id)``, and only then moves to the next pattern. The
    three clauses are interleaved per pattern here so the written order
    matches — fork #3 makes written order normative even where every clause is
    pure. ``row.id_module`` **is** ``module_of``; it is deliberately not
    ``row.module``, for the reason :func:`_matches_any` gives.

    With no patterns this is an empty disjunction, which is ``False`` — the
    frozen ``any(...)`` over an empty generator, and the reason the clause is
    applied unconditionally rather than behind an ``if allow:``. The frozen
    rule calls ``_allowed`` on every surviving symbol whether or not any
    pattern is configured, and a selection that skipped the stage would claim
    it never asked.
    """
    return any_of(
        *(
            clause
            for pattern in patterns
            for clause in (
                row.name.matches(pattern),
                row.symbol_id.matches(pattern),
                row.id_module.matches(pattern),
            )
        )
    )


def _naming_base(options: Mapping[str, Any]) -> Selection:
    """Symbols of the configured kinds whose name violates their kind's convention.

    The five clauses are the frozen rule's five ``continue`` statements in its
    written order — kind, dunder, underscore-only, the convention pattern, the
    ``allow`` list — followed by the two derived fields its message quotes.

    **The dunder clause is spelled ``not_(row.name.matches("__*__"))``, and
    that is equivalent to the frozen ``_is_dunder`` only because of the clause
    after it.** ``_is_dunder`` is ``startswith("__") and endswith("__") and
    len(name) > 4``; the glob drops the length test, so the two disagree on
    exactly one input, ``"____"`` — frozen says "not a dunder, keep going", the
    glob says "dunder, skip". They select the same rows anyway, because the
    very next clause requires ``name.lstrip("_")`` to be non-empty and
    ``"____".lstrip("_")`` is ``""``. Reordering these two clauses, or dropping
    the ``stripped`` guard, silently starts flagging ``def ____()``.
    ``tests/test_dsl_rules.py`` pins that input.

    ``kinds`` coming back empty is not an error and not an off-switch: the
    frozen rule still iterates every symbol and rejects each one, which is what
    an ``is_in`` over no values does. See
    :func:`pypeeker.dsl.sweeps._naming_kinds` for why an unparseable ``kinds``
    option produces that empty set rather than the default three.

    Options (``[tool.pypeeker.naming-conventions]``):
        ``kinds``       — symbol kinds to check (default function/method/class).
        ``conventions`` — per-kind regex overriding that kind's default pattern.
        ``allow``       — fnmatch patterns never flagged.
    """
    kinds, conventions, allow = naming_params(options)

    @opaque("naming-strip-underscores", reads=("name",))
    def _stripped(record: Any) -> str:
        return record.name.lstrip("_")

    @opaque("naming-conforms", reads=("kind", "stripped"))
    def _conforms(record: Any) -> bool:
        return conforms(conventions, record.kind, record.stripped)

    @opaque("naming-convention-label", reads=("kind",))
    def _label(record: Any) -> str:
        return convention_label(conventions, record.kind)

    @opaque("naming-suggested-name", reads=("kind", "name", "stripped"))
    def _suggestion(record: Any) -> str:
        return suggested_name(conventions, record.kind, record.name, record.stripped)

    return (
        symbols()
        .where(row.kind.is_in(*kinds))
        .where(not_(row.name.matches("__*__")))
        .with_field("stripped", _stripped)
        .where(row.stripped.is_true())
        .where(not_(_conforms))
        .where(not_(_naming_allow_clause(allow)))
        .with_field("convention_label", _label)
        .with_field("suggested_name", _suggestion)
    )


def _naming_with_suggestion(options: Mapping[str, Any]) -> Selection:
    """Violations for which a different conforming name could be produced."""
    return _naming_base(options).where(row.suggested_name.is_true())


def _naming_without_suggestion(options: Mapping[str, Any]) -> Selection:
    """Violations the converter cannot improve on: the message carries no suggestion.

    The complement of :func:`_naming_with_suggestion` over the same base, so
    every violating row is worded exactly once. ``suggested_name`` is ``""``
    when the converter returned nothing or returned the name it was given —
    the frozen ``if suggestion and suggestion != stripped`` — which is why one
    field can carry both halves of that test.
    """
    return _naming_base(options).where(not_(row.suggested_name.is_true()))


def _unused_imports(options: Mapping[str, Any]) -> Selection:
    """Import bindings with no reference to them in their own file.

    Takes no options; the frozen rule reads none either. Every one of its
    ``continue`` statements is a clause here, in its written order, over the
    row source :func:`pypeeker.dsl.sweeps.unused_import_rows` produces — see
    there for why this is a row source and not a fact keyed on ``symbol_id``.

    The first two clauses are the frozen rule's **file-level early returns**
    (``__init__.py`` barrels re-export by design; a file binding ``__all__``
    re-exports by string, which reference analysis cannot see). They are
    clauses rather than absent rows so the exclusions stay visible: a barrel's
    imports are rows this rule looked at and rejected, not rows that never
    existed.

    The third — the frozen ``if symbol.kind is not SymbolKind.IMPORT`` — is the
    one rejection that is *not* a clause, because it decides what a row **is**
    rather than what the rule makes of it. The row source is over import
    bindings; a non-import symbol is not a row of it, the way a non-function
    is not a row of the cycle universe.

    The rest, in order: star imports bind no name (``star-imports`` owns them);
    a dynamically recovered import binds no name either and has no statement a
    fix could remove; an underscore-prefixed binding is a deliberate
    "imported for re-export / side effects" signal; a dotted ``import a.b.c``
    binds a namespace whose uses do not bind back to it; ``__future__``
    imports act by existing; and a name used only inside a quoted annotation is
    invisible to reference analysis rather than unused.

    The frozen rule's whole-file ``HEURISTIC`` downgrade — every finding in a
    file that references ``getattr``/``globals``/``vars``/``locals``, because
    ``globals()["os"]`` can consume an import invisibly — is **not** a clause
    here. It is the row's intrinsic ``evidence``, set by the sweep and carried
    through the meet, the same way a dynamically recovered import row carries
    ``HEURISTIC`` for ``import-boundaries``. Spelling it as a tenth clause
    would mean a :class:`~pypeeker.dsl.Weaken` node, and that node is
    inventoried: :data:`pypeeker.dsl.DYNAMIC_ACCESS_WEAKENED_RULES` names the
    five visibility rules the frozen engine downgrades through one shared
    helper, and this rule computes the same tier by its own route rather than
    belonging to that family.
    """
    del options
    return Selection(unused_import_rows()).where(
        all_of(
            not_(row.in_barrel.is_true()),
            not_(row.has_all.is_true()),
            not_(row.is_star.is_true()),
            not_(row.dynamic.is_true()),
            not_(row.name.startswith("_")),
            not_(row.dotted.is_true()),
            not_(row.future.is_true()),
            not_(row.forward_ref.is_true()),
            row.used.eq(False),
        )
    )


def _drift_ghosts(options: Mapping[str, Any]) -> Selection:
    """Documented parameters the signature does not have.

    Two clauses over :func:`pypeeker.dsl.sweeps.drift_rows`, in the frozen
    rule's order. ``allow`` comes first because the frozen rule tests it before
    it parses anything; it is applied unconditionally because the frozen rule
    calls ``_matches_any`` on every symbol whether or not a pattern is
    configured, and an empty disjunction is ``False``. The frozen helper tests
    the ``symbol_id`` and its module path, which is exactly this module's
    :func:`_matches_any`.

    ``row.drift.eq("ghost")`` is the partition, not a rejection: the row source
    carries both drift directions so one memoized pass serves both parts, and
    the two directions have two literal message lines rather than one template
    with a branch in it.

    Everything else the frozen rule tests — the symbol's kind, an empty
    docstring, an unrecognized params section — decides what a *row is* rather
    than what the rule makes of it, so it lives in the row source. A function
    with no drift is not a row of "drifted parameters" the way a non-function
    is not a row of the cycle universe.

    Options (``[tool.pypeeker.docstring-drift]``):
        ``style``            — force one parser instead of autodetecting;
                               normalized by
                               :func:`pypeeker.dsl.sweeps.docstring_params`.
        ``allow``            — fnmatch patterns over the function's symbol id
                               or its module path; matches are never flagged.
        ``require-complete`` — read by :func:`_drift_missing`, not here.
    """
    return Selection(drift_rows(docstring_params(options))).where(
        all_of(
            not_(_matches_any(as_str_list(options.get("allow")))),
            row.drift.eq("ghost"),
        )
    )


def _drift_missing(options: Mapping[str, Any]) -> Selection | None:
    """Signature parameters an existing params section does not document.

    Returns ``None`` — the part is switched off — unless ``require-complete``
    is set. That is the frozen rule's ``if require_complete:`` guard around its
    second loop, and it is an off-switch rather than a clause for the reason
    :class:`RulePart` gives: the frozen rule does not run that pass at all, so
    an always-false predicate would claim it looked at every undocumented
    parameter and forgave each one.

    Demanding a params section where none exists is ``require-docstrings``'
    turf, and the row source already honors that: a function whose docstring
    has no recognized section produces no rows in either direction.
    """
    if not options.get("require-complete"):
        return None
    return Selection(drift_rows(docstring_params(options))).where(
        all_of(
            not_(_matches_any(as_str_list(options.get("allow")))),
            row.drift.eq("missing"),
        )
    )


def _unused_return_value(options: Mapping[str, Any]) -> Selection:
    """Functions promising a value whose every resolved call site discards it.

    Four clauses over :func:`pypeeker.dsl.sweeps.unused_return_rows`, in the
    frozen rule's written order: ``allow``, then the value-escape skip, then
    "zero calls is dead-code territory, not ours", then the verdict itself.

    Everything above them in the frozen loop — kind, a missing or non-declared
    return annotation, ``-> None`` in any of its three spellings, a dunder
    name — decides what a *row is* rather than what the rule makes of it, and
    one of those tests is not expressible on a symbols row at all: the
    annotation's ``confidence`` is not a published field. Both reasons put the
    candidate test in the row source; see :func:`pypeeker.dsl.sweeps.unused_return_rows`.

    ``row.any_used.eq(False)`` rather than ``not_(row.any_used.is_true())``:
    the field is a real boolean the sweep computed, so the comparison says
    what the frozen ``if any(...)`` says. The two agree here — the field is
    never ``UNMATCHED`` — and the positive form reads as the verdict it is.

    Options (``[tool.pypeeker.unused-return-value]``):
        ``allow`` — fnmatch patterns over the function's symbol id or its
                    module path; matching functions are never flagged.
    """
    return Selection(unused_return_rows()).where(
        all_of(
            not_(_matches_any(as_str_list(options.get("allow")))),
            not_(row.escapes.is_true()),
            row.has_calls.is_true(),
            row.any_used.eq(False),
        )
    )


def _star_imports_unindexed(options: Mapping[str, Any]) -> Selection:
    """Star imports whose target module is not in the corpus.

    The first of four complementary partitions over
    :func:`pypeeker.dsl.sweeps.star_import_rows`. The frozen rule's
    ``if star.imported_from not in modules`` branch: it reports before it
    computes any used names, and its finding is ``HEURISTIC`` regardless of how
    many stars the file has — the row source carries that tier, so this
    selection only has to say *which* rows are in the branch.

    ``not_(row.indexed.is_true())`` rather than ``row.indexed.eq(False)`` for
    symmetry with its three siblings, which all lead with the positive form.
    """
    del options
    return Selection(star_import_rows()).where(not_(row.indexed.is_true()))


def _star_imports_zero(options: Mapping[str, Any]) -> Selection:
    """Indexed star imports supplying no name the file actually uses."""
    del options
    return Selection(star_import_rows()).where(
        all_of(row.indexed.is_true(), row.name_count.eq(0))
    )


def _star_imports_one(options: Mapping[str, Any]) -> Selection:
    """Indexed star imports supplying exactly one used name — the singular wording."""
    del options
    return Selection(star_import_rows()).where(
        all_of(row.indexed.is_true(), row.name_count.eq(1))
    )


def _star_imports_many(options: Mapping[str, Any]) -> Selection:
    """Indexed star imports supplying two or more used names — the plural wording.

    ``not_(row.name_count.is_in(0, 1))`` rather than a ``> 1`` comparison: the
    grammar has no ordering operator, deliberately, and the complement of the
    two sibling partitions is what makes the four exhaustive anyway. A count is
    never negative, so the two spellings select the same rows.
    """
    del options
    return Selection(star_import_rows()).where(
        all_of(row.indexed.is_true(), not_(row.name_count.is_in(0, 1)))
    )


def _barrel_only(options: Mapping[str, Any]) -> Selection:
    """Cross-package deep imports of a name the target package's barrel re-exports.

    Eight clauses over :func:`pypeeker.dsl.sweeps.barrel_rows`, in the frozen
    rule's written order: the ``__init__.py`` exemption, a bare ``import a.b.c``
    binding no name through a barrel, a dynamically recovered import, an
    ``imported_from`` with no module part, an import that is not cross-package
    (external, root-level, or a sibling of the importer), one that already
    imports at the barrel level rather than below it, a target package with no
    curated ``__all__`` barrel, and finally a barrel that does not re-export
    *this* name.

    The first is the interesting one. A package ``__init__`` deep-importing its
    own submodules is how barrels are built, and the frozen rule skips such a
    file before it looks at a single symbol — but that is an *exemption of the
    rule*, not a question of jurisdiction, so it is a clause here and a barrel's
    imports are rows this rule looked at and forgave. The two file-level skips
    that genuinely decide jurisdiction (no MODULE symbol; outside the root)
    produce no rows at all, in the sweep.

    ``resolve_definition`` deciding ``re_exported`` is what makes the rule
    chain-aware: a name laundered through an intermediate barrel resolves to
    the same canonical definition as the re-export, so the violation cannot be
    hidden behind one.

    Options (``[tool.pypeeker.barrel-only]``):
        ``root`` — project root package (dotted prefix). When omitted each file
                   falls back to its own top-level segment, mirroring
                   ``import-boundaries``.
    """
    return Selection(barrel_rows(barrel_params(options))).where(
        all_of(
            not_(row.in_barrel.is_true()),
            row.imported_from.is_true(),
            not_(row.dynamic.is_true()),
            row.target_module.is_true(),
            row.cross_package.is_true(),
            row.deeper_than_barrel.is_true(),
            row.curated.is_true(),
            row.re_exported.is_true(),
        )
    )


RULES: Mapping[str, PortedRule] = MappingProxyType({
    # The safety question is asked by the selection (TUPLE_CANDIDATE), not by
    # the mutation, so TUPLIFY carries no preconditions — the frozen rule
    # likewise attaches its remedy to every finding it reaches.
    "prefer-tuple": DslRule(
        rule_id="prefer-tuple",
        build=_prefer_tuple,
        message="list '{name}' is never mutated — consider a tuple",
        mutation=TUPLIFY,
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
    # The repair is unconditional in the frozen rule — every finding carries a
    # RemoveImportIntent — so REMOVE_IMPORT has no preconditions, and the only
    # thing that ever withheld it is its DECLARED floor (the dynamic-access
    # downgrade the row source carries).
    "unused-imports": DslRule(
        rule_id="unused-imports",
        build=_unused_imports,
        message="import '{name}' is unused in this module",
        mutation=REMOVE_IMPORT,
    ),
    # Two parts, one row shape: the frozen rule writes two literal message
    # lines and picks between them on whether a different conforming name
    # exists. The selections partition on `suggested_name`, which is a visible
    # derived field, so no row is worded twice and the branch stays in the DSL.
    # Both templates are copied character-for-character out of the frozen rule
    # — the separator before "suggested name" is an em dash (U+2014).
    "naming-conventions": MultiPartRule(
        rule_id="naming-conventions",
        parts=(
            RulePart(
                build=_naming_with_suggestion,
                message=(
                    "{kind.value} '{symbol_id}' does not match the "
                    "{convention_label} naming convention"
                    " — suggested name: '{suggested_name}'"
                ),
            ),
            RulePart(
                build=_naming_without_suggestion,
                message=(
                    "{kind.value} '{symbol_id}' does not match the "
                    "{convention_label} naming convention"
                ),
            ),
        ),
    ),
    # Two parts over ONE row source, whose rows are drifted parameters rather
    # than functions — see pypeeker.dsl.sweeps.drift_rows. That is where the
    # one-symbol-many-findings shape is expressed; both parts here are ordinary
    # one-finding-per-row selections. The second is switched off entirely
    # unless `require-complete` is set, because the frozen rule does not run
    # that pass.
    #
    # Both parts carry RENAME_DOCSTRING_PARAM, the rule's one repair. The
    # frozen rule attaches it only to ghost findings, and only when the drift
    # is the unambiguous one-for-one rename; those are the mutation's own
    # preconditions, so the missing part declares the same repair and is
    # silenced by the guard rather than by a missing declaration.
    "docstring-drift": MultiPartRule(
        rule_id="docstring-drift",
        parts=(
            RulePart(
                build=_drift_ghosts,
                message=(
                    "docstring of {kind.value} '{name}' documents parameter "
                    "'{param}' which does not exist"
                ),
                mutation=RENAME_DOCSTRING_PARAM,
            ),
            RulePart(
                build=_drift_missing,
                message=(
                    "docstring of {kind.value} '{name}' does not document "
                    "parameter '{param}'"
                ),
                mutation=RENAME_DOCSTRING_PARAM,
            ),
        ),
    ),
    # ── the visibility / reference-counting family (phase 3b) ──────────────
    # Expressions in pypeeker.dsl.visibility; messages verbatim from the
    # frozen rules except test-only-production-code's, which drops a computed
    # reference count and carries a ledger divergence for the wording.
    # DELETE_SYMBOL's own `public-api` precondition is the frozen
    # `visibility is not PUBLIC` guard: dead private code is deletable, dead
    # public API is a contract. Reachable only under `also-private`, which no
    # differential corpus sets, so the repair is ungraded by the oracle and
    # pinned by unit tests instead.
    "unused-public-symbol": DslRule(
        rule_id="unused-public-symbol",
        build=unused_public_symbol,
        message="{visibility.value} {kind.value} '{symbol_id}' has no references in the project",
        mutation=DELETE_SYMBOL,
    ),
    "over-exposed-module-symbol": DslRule(
        rule_id="over-exposed-module-symbol",
        build=over_exposed_module_symbol,
        message="public '{symbol_id}' is only used within its module — make it _{name}",
    ),
    "over-exposed-export": DslRule(
        rule_id="over-exposed-export",
        build=over_exposed_export,
        message=(
            "package '{module}' exports '{name}' but no outside consumer uses it "
            "— drop the re-export"
        ),
    ),
    "born-private": DslRule(
        rule_id="born-private",
        build=born_private,
        message=(
            "newly public '{name}' is only used within its module — make it _{name} "
            "or record it (`check --update-baseline`)"
        ),
    ),
    "test-only-production-code": DslRule(
        rule_id="test-only-production-code",
        build=test_only_production_code,
        message="'{symbol_id}' is referenced only from tests",
    ),
    # ── the cross-file residue (phase 3d) ──────────────────────────────────
    # Four parts, one row shape. The frozen _message writes three literal lines
    # and computes `plural = "s" if len(names) != 1 else ""`; a template
    # carrying a `{plural}` field would move half a message into Python, where
    # fork #9 says the derivation tree can no longer describe it. So the
    # partition is on two visible fields — `indexed` and `name_count` — and all
    # four lines stay literal. It is complementary by construction: not
    # indexed / indexed with 0 / indexed with 1 / indexed with neither. The
    # separator after the module name is an em dash (U+2014) in all four.
    #
    # All four parts carry REWRITE_STAR_IMPORT, the rule's one repair. The
    # frozen guard is `names and file_confidence is DECLARED`, and neither half
    # of it is a partition clause: `file_confidence` is the row's own intrinsic
    # evidence, so the mutation's DECLARED floor rejects the multi-star and
    # unindexed rows, and `names` is the mutation's two preconditions. No part
    # needs to know which partition it is in.
    "star-imports": MultiPartRule(
        rule_id="star-imports",
        parts=(
            RulePart(
                build=_star_imports_unindexed,
                mutation=REWRITE_STAR_IMPORT,
                message=(
                    "star import from '{imported_from}' — target module is not "
                    "indexed; used names unknown"
                ),
            ),
            RulePart(
                build=_star_imports_zero,
                mutation=REWRITE_STAR_IMPORT,
                message=(
                    "star import from '{imported_from}' — 0 names actually used; "
                    "consider deleting the import"
                ),
            ),
            RulePart(
                build=_star_imports_one,
                mutation=REWRITE_STAR_IMPORT,
                message=(
                    "star import from '{imported_from}' — 1 name actually used: "
                    "{used_names}"
                ),
            ),
            RulePart(
                build=_star_imports_many,
                mutation=REWRITE_STAR_IMPORT,
                message=(
                    "star import from '{imported_from}' — {name_count} names "
                    "actually used: {used_names}"
                ),
            ),
        ),
    ),
    # `{imported_name}` is the last dotted segment of the import's
    # `imported_from`, NOT the binding's local name: the frozen message quotes
    # what the barrel exports, so `from a.b import C as D` is worded
    # "import 'C' via ...". See pypeeker.dsl.sweeps._BarrelRow.
    "barrel-only": DslRule(
        rule_id="barrel-only",
        build=_barrel_only,
        message=(
            "import '{imported_name}' via the '{barrel_module}' barrel, not its "
            "internal module '{target_module}'"
        ),
    ),
    # Two parts, one row shape, over the references universe. The frozen
    # under-exposed-access writes two literal message lines and picks between
    # them on whether the *referencing* file is test code; the selections
    # partition on that same test-glob clause and its negation, so the two are
    # complementary by construction and no reference is worded twice. Both
    # templates are copied character-for-character from
    # check/builtin/visibility.py, including `{origin}` being the referencing
    # module (`row.module`) and `{target_module}` the defining one.
    "under-exposed-access": MultiPartRule(
        rule_id="under-exposed-access",
        parts=(
            RulePart(
                build=under_exposed_access_from_tests,
                message="{target_visibility.value} '{target_name}' accessed from tests ('{module}')",
            ),
            RulePart(
                build=under_exposed_access_outside,
                message=(
                    "{target_visibility.value} '{target_name}' accessed from '{module}' "
                    "outside its defining module '{target_module}'"
                ),
            ),
        ),
    ),
    # The count and the call-site summary are row fields, not an aggregate the
    # template cannot see — the row source already holds the site list. See
    # pypeeker.dsl.sweeps.unused_return_rows for why that is consistent with
    # the ledger dropping test-only-production-code's count.
    "unused-return-value": DslRule(
        rule_id="unused-return-value",
        build=_unused_return_value,
        message=(
            "{kind.value} '{symbol_id}' declares return type '{annotation}' but all "
            "{call_count} call site(s) discard the result ({call_sites})"
        ),
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
    # ── the mutation pair (phase 3e) ───────────────────────────────────────
    # Expressions in pypeeker.dsl.mutation; both rules quantify over mutation
    # SITES, which are references, so the frozen "for every function, for every
    # reference in its subtree" loop becomes the row field
    # `enclosing_function_id`.
    #
    # Three parts, one per arm of the frozen `_mutation_detail`, in its written
    # order. They are disjoint by construction — CALL+attribute, WRITE+
    # attribute, WRITE+non-attribute — so no row is worded twice. Every
    # template quotes `{enclosing_function_kind.value}`, which is the frozen
    # f-string's `symbol.kind.value`: this rule says "method" for a method.
    "no-argument-mutation": MultiPartRule(
        rule_id="no-argument-mutation",
        parts=(
            RulePart(
                build=argument_mutator_call,
                message=(
                    "{enclosing_function_kind.value} '{enclosing_function_id}': "
                    "parameter '{receiver_root_name}' mutated via {via}()"
                ),
            ),
            RulePart(
                build=argument_attribute_write,
                message=(
                    "{enclosing_function_kind.value} '{enclosing_function_id}': "
                    "parameter '{receiver_root_name}' mutated via attribute write "
                    "'.{attribute}'"
                ),
            ),
            RulePart(
                build=argument_subscript_write,
                message=(
                    "{enclosing_function_kind.value} '{enclosing_function_id}': "
                    "parameter '{binding_name}' mutated via subscript write"
                ),
            ),
        ),
    ),
    # Four parts, one per frozen shape: 1a outer-scope write, 2 mutator call on
    # a module-level container, 3 attribute write on an imported module, and 1b
    # the `global x; x = 1` rebind — which is not a reference at all and comes
    # from its own row source. Disjoint by construction, three of them on the
    # kind/attribute partition and the fourth on the row shape.
    #
    # Every template says the literal word `function`, even where the enclosing
    # symbol is a METHOD: that is the frozen wording (`f"function '{func_id}'"`
    # in all four shapes), and it is deliberately NOT the argument rule's
    # `{enclosing_function_kind.value}`.
    "no-hidden-global-mutation": MultiPartRule(
        rule_id="no-hidden-global-mutation",
        parts=(
            RulePart(
                build=global_outer_scope_write,
                message=(
                    "function '{enclosing_function_id}' writes module-level "
                    "variable '{written_variable}'"
                ),
            ),
            RulePart(
                build=global_rebind,
                message=(
                    "function '{enclosing_function_id}' rebinds module-level "
                    "variable '{rebound_variable}'"
                ),
            ),
            RulePart(
                build=global_mutator_call,
                message=(
                    "function '{enclosing_function_id}' calls '.{method}()' on "
                    "module-level variable '{receiver_root_symbol_id}'"
                ),
            ),
            RulePart(
                build=global_import_attribute_write,
                message=(
                    "function '{enclosing_function_id}' writes attribute "
                    "'{attribute}' on imported module '{imported_module}'"
                ),
            ),
        ),
    ),
    # ── the impurity pair (phase 3f) ───────────────────────────────────────
    # Expressions in pypeeker.dsl.impurity. Both rules drive
    # `analysis.impurities` through the same lazy IMPURITY fact
    # `no-impure-functions` uses, so the summary wording and the DECLARED /
    # HEURISTIC tier are shared rather than restated.
    "pure-decorator-contracts": DslRule(
        rule_id="pure-decorator-contracts",
        build=pure_decorator_contracts,
        message=(
            "{kind.value} '{symbol_id}' violates the {contract} purity "
            "contract: {impurities}"
        ),
    ),
    # Three parts, one per arm of the frozen `_describe_call`, in its written
    # `if`/`elif`/`else` order. That function is a FALL-THROUGH partition, so
    # the parts carry the exclusions that make them disjoint: shape 1 returns
    # unconditionally on a non-None bare name, so B and C exclude one; shape 2
    # fires only on a qualified name that is IN the module denylist, so C
    # excludes the membership rather than the name. No row is worded twice and
    # none is dropped. Part order is for readers only — the differential oracle
    # sorts findings before comparing.
    #
    # `{call_name}` is the frozen `name` each arm returned, and is deliberately
    # a different thing per part: a bare name, a dotted qualified name, and a
    # resolved project symbol id. It is also what `allow` patterns match.
    "import-time-side-effects": MultiPartRule(
        rule_id="import-time-side-effects",
        parts=(
            RulePart(
                build=import_time_builtin_call,
                message=(
                    "import-time call to '{call_name}' matches the "
                    "impure-builtin policy"
                ),
            ),
            RulePart(
                build=import_time_module_call,
                message=(
                    "import-time call to '{call_name}' matches the "
                    "impure-call policy"
                ),
            ),
            RulePart(
                build=import_time_project_call,
                message=(
                    "import-time call to '{call_name}' resolves to an impure "
                    "project {definition_kind.value}"
                ),
            ),
        ),
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
