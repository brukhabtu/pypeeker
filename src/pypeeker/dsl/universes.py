"""The five universes a selection can quantify over, and how to walk between them.

Fork #8 of ``dsl-rewrite.md`` fixes the universes at five — **symbols,
references, imports, modules, scopes**. This module is the whole of that
resolution: each universe declares the fields a row exposes, the evidence a row
carries intrinsically, and the ``follow`` steps that lead out of it.

Three properties worth stating outright, because each is load-bearing
elsewhere:

**Fields are declared, not discovered.** A universe publishes a fixed tuple of
field names. ``where(row.kindd.eq(...))`` is refused at authoring time with the
valid names listed, rather than quietly reading ``None`` forever. Line numbers
are exposed **1-based**, matching every line number pypeeker prints, even though
the model stores them 0-based — the DSL is an authoring and output surface, not
a second copy of the model.

**Row evidence is intrinsic.** Model fields report ``DECLARED`` because the
model is the record of what the binder actually saw; the sub-``DECLARED``
evidence in a typical finding therefore has to enter from somewhere. One of its
doors is here: every row carries a level that joins the meet without any
expression asking for it. Four universes carry ``DECLARED``. The ``imports``
universe carries :attr:`~pypeeker.models.Symbol.import_confidence` — ``None``
for a static ``import`` statement, which reads as ``DECLARED``, and
``HEURISTIC`` for one recovered from ``importlib.import_module("pkg.mod")``.
So a rule written over imports reports ``HEURISTIC`` on a dynamically-recovered
binding without its author writing a line about confidence.

**Reach is a property of the step, not of the caller.** Each follow step
declares whether reaching its target needs the cross-module resolver. That
declaration is the *only* input to
:attr:`pypeeker.dsl.Selection.reach`, which is how "derived from the
expression, never declared" is enforced rather than merely intended: a step
that consults :attr:`~pypeeker.dsl.Corpus.resolver` is marked ``PROJECT`` here,
next to the code that does the consulting.

A follow step yields **distinct** targets in first-reached order — ten symbols
in one file followed to ``module`` produce one module row, not ten. Where two
paths reach the same target on different evidence, the first-reached path's
evidence stands: written order is normative in this DSL (fork #3), so
"first-reached wins" is the answer consistent with the rest of it, and needs no
lattice operation the evidence design does not already have.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from pypeeker.dsl.anchors import Anchor, AnchorKind
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import UnknownFieldError, UnknownFollowError, UnknownUniverseError
from pypeeker.dsl.evidence import meet
from pypeeker.dsl.reach import Reach
from pypeeker.models import Confidence, FileIndex, Reference, Scope, Symbol, SymbolKind


@dataclass(frozen=True)
class _Env:
    """Per-file lookup tables a row builder needs, computed once per index."""

    index: FileIndex
    module: str
    scopes_by_id: Mapping[str, Scope]

    @staticmethod
    def of(index: FileIndex) -> _Env:
        """Build the environment for ``index``.

        The module name comes from the index's own ``MODULE`` symbol, which the
        binder always emits, rather than from path arithmetic — that keeps
        ``paths`` and ``treebuild`` out of this package's layering allow-list.
        """
        module = next(
            (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE),
            index.file_path,
        )
        return _Env(
            index=index,
            module=module,
            scopes_by_id={scope.scope_id: scope for scope in index.scopes},
        )


@dataclass(frozen=True)
class _Record:
    """One row: what it is about, what it exposes, and what it is worth.

    ``env`` is ``None`` for a row that did not come from a file. Every model
    universe's rows have one — they are built by walking an index — but the
    primitive tier's row sources (:func:`pypeeker.dsl.fact_source`) quantify
    over something the binder never saw, such as an entry in the project's
    configuration, and there is no file for such a row to be *in*. Consumers
    that would dereference it (trait resolution, which needs a
    :class:`~pypeeker.models.FileIndex`) degrade to "no traits here" rather
    than inventing one.
    """

    universe: str
    anchor: Anchor
    fields: Mapping[str, Any]
    evidence: Confidence
    env: _Env | None
    source: Any
    trait_anchor: str

    def restated(self, evidence: Confidence) -> _Record:
        """Return this record with its evidence (and its anchor's) met against ``evidence``."""
        weakened = meet(self.evidence, evidence)
        if weakened is self.evidence:
            return self
        return _Record(
            universe=self.universe,
            anchor=self.anchor.with_evidence(weakened),
            fields=self.fields,
            evidence=weakened,
            env=self.env,
            source=self.source,
            trait_anchor=self.trait_anchor,
        )


@dataclass(frozen=True)
class _Follow:
    """One navigation step out of a universe.

    ``reach`` is the honest cost of the step: ``PROJECT`` exactly when
    ``expand`` touches :attr:`~pypeeker.dsl.Corpus.resolver`.
    """

    name: str
    target: str
    reach: Reach
    expand: Callable[[_Record, Corpus], Iterator[_Record]]


@dataclass(frozen=True)
class _Universe:
    """A row source: what it publishes, what its rows are worth, where they lead.

    Rows come from exactly one of two places, and a universe must declare
    which. ``build`` is the model shape: called once per indexed file with that
    file's :class:`_Env`, which is what makes a model universe's rows
    per-file by construction. ``corpus_rows`` is the primitive tier's shape:
    called once with the whole corpus, for a row source whose rows are not a
    property of any one file.

    The five universes fork #8 fixes all use ``build``, and this module defines
    no ``corpus_rows`` universe — the tier that does is
    :mod:`pypeeker.dsl.facts`, deliberately, so that "which universes exist" and
    "how a row source may be shaped" stay separate questions.
    """

    name: str
    anchor_kind: AnchorKind
    fields: tuple[str, ...]
    build: Callable[[_Env], Iterator[_Record]] | None = None
    follows: Mapping[str, _Follow] = field(default_factory=dict)
    corpus_rows: Callable[[Corpus], Iterator[_Record]] | None = None

    def __post_init__(self) -> None:
        if (self.build is None) == (self.corpus_rows is None):
            raise ValueError(
                f"universe {self.name!r} must declare exactly one row source: "
                f"build= (once per indexed file) or corpus_rows= (once per corpus)"
            )

    def rows(self, corpus: Corpus) -> Iterator[_Record]:
        """Every row of this universe in ``corpus``, in indexed-path order."""
        if self.corpus_rows is not None:
            yield from self.corpus_rows(corpus)
            return
        build = self.build
        if build is not None:
            for index in corpus.indexes:
                yield from build(_Env.of(index))

    def require_field(self, name: str, visible: frozenset[str]) -> None:
        """Raise :class:`UnknownFieldError` unless ``name`` is currently visible."""
        if name not in visible:
            raise UnknownFieldError(name, visible, universe=self.name)

    def require_follow(self, step: str) -> _Follow:
        """Return the follow step named ``step``, or raise naming the valid ones."""
        found = self.follows.get(step)
        if found is None:
            raise UnknownFollowError(step, self.follows.keys(), universe=self.name)
        return found


def _line(obj: Symbol | Reference) -> int:
    """1-based start line of a symbol or reference, matching pypeeker's output."""
    return obj.location.span.start.line + 1


def _scope_kind(env: _Env, scope_id: str | None) -> str | None:
    """Kind of the named scope, as its string value, or ``None`` when unknown."""
    scope = env.scopes_by_id.get(scope_id or "")
    return scope.kind.value if scope is not None else None


# --------------------------------------------------------------------------
# row builders
# --------------------------------------------------------------------------

_SYMBOL_FIELDS = (
    "symbol_id",
    "name",
    "kind",
    "visibility",
    "file_path",
    "line",
    "parent_scope_id",
    "scope_kind",
    "docstring",
    "decorators",
    "annotation",
    "imported_from",
    "module",
    "is_module_level",
)


def _symbol_record(symbol: Symbol, env: _Env) -> _Record:
    """Build the symbols-universe row for ``symbol``."""
    return _Record(
        universe="symbols",
        anchor=Anchor(AnchorKind.SYMBOL, symbol.symbol_id, Confidence.DECLARED),
        fields={
            "symbol_id": symbol.symbol_id,
            "name": symbol.name,
            "kind": symbol.kind,
            "visibility": symbol.visibility,
            "file_path": symbol.location.file_path,
            "line": _line(symbol),
            "parent_scope_id": symbol.parent_scope_id,
            "scope_kind": _scope_kind(env, symbol.parent_scope_id),
            "docstring": symbol.docstring,
            "decorators": tuple(symbol.decorators),
            "annotation": symbol.type_annotation.raw if symbol.type_annotation else None,
            # Non-empty only for an IMPORT symbol: the dotted module it was
            # imported from. Exposed on the symbols universe as well as on
            # imports because a rule about *re-exports* must read it while
            # staying on DECLARED symbol rows — an imports row carries
            # ``import_confidence``, so quantifying there would silently report
            # a dynamically-recovered re-export at HEURISTIC.
            "imported_from": symbol.imported_from,
            "module": env.module,
            "is_module_level": symbol.parent_scope_id == env.module,
        },
        evidence=Confidence.DECLARED,
        env=env,
        source=symbol,
        trait_anchor=symbol.symbol_id,
    )


def _symbol_rows(env: _Env) -> Iterator[_Record]:
    """Every symbol in the file, including the file's own ``MODULE`` symbol."""
    for symbol in env.index.symbols:
        yield _symbol_record(symbol, env)


def _symbol_fields(file_index: FileIndex, symbol_id: str) -> Mapping[str, Any] | None:
    """The symbols-universe row fields for one symbol, or ``None`` when it is absent.

    The pointwise counterpart of :func:`_symbol_rows`, for callers that already
    know which anchor they are asking about rather than quantifying over a
    file. :func:`pypeeker.dsl.trait` is the one such caller: a trait provider
    is handed ``(FileIndex, symbol_id)`` and has to reconstitute exactly the
    row a ``symbols()`` selection would have produced, or a named expression
    would mean something different depending on which side asked.

    Returns ``None`` rather than raising on a miss; the provider contract in
    :mod:`pypeeker.analysis.traits` requires an unknown ``symbol_id`` to yield
    a trait, not an exception.
    """
    env = _Env.of(file_index)
    for symbol in file_index.symbols:
        if symbol.symbol_id == symbol_id:
            return _symbol_record(symbol, env).fields
    return None


_REFERENCE_FIELDS = (
    "symbol_id",
    "kind",
    "file_path",
    "line",
    "in_scope_id",
    "scope_kind",
    "resolved",
    "is_attribute_access",
    "receiver_root_symbol_id",
    "result_used",
    "escapes",
    "module",
)


def _reference_record(ref: Reference, env: _Env) -> _Record:
    """Build the references-universe row for ``ref``.

    The anchor id is synthetic — a reference has no id of its own — but stable
    and precise: ``<symbol-id>@<file>:<line>:<column>`` points at the use site.
    """
    start = ref.location.span.start
    anchor_id = f"{ref.symbol_id}@{ref.location.file_path}:{start.line + 1}:{start.column}"
    return _Record(
        universe="references",
        anchor=Anchor(AnchorKind.REFERENCE, anchor_id, Confidence.DECLARED),
        fields={
            "symbol_id": ref.symbol_id,
            "kind": ref.kind,
            "file_path": ref.location.file_path,
            "line": _line(ref),
            "in_scope_id": ref.in_scope_id,
            "scope_kind": _scope_kind(env, ref.in_scope_id),
            "resolved": ref.resolved,
            "is_attribute_access": ref.is_attribute_access,
            "receiver_root_symbol_id": ref.receiver_root_symbol_id,
            "result_used": ref.result_used,
            "escapes": ref.escapes,
            "module": env.module,
        },
        evidence=Confidence.DECLARED,
        env=env,
        source=ref,
        trait_anchor=ref.symbol_id,
    )


def _reference_rows(env: _Env) -> Iterator[_Record]:
    """Every reference recorded in the file."""
    for ref in env.index.references:
        yield _reference_record(ref, env)


_IMPORT_FIELDS = (
    "symbol_id",
    "name",
    "imported_from",
    "import_confidence",
    "file_path",
    "line",
    "module",
)


def _import_rows(env: _Env) -> Iterator[_Record]:
    """Every ``IMPORT`` symbol in the file, carrying its binding's confidence.

    This is the one universe whose rows are not intrinsically ``DECLARED``:
    ``import_confidence`` is ``HEURISTIC`` for a binding recovered from a
    dynamic ``importlib.import_module`` call, and that level joins the meet for
    every expression evaluated over the row.
    """
    for symbol in env.index.symbols:
        if symbol.kind is not SymbolKind.IMPORT:
            continue
        evidence = symbol.import_confidence or Confidence.DECLARED
        yield _Record(
            universe="imports",
            anchor=Anchor(AnchorKind.IMPORT, symbol.symbol_id, evidence),
            fields={
                "symbol_id": symbol.symbol_id,
                "name": symbol.name,
                "imported_from": symbol.imported_from,
                "import_confidence": evidence,
                "file_path": symbol.location.file_path,
                "line": _line(symbol),
                "module": env.module,
            },
            evidence=evidence,
            env=env,
            source=symbol,
            trait_anchor=symbol.symbol_id,
        )


_MODULE_FIELDS = ("module", "file_path", "has_errors")


def _module_record(env: _Env) -> _Record:
    """Build the single modules-universe row for a file."""
    return _Record(
        universe="modules",
        anchor=Anchor(AnchorKind.MODULE, env.module, Confidence.DECLARED),
        fields={
            "module": env.module,
            "file_path": env.index.file_path,
            "has_errors": bool(env.index.errors),
        },
        evidence=Confidence.DECLARED,
        env=env,
        source=env.index,
        trait_anchor=env.module,
    )


def _module_rows(env: _Env) -> Iterator[_Record]:
    """One row per indexed file."""
    yield _module_record(env)


_SCOPE_FIELDS = (
    "scope_id",
    "name",
    "kind",
    "file_path",
    "parent_scope_id",
    "start_line",
    "end_line",
    "module",
)


def _scope_record(scope: Scope, env: _Env) -> _Record:
    """Build the scopes-universe row for ``scope``."""
    return _Record(
        universe="scopes",
        anchor=Anchor(AnchorKind.SCOPE, scope.scope_id, Confidence.DECLARED),
        fields={
            "scope_id": scope.scope_id,
            "name": scope.name,
            "kind": scope.kind,
            "file_path": scope.file_path,
            "parent_scope_id": scope.parent_scope_id,
            "start_line": scope.span.start.line + 1,
            "end_line": scope.span.end.line + 1,
            "module": env.module,
        },
        evidence=Confidence.DECLARED,
        env=env,
        source=scope,
        trait_anchor=scope.scope_id,
    )


def _scope_rows(env: _Env) -> Iterator[_Record]:
    """Every lexical scope in the file."""
    for scope in env.index.scopes:
        yield _scope_record(scope, env)


# --------------------------------------------------------------------------
# follow steps
# --------------------------------------------------------------------------


def _follow_symbol_references(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """References to this symbol recorded in the same file."""
    del corpus
    symbol: Symbol = record.source
    for ref in record.env.index.references:
        if ref.symbol_id == symbol.symbol_id:
            yield _reference_record(ref, record.env)


def _follow_symbol_scope(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The scope this symbol is declared in."""
    del corpus
    scope = record.env.scopes_by_id.get(record.source.parent_scope_id or "")
    if scope is not None:
        yield _scope_record(scope, record.env)


def _follow_to_module(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The module row for the file this row came from."""
    del corpus
    yield _module_record(record.env)


def _located_symbol(corpus: Corpus, symbol_id: str) -> Iterator[_Record]:
    """The symbols-universe row for ``symbol_id``, if the corpus has it."""
    found = corpus.locate(symbol_id)
    if found is not None:
        symbol, index = found
        yield _symbol_record(symbol, _Env.of(index))


def _follow_symbol_definition(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The canonical definition this symbol resolves to, following imports across files."""
    yield from _located_symbol(corpus, corpus.resolver.resolve_definition(record.source.symbol_id))


def _follow_reference_symbol(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The symbol this reference names, when it is declared in the same file."""
    del corpus
    for symbol in record.env.index.symbols:
        if symbol.symbol_id == record.source.symbol_id:
            yield _symbol_record(symbol, record.env)
            return


def _follow_reference_scope(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The scope this reference occurs in."""
    del corpus
    scope = record.env.scopes_by_id.get(record.source.in_scope_id)
    if scope is not None:
        yield _scope_record(scope, record.env)


def _follow_reference_definition(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The canonical definition this reference binds to, across files."""
    yield from _located_symbol(corpus, corpus.resolver.resolve_reference(record.source))


def _follow_import_target(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The definition an import binds to.

    Rows produced here inherit the import's evidence, so a target reached
    through a dynamically-recovered import stays ``HEURISTIC``.
    """
    target = corpus.resolver.resolve_definition(record.source.symbol_id)
    for produced in _located_symbol(corpus, target):
        yield produced.restated(record.evidence)


def _follow_module_symbols(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """Every symbol declared in this module's file."""
    del corpus
    yield from _symbol_rows(record.env)


def _follow_module_scopes(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """Every scope in this module's file."""
    del corpus
    yield from _scope_rows(record.env)


def _follow_module_imports(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """Every import bound in this module's file."""
    del corpus
    yield from _import_rows(record.env)


def _follow_scope_symbols(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The symbols this scope directly holds."""
    del corpus
    for symbol in record.env.index.symbols:
        if symbol.parent_scope_id == record.source.scope_id:
            yield _symbol_record(symbol, record.env)


def _follow_scope_parent(record: _Record, corpus: Corpus) -> Iterator[_Record]:
    """The enclosing scope, if this one has a parent."""
    del corpus
    parent = record.env.scopes_by_id.get(record.source.parent_scope_id or "")
    if parent is not None:
        yield _scope_record(parent, record.env)


# --------------------------------------------------------------------------
# the five universes
# --------------------------------------------------------------------------

_SYMBOLS = _Universe(
    name="symbols",
    anchor_kind=AnchorKind.SYMBOL,
    fields=_SYMBOL_FIELDS,
    build=_symbol_rows,
    follows={
        "references": _Follow("references", "references", Reach.FILE, _follow_symbol_references),
        "scope": _Follow("scope", "scopes", Reach.FILE, _follow_symbol_scope),
        "module": _Follow("module", "modules", Reach.FILE, _follow_to_module),
        "definition": _Follow(
            "definition", "symbols", Reach.PROJECT, _follow_symbol_definition
        ),
    },
)

_REFERENCES = _Universe(
    name="references",
    anchor_kind=AnchorKind.REFERENCE,
    fields=_REFERENCE_FIELDS,
    build=_reference_rows,
    follows={
        "symbol": _Follow("symbol", "symbols", Reach.FILE, _follow_reference_symbol),
        "scope": _Follow("scope", "scopes", Reach.FILE, _follow_reference_scope),
        "definition": _Follow(
            "definition", "symbols", Reach.PROJECT, _follow_reference_definition
        ),
    },
)

_IMPORTS = _Universe(
    name="imports",
    anchor_kind=AnchorKind.IMPORT,
    fields=_IMPORT_FIELDS,
    build=_import_rows,
    follows={
        "module": _Follow("module", "modules", Reach.FILE, _follow_to_module),
        "target": _Follow("target", "symbols", Reach.PROJECT, _follow_import_target),
    },
)

_MODULES = _Universe(
    name="modules",
    anchor_kind=AnchorKind.MODULE,
    fields=_MODULE_FIELDS,
    build=_module_rows,
    follows={
        "symbols": _Follow("symbols", "symbols", Reach.FILE, _follow_module_symbols),
        "scopes": _Follow("scopes", "scopes", Reach.FILE, _follow_module_scopes),
        "imports": _Follow("imports", "imports", Reach.FILE, _follow_module_imports),
    },
)

_SCOPES = _Universe(
    name="scopes",
    anchor_kind=AnchorKind.SCOPE,
    fields=_SCOPE_FIELDS,
    build=_scope_rows,
    follows={
        "symbols": _Follow("symbols", "symbols", Reach.FILE, _follow_scope_symbols),
        "parent": _Follow("parent", "scopes", Reach.FILE, _follow_scope_parent),
    },
)

_UNIVERSES: Mapping[str, _Universe] = {
    universe.name: universe
    for universe in (_SYMBOLS, _REFERENCES, _IMPORTS, _MODULES, _SCOPES)
}

UNIVERSE_NAMES: tuple[str, ...] = tuple(_UNIVERSES)
"""The five universes, in the order fork #8 lists them."""


def _universe(name: str) -> _Universe:
    """Look up a universe by name, raising a loud error listing the five."""
    found = _UNIVERSES.get(name)
    if found is None:
        raise UnknownUniverseError(name, UNIVERSE_NAMES)
    return found


def universe_fields(name: str) -> tuple[str, ...]:
    """The field names the universe ``name`` declares.

    Public because it is the only honest way for a caller — a CLI ``--help``,
    a test, an LLM composing an expression — to discover what a universe
    exposes without reading this module's source.
    """
    return _universe(name).fields


def universe_follows(name: str) -> tuple[str, ...]:
    """The follow steps available from the universe ``name``, sorted."""
    return tuple(sorted(_universe(name).follows))
