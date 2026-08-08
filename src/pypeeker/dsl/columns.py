"""Project columns: per-row facts that only the whole corpus can answer.

A ``where`` clause reads fields off the row it was handed, and every one of
those comes from a single :class:`~pypeeker.models.FileIndex`. Some questions
about a row are not like that: *which definition does this name actually bind
to, across imports and re-exports*, and *which modules use that definition*.
Both are pointwise — one answer per row — but neither can be computed without
the cross-module resolver and a pass over every index.

``follow("definition")`` already navigates the first of those, but a follow
step **replaces** the rows in flight. A rule that needs to compare a symbol
against its own definition cannot follow, because after the follow the original
row is gone. A column is the pointwise form of the same navigation: it stays on
the row and produces a value.

Columns are a **constant table**, not a registry. There is no
``register_column``: a module-level table written from a function is new
mutable global surface, and unlike
:func:`pypeeker.analysis.register_trait` — which exists because a consumer
project genuinely may want to replace a builtin trait provider — nothing wants
to replace "what does this id resolve to". The table is closed the same way
:data:`pypeeker.dsl.EXPRESSIONS` is closed, and an unknown name is a loud
:class:`~pypeeker.dsl.UnknownExpressionError` naming the ones that exist.

The evidence law, and why it is this one
----------------------------------------

**A project column reports ``DECLARED``.**

The argument is the one ``follow("definition")`` already makes and this module
must not contradict: the rows that step produces carry
:attr:`~pypeeker.models.Confidence.DECLARED` intrinsic evidence, because a
resolved definition is a fact the model records rather than a guess about it.
A column is that same navigation performed pointwise, so reporting anything
weaker would mean the same question answered two ways gave two confidences —
and the weaker one would be produced by the *more* precise spelling, which is
backwards.

Where genuine uncertainty enters, it enters through the row, not through the
column: the ``imports`` universe carries ``import_confidence``, so a row
reached through a dynamically-recovered import is already ``HEURISTIC`` before
any column runs, and the meet carries that into every answer about it.

Why there is no ``definition-row`` column
-----------------------------------------

The obvious design — one column yielding the whole definition row, projected
with ``.attr("kind")`` — was rejected. :class:`~pypeeker.dsl.Attr` documents
that a missing attribute yields ``None`` at ``UNKNOWN``, and it detects
"missing" with ``hasattr``; a row view answers every attribute, so
``.attr("kidn")`` would report a *present* attribute at ``DECLARED`` with the
value ``None``, and ``not_(....eq(SymbolKind.IMPORT))`` over that typo would
quietly pass for every row. Narrow columns cost the same number of clauses and
cannot lie: a name nobody declares is refused at authoring time.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import UnknownExpressionError
from pypeeker.dsl.evidence import Derivation
from pypeeker.dsl.expr import PROJECT_READ_PREFIX, UNMATCHED, EvalContext, Expr
from pypeeker.dsl.reach import Reach
from pypeeker.models import Confidence, SymbolKind

DEFINITION_ID = "definition-id"
"""The canonical definition this row binds to, following imports across files."""

DEFINITION_KIND = "definition-kind"
"""The :class:`~pypeeker.models.SymbolKind` of that definition, when in the corpus."""

DEFINITION_MODULE = "definition-module"
"""The dotted module path declaring that definition, when in the corpus."""

USAGE_ORIGINS = "usage-origins"
"""Modules that reference this row's definition, as a frozen set of module ids."""

_SYMBOL_LIKE = frozenset({"symbols", "imports"})
"""Universes whose row subject is a :class:`~pypeeker.models.Symbol`."""


def _definition_id(corpus: Corpus, universe: str | None, subject: Any) -> Any:
    """Resolve this row's subject to a canonical definition id.

    References resolve through ``resolve_reference`` (which understands the use
    site: attribute access, receiver roots), symbols and imports through
    ``resolve_definition``. Any other universe has no definition to speak of
    and yields :data:`~pypeeker.dsl.UNMATCHED`.
    """
    if universe == "references":
        return corpus.resolver.resolve_reference(subject)
    if universe in _SYMBOL_LIKE and subject is not None:
        return corpus.resolver.resolve_definition(subject.symbol_id)
    return UNMATCHED


def _modules_by_file(corpus: Corpus) -> Mapping[str, str]:
    """Indexed file path -> the id of that file's ``MODULE`` symbol, once per run.

    A file with no ``MODULE`` symbol is absent from the map rather than falling
    back to its path. That is deliberate and matches the frozen engine's
    ``_module_by_file``: a path is not a module id, and a rule comparing module
    ids would treat one as a module nobody can name.
    """

    def _build() -> Mapping[str, str]:
        table: dict[str, str] = {}
        for index in corpus.indexes:
            for symbol in index.symbols:
                if symbol.kind is SymbolKind.MODULE:
                    table[index.file_path] = symbol.symbol_id
                    break
        return MappingProxyType(table)

    return corpus.memo("dsl.columns:modules-by-file", _build)


def _usage_origins(corpus: Corpus) -> Mapping[str, frozenset[str]]:
    """Canonical definition id -> the modules referencing it, once per run.

    One pass over every reference in the corpus, each resolved through the
    cross-module resolver so import aliases and barrel re-exports land on the
    definition rather than on the alias. This is the single "where is this
    actually used from" computation the visibility family quantifies over, and
    materializing it is what keeps five rules asking the question once.
    """

    def _build() -> Mapping[str, frozenset[str]]:
        modules = _modules_by_file(corpus)
        resolver = corpus.resolver
        origins: dict[str, set[str]] = {}
        for index in corpus.indexes:
            origin = modules.get(index.file_path)
            if origin is None:
                continue
            for ref in index.references:
                origins.setdefault(resolver.resolve_reference(ref), set()).add(origin)
        return MappingProxyType({key: frozenset(value) for key, value in origins.items()})

    return corpus.memo("dsl.columns:usage-origins", _build)


def _definition_symbol(corpus: Corpus, universe: str | None, subject: Any) -> Any:
    """The located ``(Symbol, FileIndex)`` for this row's definition, or ``None``."""
    definition = _definition_id(corpus, universe, subject)
    if definition is UNMATCHED or definition is None:
        return None
    return corpus.locate(definition)


def _definition_kind_provider(corpus: Corpus, universe: str | None, subject: Any) -> Any:
    """The definition's symbol kind, or ``UNMATCHED`` when it is outside the corpus."""
    found = _definition_symbol(corpus, universe, subject)
    return UNMATCHED if found is None else found[0].kind


def _definition_module_provider(corpus: Corpus, universe: str | None, subject: Any) -> Any:
    """The module declaring the definition, or ``UNMATCHED`` when it is not locatable."""
    found = _definition_symbol(corpus, universe, subject)
    if found is None:
        return UNMATCHED
    module = _modules_by_file(corpus).get(found[1].file_path)
    return UNMATCHED if module is None else module


def _usage_origins_provider(corpus: Corpus, universe: str | None, subject: Any) -> Any:
    """The modules referencing this row's definition; an empty set when none do."""
    definition = _definition_id(corpus, universe, subject)
    if definition is UNMATCHED or definition is None:
        return UNMATCHED
    return _usage_origins(corpus).get(definition, frozenset())


COLUMNS: Mapping[str, Callable[[Corpus, str | None, Any], Any]] = MappingProxyType({
    DEFINITION_ID: _definition_id,
    DEFINITION_KIND: _definition_kind_provider,
    DEFINITION_MODULE: _definition_module_provider,
    USAGE_ORIGINS: _usage_origins_provider,
})
"""The closed table of project columns, by name. See the module docstring."""


@dataclass(frozen=True)
class ProjectColumn(Expr):
    """A pointwise fact about a row that the whole corpus has to answer.

    Reach is ``PROJECT`` unconditionally — every provider in :data:`COLUMNS`
    consults :attr:`pypeeker.dsl.Corpus.resolver` — and evidence is
    ``DECLARED``, for the reason argued in the module docstring. A row whose
    subject has no answer (a definition outside the corpus, a universe with no
    definition to resolve) yields :data:`~pypeeker.dsl.UNMATCHED`, which
    compares false against everything.
    """

    name: str

    @property
    def reach(self) -> Reach:
        """``PROJECT``: every column consults the cross-module resolver."""
        return Reach.PROJECT

    def evaluate(self, ctx: EvalContext) -> Derivation:
        """Run this column's provider against the row's subject."""
        corpus = ctx.require_corpus(f"column_of({self.name!r})")
        value = COLUMNS[self.name](corpus, ctx.universe, ctx.subject)
        return Derivation(
            op="column",
            value=value,
            confidence=Confidence.DECLARED,
            reads=frozenset({PROJECT_READ_PREFIX + self.name}),
            detail=MappingProxyType({"column": self.name, "matched": value is not UNMATCHED}),
        )


def column_of(name: str) -> ProjectColumn:
    """Open the project column registered under ``name``.

    Args:
        name: one of :data:`COLUMNS`' keys — :data:`DEFINITION_ID`,
            :data:`DEFINITION_KIND`, :data:`DEFINITION_MODULE`,
            :data:`USAGE_ORIGINS`.

    Returns:
        A :class:`ProjectColumn` node.

    Raises:
        UnknownExpressionError: no column is registered under ``name``; the
            message names the ones that are.
    """
    if name not in COLUMNS:
        raise UnknownExpressionError(name, COLUMNS)
    return ProjectColumn(name)
