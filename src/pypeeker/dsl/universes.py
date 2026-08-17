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

Declared also means **extended deliberately**. The ``references`` universe grew
eleven fields in phase 3e and two more in phase 3f (see
:func:`_reference_record`): the receiver chain the binder recorded, the
same-file symbol the receiver root names, the same-file symbol the reference
itself names, the function body the reference sits in, whether that scope runs
at import time, and the file's module scope id. Each is a per-file *model* fact
rather than a rule's
judgement, which is the test a new field has to pass — a field is a thing the
binder saw, published raw, and a rule is what decides what it means. The
alternative for each was an ``opaque`` reconstructing from ``symbol_id`` a fact
the index already holds (the grammar has no string operators, so even reading a
name out of an id needs one), which would move model access into rule bodies
where ``--why`` cannot describe it.

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
from pypeeker.models import (
    Confidence,
    FileIndex,
    Reference,
    Scope,
    ScopeKind,
    Symbol,
    SymbolKind,
)

_FUNCTION_SYMBOL_KINDS = (SymbolKind.FUNCTION, SymbolKind.METHOD)
_FUNCTION_SCOPE_KINDS = (ScopeKind.FUNCTION, ScopeKind.LAMBDA)
_IMPORT_TIME_TRANSPARENT_KINDS = (ScopeKind.CLASS, ScopeKind.COMPREHENSION)
"""Scope kinds whose bodies run when their parent runs.

``check.builtin.import_time_side_effects._DEFERRED_FREE_KINDS``, copied rather
than imported (``dsl`` may not import ``check``): a class body or a
comprehension at module scope executes during the import, where a function or
lambda body waits to be called.
"""


@dataclass(frozen=True)
class _Env:
    """Per-file lookup tables a row builder needs, computed once per index."""

    index: FileIndex
    module: str
    scopes_by_id: Mapping[str, Scope]
    _cache: dict[str, Any] = field(
        default_factory=dict, compare=False, repr=False
    )
    """Memo for the two lookups a reference row needs and other rows do not.

    Deliberately lazy, and deliberately not built in :meth:`of`. ``of`` is
    called once per indexed file per universe sweep — and, in the visibility
    family, once per *followed row* (:func:`_located_symbol`) — so anything
    eager here is a cost every rule pays, including the ones that never read
    :attr:`symbols_by_id` or call :meth:`enclosing_function`.
    """

    @property
    def symbols_by_id(self) -> Mapping[str, Symbol]:
        """The file's symbols by id, **last binding wins** on a duplicate id.

        Last-wins is not incidental: every frozen rule that needs this map
        builds it as ``{s.symbol_id: s for s in index.symbols}``, and two
        declarations can share an id (a source root that is itself a package
        makes two files' symbols collide, and the harness's ``boundaries``
        corpus carries that shape deliberately). It is therefore **not** the
        first-wins ``next()`` scan :meth:`of` uses to find the module id; the
        two answer different questions and must not be merged.
        """
        found = self._cache.get("symbols_by_id")
        if found is None:
            found = {symbol.symbol_id: symbol for symbol in self.index.symbols}
            self._cache["symbols_by_id"] = found
        return found

    def enclosing_function(self, scope_id: str | None) -> Symbol | None:
        """The FUNCTION/METHOD symbol whose body ``scope_id`` sits inside, or ``None``.

        Walk up ``parent_scope_id`` from ``scope_id`` and stop at the first
        FUNCTION or LAMBDA scope; the answer is the symbol that scope *is*,
        when the file declares one of function kind. Class and comprehension
        scopes are walked **through**, which is what makes a mutation inside a
        class body defined in a function, or inside a comprehension, belong to
        the enclosing function.

        Three ways this is ``None``, and each is a real shape: the scope chain
        reaches the module without passing a function; a scope id names no
        scope in this file; or the innermost function scope is a **lambda**,
        which owns no FUNCTION/METHOD symbol — a mutation in a lambda body
        therefore has no function to charge, and the frozen rules'
        ``AnalysisContext.for_function`` subtree walk agrees, because it stops
        descending at exactly that boundary.

        This is the pointwise form of the frozen loop "for every FUNCTION /
        METHOD symbol, for every reference in its scope subtree": that loop's
        set of enclosing functions for one reference is a singleton or empty,
        and it is this. ``analysis.context._function_scope_id`` requires
        ``scope.scope_id == symbol.symbol_id``, so the map lookup below is the
        same correspondence rather than an approximation of it.
        """
        cache: dict[str | None, Symbol | None] = self._cache.setdefault("enclosing", {})
        if scope_id in cache:
            return cache[scope_id]
        found = self._walk_to_function(scope_id)
        cache[scope_id] = found
        return found

    def runs_at_import(self, scope_id: str | None) -> bool:
        """True when the statements in ``scope_id`` execute on ``import``.

        The pointwise form of the frozen
        ``check.builtin.import_time_side_effects._import_time_scope_ids``, whose
        inner ``runs_at_import`` this copies: the module scope itself runs at
        import time, a CLASS or COMPREHENSION scope runs at import time exactly
        when its parent does, and every other scope kind — a function or lambda
        body — breaks the chain, because it only runs when called. A scope id
        naming no scope in this file is ``False``, which is the answer the
        frozen membership test ``ref.in_scope_id not in import_time_scopes``
        gives for the same input: that set is built from this file's scope ids
        alone.

        The frozen helper materializes the whole set per file; this answers one
        scope at a time and memoizes, because a rule asks about the scopes its
        references actually sit in and nothing else.

        One deliberate difference, and it is a robustness addition rather than a
        copy: the iterative walk carries a ``seen`` guard, exactly as
        :meth:`_walk_to_function` does, where the frozen helper recurses and
        writes its memo only *after* the recursive call — so a cyclic parent
        chain would recurse until ``RecursionError`` there. Unobservable on
        binder output, whose scope trees are acyclic.
        """
        cache: dict[str | None, bool] = self._cache.setdefault("import_time", {})
        if scope_id in cache:
            return cache[scope_id]
        found = self._walk_to_import_time(scope_id)
        cache[scope_id] = found
        return found

    def _walk_to_import_time(self, scope_id: str | None) -> bool:
        """:meth:`runs_at_import` without the memo. ``seen`` guards a cyclic chain."""
        seen: set[str] = set()
        current = scope_id
        while current is not None and current not in seen:
            seen.add(current)
            scope = self.scopes_by_id.get(current)
            if scope is None:
                return False
            if scope.kind is ScopeKind.MODULE:
                return True
            if scope.kind not in _IMPORT_TIME_TRANSPARENT_KINDS:
                return False
            current = scope.parent_scope_id
        return False

    @property
    def module_scope_id(self) -> str:
        """The file's ``MODULE`` scope id, or ``""`` when it has none.

        The frozen ``check.builtin.import_time_side_effects._module_path``,
        which is what that rule's ``allow`` patterns are matched against as the
        "calling module". Deliberately **not** :attr:`module`, and the two
        differ on a real corpus: ``module`` is the module *symbol*'s id with an
        ``index.file_path`` fallback, and ``binder.binder._emit_module_symbol``
        emits no MODULE symbol at all when the module path is empty — a source
        root that is itself a package, the shape the harness's ``purity``
        corpus carries. There ``module`` is a file path where the frozen module
        path is ``""``, so publishing ``module`` in its place would silently
        change which ``allow`` patterns match.

        Memoized with an ``in`` test rather than a truthiness test: ``""`` is a
        legitimate answer, and a falsy-keyed memo would recompute on exactly
        the shape this field exists to get right.
        """
        cache = self._cache
        if "module_scope_id" not in cache:
            cache["module_scope_id"] = next(
                (
                    scope.scope_id
                    for scope in self.index.scopes
                    if scope.kind is ScopeKind.MODULE
                ),
                "",
            )
        return cache["module_scope_id"]

    def _walk_to_function(self, scope_id: str | None) -> Symbol | None:
        """:meth:`enclosing_function` without the memo. ``seen`` guards a cyclic chain."""
        seen: set[str] = set()
        current = scope_id
        while current is not None and current not in seen:
            seen.add(current)
            scope = self.scopes_by_id.get(current)
            if scope is None:
                return None
            if scope.kind in _FUNCTION_SCOPE_KINDS:
                symbol = self.symbols_by_id.get(scope.scope_id)
                if symbol is not None and symbol.kind in _FUNCTION_SYMBOL_KINDS:
                    return symbol
                return None
            current = scope.parent_scope_id
        return None

    @staticmethod
    def of(index: FileIndex) -> _Env:
        """Build the environment for ``index``.

        The module name comes from the index's own ``MODULE`` symbol, which the
        binder always emits, rather than from path arithmetic — that keeps
        ``paths`` and ``treebuild`` out of this package's layering allow-list.

        A file the binder gave no ``MODULE`` symbol (a source root that is
        itself a package, whose root-level ``__init__.py`` collapses to an
        empty module path) falls back to ``index.file_path``. That fallback is
        deliberately **not** the empty string, and deliberately not dropped
        here: it is a display value that keeps every ``symbols()`` row's
        ``module`` field a non-``None`` string, so row shape stays uniform for
        every consumer, module-less or not — including
        ``row.is_module_level``'s ``parent_scope_id == env.module`` test, which
        must keep comparing false for a module-less file's top-level symbols
        rather than start comparing true. The "is this file a module"
        question a rule actually needs to ask is answered elsewhere, on
        purpose, by two separate ports of the frozen ``module_id is None ->
        continue`` guard that must move together: on the project-column side,
        ``pypeeker.dsl.columns._modules_by_file`` omits the file entirely; on
        the row side, ``pypeeker.dsl.visibility.MODULE_FILES`` is a semi-join
        candidate rows are tested against. Neither reads this fallback as a
        module id (``dsl-rewrite.md`` ledger, phase 3b, reconciled).
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

    @property
    def reach(self) -> Reach:
        """How far out of one file producing this source's rows reads. Derived.

        A ``build`` source is handed one file's :class:`_Env` and can read
        nothing else, so it is ``FILE``. A ``corpus_rows`` source is handed the
        whole :class:`~pypeeker.dsl.Corpus` — that is the entire difference
        between the two shapes — so it is ``PROJECT``. Derived from which
        constructor argument was supplied rather than declared beside it, for
        the reason :attr:`pypeeker.dsl.Selection.reach` gives: a declared reach
        is a reach that can drift from the code that spends it.
        """
        return Reach.PROJECT if self.corpus_rows is not None else Reach.FILE

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
    "id_module",
    "is_module_level",
)


def _symbol_record(symbol: Symbol, env: _Env) -> _Record:
    """Build the symbols-universe row for ``symbol``.

    ``module`` and ``id_module`` are two different questions and agree almost
    always. ``module`` is what the *file* is called: the id of its ``MODULE``
    symbol, or — when the binder emitted none — the file path, so the field is
    never empty. ``id_module`` is the module segment of the *symbol's own id*,
    ``symbol_id.split(":", 1)[0]``, which is what a pattern written against a
    dotted module prefix is really matched against.

    They part company in exactly one case, and it is reachable: a configured
    source root that is itself a package makes ``paths.module_path_from``
    return ``""`` for its ``__init__.py``, the binder then emits no ``MODULE``
    symbol, and every symbol in that file has an id like ``":helper"`` while
    ``module`` has fallen back to ``"src/__init__.py"``. Matching an fnmatch
    pattern against the fallback would fire on a path that is not a module
    name; ``id_module`` is ``""`` there, which is what the frozen engine
    compares against.
    """
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
            "id_module": symbol.symbol_id.split(":", 1)[0],
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
    # the receiver, as the binder recorded it
    "receiver_chain",
    # the same-file symbol the receiver root names
    "receiver_root_kind",
    "receiver_root_name",
    "receiver_root_imported_from",
    "receiver_root_parent_scope_id",
    # the same-file symbol this reference itself names. `binding_` and not
    # `target_`: `dsl/visibility.py` derives `target_name` / `target_module` /
    # `target_visibility` on references rows from the PROJECT-WIDE definition
    # column, which is a different symbol whenever the reference crosses a file.
    "binding_kind",
    "binding_name",
    "binding_parent_scope_id",
    # the function body this reference sits in
    "enclosing_function_id",
    "enclosing_function_kind",
    "enclosing_function_id_module",
    # when this reference's scope runs, and the module path an allow pattern
    # matching "the calling module" is matched against
    "runs_at_import",
    "module_scope_id",
)


def _reference_record(ref: Reference, env: _Env) -> _Record:
    """Build the references-universe row for ``ref``.

    The anchor id is synthetic — a reference has no id of its own — but stable
    and precise: ``<symbol-id>@<file>:<line>:<column>`` points at the use site.

    A row publishes four things (phase 3e, the mutation pair): the reference,
    the same-file symbol it names (``binding_*``), the same-file symbol its
    **receiver root** names (``receiver_root_*``), and the function body it sits
    in (``enclosing_function_*``). All three lookups are **same-file by
    construction** — they go through ``env.symbols_by_id``, which is exactly the
    per-index ``{s.symbol_id: s for s in index.symbols}`` every frozen rule
    reading receiver metadata builds, and deliberately **not**
    :meth:`pypeeker.dsl.Corpus.locate`, which would answer with another file's
    declaration under an id collision. That is also why the reference's own
    symbol is published as ``binding_*`` and not ``target_*``: the
    ``target_name`` / ``target_module`` / ``target_visibility``
    :mod:`pypeeker.dsl.visibility` derives on these same rows come from the
    project-wide definition column, which names a different symbol whenever the
    reference crosses a file.

    Each of the seven symbol-derived fields is ``None`` when its lookup misses,
    which is a real answer rather than an absence: a receiver root that is a
    dynamic expression, or a name declared in another file, resolves to no
    same-file symbol, and the frozen rules read exactly that as "not a
    parameter" / "not a module-level variable".

    Phase 3f added two more, for the impurity pair: ``runs_at_import`` — whether
    the reference's own scope executes during the import, the pointwise form of
    the frozen ``_import_time_scope_ids`` set — and ``module_scope_id``, the
    file's MODULE *scope* id, which is the frozen ``_module_path`` and is
    **not** ``module`` (see :meth:`_Env.module_scope_id` for the corpus where
    they differ). Both pass the same test the phase-3e eleven do: a fact the
    binder recorded, published raw, with the judgement left to the rule.

    The two parent-scope ids are published **raw**, not pre-judged into an
    ``is_module_level`` boolean. Two consumers ask two different questions of
    them and disagree on a real corpus: the frozen ``_is_module_scope`` is
    ``":" not in scope_id``, while the symbols universe's ``is_module_level``
    is ``parent_scope_id == env.module``. On a source root that is itself a
    package (the harness's ``purity`` corpus) a top-level symbol has
    ``parent_scope_id == ""`` and ``env.module`` is a file path, so the first
    is ``True`` where the second is ``False``. Publishing one boolean would bake
    one rule's policy into the universe and be wrong for the other.
    """
    start = ref.location.span.start
    anchor_id = f"{ref.symbol_id}@{ref.location.file_path}:{start.line + 1}:{start.column}"
    symbols_by_id = env.symbols_by_id
    receiver_root = (
        symbols_by_id.get(ref.receiver_root_symbol_id)
        if ref.receiver_root_symbol_id is not None
        else None
    )
    target = symbols_by_id.get(ref.symbol_id)
    enclosing = env.enclosing_function(ref.in_scope_id)
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
            "receiver_chain": tuple(ref.receiver_chain or ()),
            "receiver_root_kind": receiver_root.kind if receiver_root else None,
            "receiver_root_name": receiver_root.name if receiver_root else None,
            "receiver_root_imported_from": (
                receiver_root.imported_from if receiver_root else None
            ),
            "receiver_root_parent_scope_id": (
                receiver_root.parent_scope_id if receiver_root else None
            ),
            "binding_kind": target.kind if target else None,
            "binding_name": target.name if target else None,
            "binding_parent_scope_id": target.parent_scope_id if target else None,
            "enclosing_function_id": enclosing.symbol_id if enclosing else None,
            "enclosing_function_kind": enclosing.kind if enclosing else None,
            "enclosing_function_id_module": (
                enclosing.symbol_id.split(":", 1)[0] if enclosing else None
            ),
            "runs_at_import": env.runs_at_import(ref.in_scope_id),
            "module_scope_id": env.module_scope_id,
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
