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
from pypeeker.dsl.expr import all_of, not_, row
from pypeeker.dsl.library import TUPLE_CANDIDATE
from pypeeker.dsl.selection import Selection, references, symbols
from pypeeker.dsl.visibility import (
    born_private,
    over_exposed_export,
    over_exposed_module_symbol,
    test_only_production_code,
    unused_public_symbol,
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
    """

    rule_id: str
    build: Callable[[Mapping[str, Any]], Selection]
    message: str

    def findings(self, options: Mapping[str, Any], corpus: Corpus) -> list[Finding]:
        """Run this rule over ``corpus``, one :class:`Finding` per surviving row.

        Args:
            options: the rule's option table, as the old engine's
                ``rule_options[rule_id]`` would have supplied it.
            corpus: the indexes to quantify over.

        Returns:
            The findings in the order the selection produced its rows, which is
            index order and therefore stable across runs.
        """
        return [
            Finding(
                rule=self.rule_id,
                path=match.fields["file_path"],
                line=match.fields["line"],
                message=self.message.format(**match.fields),
                confidence=match.confidence,
            )
            for match in self.build(options).rows(corpus)
        ]


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


RULES: Mapping[str, DslRule] = MappingProxyType({
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
    # ── the visibility / reference-counting family (phase 3b) ──────────────
    # Expressions in pypeeker.dsl.visibility; messages verbatim from the
    # frozen rules except test-only-production-code's, which drops a computed
    # reference count and carries a ledger divergence for the wording.
    "unused-public-symbol": DslRule(
        rule_id="unused-public-symbol",
        build=unused_public_symbol,
        message="{visibility.value} {kind.value} '{symbol_id}' has no references in the project",
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
})
"""Every rule the new engine implements, by its rule id.

A name appears here only once its expression has been written; it appears in
``scripts/parity-manifest.toml``'s ``claimed`` list only once the differential
oracle grades it at parity with the frozen old engine.
"""


def dsl_rule(name: str) -> DslRule:
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
