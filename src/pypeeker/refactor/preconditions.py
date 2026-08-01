"""First-class planner preconditions.

Each refactor planner (rename, extract-variable, extract-method,
inline-variable) historically validated its prerequisites with inline
``raise`` statements. This module lifts those checks into small, named
:class:`Precondition` objects so they can be evaluated independently of the
planners — the composite batch planner re-validates "guarded" intents at
materialization time by re-running a planner's precondition set (TASK-88).

Contract
--------
- A precondition has a stable :attr:`Precondition.name` and an
  ``evaluate() -> PreconditionResult`` method returning a pass/fail flag
  plus, on failure, a reason string identical to the planner's historical
  error message. ``evaluate()`` never raises for an ordinary failed check.
- Inputs are passed explicitly at construction — usually the
  :class:`~pypeeker.storage.IndexStore` /
  :class:`~pypeeker.query.engine.SemanticQueryEngine` and the plan
  parameters. Preconditions that need values computed mid-plan (the resolved
  symbol for rename's conflict check, the parsed CST for extract-variable,
  the range dataflow for extract-method) take those values as constructor
  arguments. Each planner's ``preconditions(...)`` method recomputes those
  inputs, so a caller that wants a check against *current* state should
  rebuild the set through that method rather than re-evaluating cached
  instances whose constructor inputs may have gone stale.
- Resolution-style preconditions cache what they resolved (e.g.
  :attr:`SymbolResolvesUniquely.symbol`) on a successful ``evaluate()`` so
  the planner and dependent preconditions can reuse it without re-querying.
- :func:`evaluate_in_order` drives an ordered set, stopping at the first
  failure. It accepts generators that construct later preconditions from the
  cached results of earlier ones; the generator is never advanced past a
  failing precondition.
- A precondition whose failure the ``check --fix`` report must map onto a
  legacy refusal slug (TASK-125: the six phase-4 remedy planners —
  delete-symbol, remove-import, rewrite-star-import, tuplify, replace-text,
  rename-docstring-param — ported from the superseded fix protocol) carries
  that slug as a
  :attr:`Precondition.slug` class attribute (``"file-missing"`` /
  ``"stale-index"`` / ``"text-mismatch"`` / ``"ambiguous"``). One precondition
  class always has exactly one fixed slug; two checks that happen to share a
  slug are still two classes if their failure wording differs. A planner
  never hardcodes the slug at its raise site — it reads
  ``evaluated[-1].slug`` off the failing precondition and passes it through
  as the error's ``code``, so the mapping lives in exactly one place (this
  module) and the error's ``.precondition`` carries the failing
  precondition's :attr:`~Precondition.name` alongside it. Preconditions with
  no legacy slug (every rename/extract/inline check, all pre-TASK-125) leave
  :attr:`slug` at its default ``None``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from re import Pattern
from typing import ClassVar, Iterable

from tree_sitter import Node

from pypeeker.analysis import (
    TYPE_ANNOTATION,
    VARIABLE_MUTATION,
    ParamsSection,
    get_trait_provider,
    is_inferred_list,
    param_drift,
    parse_documented_params,
    signature_params,
)
from pypeeker.models import (
    FileIndex,
    ReferenceKind,
    Scope,
    ScopeKind,
    Symbol,
    SymbolKind,
    module_of,
)
from pypeeker.query import SemanticQueryEngine
from pypeeker.refactor import cst
from pypeeker.refactor.dataflow import (
    RangeDataFlow,
    analyze_range,
    enclosing_function_scope,
)
from pypeeker.refactor.text_anchor import is_definition_header, line_end, line_start_offsets
from pypeeker.storage import IndexStore

# Selecting one of these means the user didn't select an expression.
_NON_EXPRESSION_TYPES = frozenset(
    {"module", "block", "function_definition", "class_definition"}
)


@dataclass(frozen=True)
class PreconditionResult:
    """Outcome of evaluating a single precondition."""

    ok: bool
    reason: str = ""


_PASS = PreconditionResult(ok=True)


def _fail(reason: str) -> PreconditionResult:
    return PreconditionResult(ok=False, reason=reason)


class Precondition(ABC):
    """A named, independently evaluable prerequisite of a refactor plan."""

    name: ClassVar[str]
    slug: ClassVar[str | None] = None
    """The legacy ``check --fix`` refusal code this precondition's failure maps
    onto (``"file-missing"`` / ``"stale-index"`` / ``"text-mismatch"`` /
    ``"ambiguous"``), or ``None`` for preconditions with no such mapping (see
    the module docstring's TASK-125 note)."""

    @abstractmethod
    def evaluate(self) -> PreconditionResult:
        """Check the precondition; report failure via the result, not by raising."""


def evaluate_in_order(
    preconditions: Iterable[Precondition],
) -> tuple[list[Precondition], PreconditionResult | None]:
    """Evaluate preconditions in order, stopping at the first failure.

    Returns ``(evaluated, failure)``: ``evaluated`` holds every precondition
    that was constructed (the failing one last, if any) and ``failure`` is
    the failing :class:`PreconditionResult`, or ``None`` if all passed.

    The iterable may be a generator that constructs later preconditions from
    the cached results of earlier ones (e.g. the resolved symbol); it is not
    advanced past a failing precondition, so dependent construction only runs
    once its prerequisites hold.
    """
    evaluated: list[Precondition] = []
    for precondition in preconditions:
        evaluated.append(precondition)
        result = precondition.evaluate()
        if not result.ok:
            return evaluated, result
    return evaluated, None


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


class ValidIdentifier(Precondition):
    """The new name is a valid Python identifier (rename and extract)."""

    name = "valid-identifier"

    def __init__(self, new_name: str) -> None:
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.new_name.isidentifier():
            return _fail(f"Invalid Python identifier: {self.new_name}")
        return _PASS


class FileExists(Precondition):
    """The target file exists on disk (extract-variable)."""

    name = "file-exists"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self._index_store.file_exists(self.file_path):
            return _fail(f"File not found: {self.file_path}")
        return _PASS


class FileFresh(Precondition):
    """The target file is indexed and the index is not stale (extract)."""

    name = "file-fresh"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self._index_store.is_stale(self.file_path):
            return _fail(f"File is stale or not indexed: {self.file_path}")
        return _PASS


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


class RenameFlagsCompatible(Precondition):
    """``--include-exports`` and ``--keep-export`` are not combined."""

    name = "rename-flags-compatible"

    def __init__(self, include_exports: bool, keep_export: bool) -> None:
        self.include_exports = include_exports
        self.keep_export = keep_export

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.include_exports and self.keep_export:
            return _fail(
                "--include-exports and --keep-export are mutually exclusive: "
                "one changes the public export name, the other preserves it."
            )
        return _PASS


class SymbolResolvesUniquely(Precondition):
    """The symbol id matches exactly one symbol (rename).

    Caches the resolved symbol as :attr:`symbol` on a successful evaluation.
    """

    name = "symbol-resolves-uniquely"

    def __init__(self, engine: SemanticQueryEngine, symbol_id: str) -> None:
        self._engine = engine
        self.symbol_id = symbol_id
        self.symbol: Symbol | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        results = self._engine.find_symbol(self.symbol_id)
        if not results:
            return _fail(f"Symbol not found: {self.symbol_id}")
        if len(results) > 1:
            ids = [s.symbol_id for s in results]
            return _fail(
                f"Ambiguous symbol '{self.symbol_id}', matched {len(results)}: {ids}. "
                "Use the full symbol ID to disambiguate."
            )
        self.symbol = results[0]
        return _PASS


class NewNameDiffers(Precondition):
    """The new name differs from the symbol's current name."""

    name = "new-name-differs"

    def __init__(self, old_name: str, new_name: str) -> None:
        self.old_name = old_name
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.old_name == self.new_name:
            return _fail(f"New name is same as old name: {self.new_name}")
        return _PASS


class NoScopeNameConflict(Precondition):
    """No sibling symbol in the same scope already uses the new name.

    Takes the resolved symbol as a constructor argument (mid-plan value
    produced by :class:`SymbolResolvesUniquely`).
    """

    name = "no-scope-name-conflict"

    def __init__(self, index_store: IndexStore, symbol: Symbol, new_name: str) -> None:
        self._index_store = index_store
        self.symbol = symbol
        self.new_name = new_name

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.symbol.parent_scope_id:
            index = self._index_store.load(self.symbol.location.file_path)
            if index:
                for s in index.symbols:
                    if (
                        s.parent_scope_id == self.symbol.parent_scope_id
                        and s.name == self.new_name
                        and s.symbol_id != self.symbol.symbol_id
                    ):
                        return _fail(
                            f"Name conflict: '{self.new_name}' already exists in scope "
                            f"'{self.symbol.parent_scope_id}' as {s.symbol_id}"
                        )
        return _PASS


class AffectedFilesFresh(Precondition):
    """All files the rename touches are indexed and not stale.

    Takes the computed set of affected files as a constructor argument
    (mid-plan value: the planner derives it from the edit locations).
    """

    name = "affected-files-fresh"

    def __init__(self, index_store: IndexStore, file_paths: Iterable[str]) -> None:
        self._index_store = index_store
        self.file_paths = set(file_paths)

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        for fp in self.file_paths:
            if self._index_store.is_stale(fp):
                return _fail(
                    f"File '{fp}' is stale or not indexed. "
                    "Run 'pypeeker index' first."
                )
        return _PASS


# ---------------------------------------------------------------------------
# Extract-variable
# ---------------------------------------------------------------------------


class ExpressionFound(Precondition):
    """The selected range covers an expression node.

    Takes the parsed CST root as a constructor argument (mid-plan value) and
    caches the found node as :attr:`node` on a successful evaluation.
    """

    name = "expression-found"

    def __init__(
        self,
        root: Node,
        start: tuple[int, int],
        end: tuple[int, int],
        file_path: str,
    ) -> None:
        self._root = root
        self.start = start
        self.end = end
        self.file_path = file_path
        self.node: Node | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        node = cst.node_spanning(self._root, self.start, self.end)
        if node is None or node.type in _NON_EXPRESSION_TYPES or node.parent is None:
            return _fail(
                f"No expression found at {self.file_path}:{self.start[0]}:{self.start[1]}"
            )
        self.node = node
        return _PASS


class InsideStatement(Precondition):
    """The selected expression sits inside a statement.

    Takes the found expression node as a constructor argument (mid-plan value
    produced by :class:`ExpressionFound`) and caches the enclosing statement
    as :attr:`statement` on a successful evaluation.
    """

    name = "inside-statement"

    def __init__(self, node: Node) -> None:
        self._node = node
        self.statement: Node | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        statement = cst.enclosing_statement(self._node)
        if statement is None:
            return _fail("Selection is not inside a statement")
        self.statement = statement
        return _PASS


# ---------------------------------------------------------------------------
# Extract-method
# ---------------------------------------------------------------------------


class RangeInsideFunction(Precondition):
    """The line range lies inside a function.

    Caches the range dataflow summary as :attr:`dataflow` on a successful
    evaluation.
    """

    name = "range-inside-function"

    def __init__(
        self, index_store: IndexStore, file_path: str, start_line: int, end_line: int
    ) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.dataflow: RangeDataFlow | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        dataflow = analyze_range(
            self._index_store, self.file_path, self.start_line, self.end_line
        )
        if dataflow is None:
            return _fail("Range is not inside a function")
        self.dataflow = dataflow
        return _PASS


class NoControlFlowEscape(Precondition):
    """The range contains no return/break/continue.

    Takes the range dataflow as a constructor argument (mid-plan value
    produced by :class:`RangeInsideFunction`).
    """

    name = "no-control-flow-escape"

    def __init__(self, dataflow: RangeDataFlow) -> None:
        self.dataflow = dataflow

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.dataflow.has_escape:
            return _fail(
                "Range contains return/break/continue; cannot extract safely"
            )
        return _PASS


class TopLevelFunctionOnly(Precondition):
    """The enclosing function is a top-level (module-scope) function.

    Caches the enclosing function scope as :attr:`func_scope` on a successful
    evaluation.
    """

    name = "top-level-function-only"

    def __init__(
        self, index_store: IndexStore, file_path: str, start_line: int, end_line: int
    ) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.func_scope: Scope | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        index = self._index_store.load(self.file_path)
        func_scope = (
            enclosing_function_scope(index.scopes, self.start_line, self.end_line)
            if index is not None
            else None
        )
        if func_scope is None:
            # Out-of-contract standalone use (file not indexed / no function);
            # in the planner this is caught earlier by RangeInsideFunction.
            return _fail("Range is not inside a function")
        module_scope_id = next(
            (s.scope_id for s in index.scopes if s.kind == ScopeKind.MODULE), None
        )
        if func_scope.parent_scope_id != module_scope_id:
            return _fail("extract-method v1 supports only top-level functions")
        self.func_scope = func_scope
        return _PASS


# ---------------------------------------------------------------------------
# Inline-variable
# ---------------------------------------------------------------------------


class LocalVariableResolves(Precondition):
    """The symbol id resolves uniquely to a function-local variable.

    Caches the resolved symbol as :attr:`symbol` on a successful evaluation.
    """

    name = "local-variable-resolves"

    def __init__(
        self, engine: SemanticQueryEngine, index_store: IndexStore, symbol_id: str
    ) -> None:
        self._engine = engine
        self._index_store = index_store
        self.symbol_id = symbol_id
        self.symbol: Symbol | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        results = self._engine.find_symbol(self.symbol_id)
        if not results:
            return _fail(f"Symbol not found: {self.symbol_id}")
        if len(results) > 1:
            return _fail(f"Ambiguous symbol '{self.symbol_id}'; use the full id")
        symbol = results[0]
        if symbol.kind != SymbolKind.VARIABLE:
            return _fail("inline-variable only applies to variables")
        index = self._index_store.load(symbol.location.file_path)
        scope_kind = {
            s.scope_id: s.kind for s in (index.scopes if index else [])
        }.get(symbol.parent_scope_id)
        if scope_kind != ScopeKind.FUNCTION:
            return _fail("inline-variable v1 supports only function-local variables")
        self.symbol = symbol
        return _PASS


class LoadedIndexFresh(Precondition):
    """The variable's file has a loadable, non-stale index (inline).

    Caches the loaded index as :attr:`index` on a successful evaluation.
    """

    name = "loaded-index-fresh"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.index: FileIndex | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        index = self._index_store.load(self.file_path)
        if index is None or self._index_store.is_stale(self.file_path):
            return _fail(f"File is stale or not indexed: {self.file_path}")
        self.index = index
        return _PASS


class NotReassigned(Precondition):
    """The variable has exactly one binding: no reassignment, shadowing or write.

    Takes the resolved symbol and its file index as constructor arguments
    (mid-plan values produced by :class:`LocalVariableResolves` and
    :class:`LoadedIndexFresh`).

    The write check is a pointwise verification of the same
    ``variable-mutation`` trait :func:`pypeeker.check.rules.prefer_tuple`
    quantifies over every candidate (see
    :mod:`pypeeker.analysis.variable_mutation`) — but only its
    ``has_write_ref`` fact, not the full ``is_mutated`` union: a
    ``.append()``/``.sort()``/... mutator call does not "reassign" a binding
    the way inlining means it (see that trait's docstring for the judgment
    call), so it must not fail this check the way it fails ``prefer_tuple``.
    """

    name = "not-reassigned"

    def __init__(self, symbol: Symbol, index: FileIndex) -> None:
        self.symbol = symbol
        self._index = index

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        # Shadowed/reassigned: a sibling symbol with the same name, a $N suffix,
        # or a WRITE reference (augmented/subscript/rebind) means more than one
        # binding — inlining is ambiguous.
        if "$" in self.symbol.symbol_id:
            return _fail("Variable is reassigned; cannot inline")
        for s in self._index.symbols:
            if (
                s.symbol_id != self.symbol.symbol_id
                and s.parent_scope_id == self.symbol.parent_scope_id
                and s.name == self.symbol.name
            ):
                return _fail("Variable is reassigned; cannot inline")
        mutation_trait = get_trait_provider(VARIABLE_MUTATION)
        assert mutation_trait is not None, (
            f"'{VARIABLE_MUTATION}' trait provider not registered — "
            "pypeeker.analysis.variable_mutation failed to import"
        )
        if mutation_trait(self._index, self.symbol.symbol_id).value.has_write_ref:
            return _fail("Variable is reassigned; cannot inline")
        return _PASS


class MultiUseValuePure(Precondition):
    """A value duplicated across multiple uses must be side-effect-free.

    Takes the number of READ references as a constructor argument (mid-plan
    value: the planner collects the reads it will rewrite).
    """

    name = "multi-use-value-pure"

    def __init__(
        self, index_store: IndexStore, file_path: str, def_line: int, use_count: int
    ) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.def_line = def_line
        self.use_count = use_count

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.use_count > 1:
            dataflow = analyze_range(
                self._index_store, self.file_path, self.def_line, self.def_line
            )
            if dataflow is None or not dataflow.is_pure:
                return _fail(
                    "Value has side effects and is used more than once; "
                    "inlining would change behavior"
                )
        return _PASS


class AssignmentLocatable(Precondition):
    """The variable's binding is a simple assignment with an inlinable value.

    Takes the parsed CST root and the resolved symbol as constructor
    arguments (mid-plan values) and caches the assignment's right-hand side
    as :attr:`rhs` on a successful evaluation.
    """

    name = "assignment-locatable"

    def __init__(self, root: Node, symbol: Symbol) -> None:
        self._root = root
        self.symbol = symbol
        self.rhs: Node | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        target = cst.expression_at(
            self._root,
            self.symbol.location.span.start.line,
            self.symbol.location.span.start.column,
        )
        if target is None:
            return _fail("Could not locate the assignment")
        node = target
        while node is not None and node.type != "assignment":
            node = node.parent
        if node is None:
            return _fail("Variable is not a simple assignment")
        rhs = node.child_by_field_name("right")
        if rhs is None:
            return _fail("Assignment has no value to inline")
        self.rhs = rhs
        return _PASS


# ---------------------------------------------------------------------------
# Phase-4 remedy planners, shared (TASK-125)
#
# delete-symbol, remove-import, rewrite-star-import and tuplify all resolve
# their anchor project-wide by symbol id, then re-verify the target file's
# existence/freshness, before ever reading bytes; the preconditions below
# carry the legacy fix-protocol slugs those planners' ``check --fix`` report
# entries depend on (see the module docstring's TASK-125 note).
# ---------------------------------------------------------------------------


class SymbolMatchUnambiguous(Precondition):
    """At most one candidate resolves the anchor id (slug ``"ambiguous"``).

    ``matches`` is the caller's pre-filtered candidate list (e.g. a
    kind-filtered ``engine.find_symbol(symbol_id)`` result); ``noun`` and
    ``resolves_to`` fill in the ported fix-protocol wording exactly
    (``"{noun} '{symbol_id}' resolves to more than one {resolves_to}"``).
    """

    name = "symbol-match-unambiguous"
    slug: ClassVar[str] = "ambiguous"

    def __init__(
        self,
        symbol_id: str,
        matches: list[Symbol],
        noun: str,
        resolves_to: str = "symbol",
    ) -> None:
        self.symbol_id = symbol_id
        self.matches = matches
        self.noun = noun
        self.resolves_to = resolves_to

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.matches) > 1:
            return _fail(
                f"{self.noun} '{self.symbol_id}' resolves to more than one "
                f"{self.resolves_to}"
            )
        return _PASS


class SymbolMatchFound(Precondition):
    """At least one candidate resolves the anchor id (slug ``"text-mismatch"``).

    Shared, like :class:`SymbolMatchUnambiguous`, across every project-wide
    or single-index re-resolution the ported planners perform (the initial
    engine-wide lookup and the post-freshness-check re-lookup against the
    just-loaded :class:`~pypeeker.models.FileIndex` both reduce to "does a
    candidate list contain a match"). Caches the first match as
    :attr:`symbol` on success.
    """

    name = "symbol-match-found"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, symbol_id: str, matches: list[Symbol], noun: str) -> None:
        self.symbol_id = symbol_id
        self.matches = matches
        self.noun = noun
        self.symbol: Symbol | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.matches:
            return _fail(f"{self.noun} '{self.symbol_id}' is no longer in the index")
        self.symbol = self.matches[0]
        return _PASS


class AnchorFileExists(Precondition):
    """The anchored file still exists on disk (slug ``"file-missing"``).

    Ports the fix protocol's ``_current_state`` file-existence check
    verbatim in wording; this and :class:`AnchorIndexFresh` together are the
    TASK-125 replacement for the historic (now-deleted)
    ``text_anchor.current_state`` helper, split across two preconditions,
    one per legacy slug.
    """

    name = "anchor-file-exists"
    slug: ClassVar[str] = "file-missing"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self._index_store.file_exists(self.file_path):
            return _fail(f"{self.file_path} no longer exists")
        return _PASS


class AnchorIndexFresh(Precondition):
    """The anchored file has a loadable, hash-matching index entry (slug ``"stale-index"``).

    Ports the fix protocol's ``_current_state`` freshness check verbatim in
    wording (see :class:`AnchorFileExists`). Caches the file bytes and
    loaded index as :attr:`content`/:attr:`index` on success — every planner
    that reaches this precondition immediately needs both to re-locate its
    target in the current text.
    """

    name = "anchor-index-fresh"
    slug: ClassVar[str] = "stale-index"

    def __init__(self, index_store: IndexStore, file_path: str) -> None:
        self._index_store = index_store
        self.file_path = file_path
        self.content: bytes = b""
        self.index: FileIndex | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        content = self._index_store.read_file(self.file_path)
        index = self._index_store.load(self.file_path)
        if index is None:
            return _fail(f"{self.file_path} is not indexed")
        if index.file_hash != self._index_store.file_hash(self.file_path):
            return _fail(
                f"{self.file_path} changed since it was indexed; re-index and re-plan"
            )
        self.content = content
        self.index = index
        return _PASS


class AnchorTextMatches(Precondition):
    """The expected token still sits at its recorded byte offset (slug ``"text-mismatch"``).

    ``offset`` is ``None`` when the caller's own offset lookup (e.g.
    :func:`~pypeeker.refactor.text_anchor.position_to_byte_offset`) already
    missed — treated the same as a byte mismatch, one decline.
    """

    name = "anchor-text-matches"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(
        self, content: bytes, offset: int | None, expected: str, label: str = "name"
    ) -> None:
        self.content = content
        self.offset = offset
        self.expected = expected
        self.label = label

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        expected_bytes = self.expected.encode("utf-8")
        if (
            self.offset is None
            or self.content[self.offset : self.offset + len(expected_bytes)] != expected_bytes
        ):
            return _fail(f"{self.label} '{self.expected}' not found at its indexed location")
        return _PASS


# ---------------------------------------------------------------------------
# Delete-symbol (TASK-125)
# ---------------------------------------------------------------------------


class UndecoratedDefinition(Precondition):
    """The definition carries no decorators (slug ``"ambiguous"``).

    Decorator lines sit above the deleted scope span, so a decorated
    definition's deletion would silently strand its decorators.
    """

    name = "undecorated-definition"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, symbol: Symbol) -> None:
        self.symbol = symbol

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.symbol.decorators:
            return _fail(
                f"'{self.symbol.name}' is decorated; decorator lines sit above "
                "the scope span and are not deleted"
            )
        return _PASS


class DeletableScope(Precondition):
    """The definition's scope is recorded, in range, and its header still matches (slug ``"text-mismatch"``).

    Combines the delete-symbol port's three scope-location checks — a
    recorded scope entry, a span that fits inside the current file, and a
    definition header (``def``/``class`` line) that still reads as expected
    — since all three decline under the same legacy slug. Caches the located
    :attr:`scope` and :attr:`line_starts` on success, for
    :class:`ScopeSpanClean` and the planner's own edit.
    """

    name = "deletable-scope"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, index: FileIndex, content: bytes, symbol: Symbol) -> None:
        self._index = index
        self.content = content
        self.symbol = symbol
        self.scope: Scope | None = None
        self.line_starts: list[int] = []
        self.start: int = 0

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        symbol_id = self.symbol.symbol_id
        scope = next((sc for sc in self._index.scopes if sc.scope_id == symbol_id), None)
        if scope is None:
            return _fail(f"no scope recorded for '{symbol_id}'")
        line_starts = line_start_offsets(self.content)
        if scope.span.end.line >= len(line_starts):
            return _fail("indexed scope span is out of range")
        start = line_starts[scope.span.start.line]
        header = self.content[
            start : line_end(line_starts, self.content, scope.span.start.line)
        ]
        if not is_definition_header(header, self.symbol.kind.value, self.symbol.name):
            return _fail(
                f"expected a '{self.symbol.kind.value} {self.symbol.name}' header "
                "at the indexed definition line"
            )
        self.scope = scope
        self.line_starts = line_starts
        self.start = start
        return _PASS


class ScopeSpanClean(Precondition):
    """The scope's last line holds nothing but whitespace/comment past its span (slug ``"ambiguous"``).

    Anything else on that line would be swept into the deletion.
    """

    name = "scope-span-clean"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, content: bytes, line_starts: list[int], scope: Scope) -> None:
        self.content = content
        self.line_starts = line_starts
        self.scope = scope

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        end_line = self.scope.span.end.line
        span_end = self.line_starts[end_line] + self.scope.span.end.column
        tail = self.content[span_end : line_end(self.line_starts, self.content, end_line)]
        if tail.strip() and not tail.lstrip().startswith(b"#"):
            return _fail("the definition's last line carries trailing code")
        return _PASS


# ---------------------------------------------------------------------------
# Remove-import / rewrite-star-import (TASK-125)
# ---------------------------------------------------------------------------


class ImportLineInRange(Precondition):
    """The import symbol's recorded line still exists in the current file (slug ``"text-mismatch"``).

    Caches the file's line-start offsets and the physical line's byte
    span/content (:attr:`line_starts`, :attr:`line_start`, :attr:`line_stop`,
    :attr:`line`, :attr:`body`) for the checks that follow and the planner's
    own edit.
    """

    name = "import-line-in-range"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, line_no: int) -> None:
        self.content = content
        self.line_no = line_no
        self.line_starts: list[int] = []
        self.line_start: int = 0
        self.line_stop: int = 0
        self.line: bytes = b""
        self.body: bytes = b""

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        line_starts = line_start_offsets(self.content)
        if self.line_no >= len(line_starts):
            return _fail("indexed import line is out of range")
        self.line_starts = line_starts
        self.line_start = line_starts[self.line_no]
        self.line_stop = (
            line_starts[self.line_no + 1]
            if self.line_no + 1 < len(line_starts)
            else len(self.content)
        )
        self.line = self.content[self.line_start : self.line_stop]
        self.body = self.line.rstrip(b"\r\n")
        return _PASS


class ImportStatementLine(Precondition):
    """The recorded line still reads as an ``import``/``from`` statement (slug ``"text-mismatch"``)."""

    name = "import-statement-line"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, body: bytes) -> None:
        self.body = body

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        stripped = self.body.lstrip()
        if not (stripped.startswith(b"import ") or stripped.startswith(b"from ")):
            return _fail("indexed line is not an import statement")
        return _PASS


class ImportLineSurgerySafe(Precondition):
    """The import line has no parens or line-continuation (slug ``"ambiguous"``).

    Byte-level surgery on a parenthesized or backslash-continued import list
    is too fragile to attempt safely.
    """

    name = "import-line-surgery-safe"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, body: bytes) -> None:
        self.body = body

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if b"(" in self.body or self.body.rstrip().endswith(b"\\"):
            return _fail("parenthesized or continued import lists are not edited")
        return _PASS


class ImportSegmentsLocatable(Precondition):
    """The import line's comma-separated name segments could be parsed (slug ``"text-mismatch"``).

    ``segments`` is the caller's own
    ``imports_ops._import_name_segments(body, is_from_import)`` result — kept
    out of this module to avoid a reverse import, the same pattern
    :class:`ScannableLiteral` uses for tuplify's bracket scanner.
    """

    name = "import-segments-locatable"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, segments: list | None) -> None:
        self.segments = segments

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.segments is None:
            return _fail("could not locate the imported-names part of the line")
        return _PASS


class ImportNameUnambiguousOnLine(Precondition):
    """Exactly one comma-separated entry on the line binds the target name (slug ``"ambiguous"``)."""

    name = "import-name-unambiguous-on-line"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, name: str, index_matches: list[int]) -> None:
        self.import_name = name
        self.index_matches = index_matches

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.index_matches) != 1:
            return _fail(
                f"'{self.import_name}' does not match exactly one name on the import line"
            )
        return _PASS


class SingleStarImportInFile(Precondition):
    """At most one star import remains in the file (slug ``"ambiguous"``).

    First-star-wins attribution (see
    :mod:`pypeeker.refactor.imports_ops`'s module docstring) is heuristic
    once a second star import appears.
    """

    name = "single-star-import-in-file"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, file_path: str, stars: list[Symbol]) -> None:
        self.file_path = file_path
        self.stars = stars

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.stars) > 1:
            return _fail(
                f"{self.file_path} now has {len(self.stars)} star imports; "
                "first-star-wins attribution is heuristic there"
            )
        return _PASS


class StarTargetModuleIndexed(Precondition):
    """The star import's target module is indexed, so its supply is derivable (slug ``"ambiguous"``)."""

    name = "star-target-module-indexed"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, imported_from: str, modules: Mapping[str, FileIndex]) -> None:
        self.imported_from = imported_from
        self.modules = modules

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.imported_from not in self.modules:
            return _fail(
                f"target module '{self.imported_from}' is not indexed; the "
                "names the star supplies cannot be derived"
            )
        return _PASS


class StarAttributionUnambiguous(Precondition):
    """Every unresolved bare name attributes to a star-imported module's surface (slug ``"ambiguous"``)."""

    name = "star-attribution-unambiguous"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, unattributed: list[str]) -> None:
        self.unattributed = unattributed

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.unattributed:
            return _fail(
                "unresolved name(s) "
                + ", ".join(f"'{n}'" for n in self.unattributed)
                + " match no star-imported module's public surface — the star "
                "import may still supply them (e.g. via a transitive star "
                "import), so the rewrite cannot be proven complete"
            )
        return _PASS


class StarSupplyNonEmpty(Precondition):
    """The star import actually supplies at least one used name (slug ``"ambiguous"``)."""

    name = "star-supply-nonempty"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, imported_from: str, names: list[str]) -> None:
        self.imported_from = imported_from
        self.names = names

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.names:
            return _fail(
                f"no names from '{self.imported_from}' are used; delete the "
                "star import instead of rewriting it"
            )
        return _PASS


class StarTokenMatches(Precondition):
    """The ``*`` token still sits at its indexed byte location (slug ``"text-mismatch"``)."""

    name = "star-token-matches"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, offset: int | None) -> None:
        self.content = content
        self.offset = offset

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.offset is None or self.content[self.offset : self.offset + 1] != b"*":
            return _fail("the '*' token is not at its indexed location")
        return _PASS


class StarLinePlainForm(Precondition):
    """The star's line is a plain, single-line ``from <module> import *`` (slug ``"text-mismatch"``).

    ``prefix_re`` is the caller's own compiled pattern (``imports_ops.
    _STAR_LINE_PREFIX``), kept out of this module for the same reason
    :class:`ImportSegmentsLocatable` takes its scanner's result rather than
    the scanner itself.
    """

    name = "star-line-plain-form"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, offset: int, prefix_re: Pattern[bytes]) -> None:
        self.content = content
        self.offset = offset
        self._prefix_re = prefix_re

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs.

        Assumes :class:`StarTokenMatches` already verified ``offset`` points
        at the ``*`` byte (this precondition only runs after that one
        passes); it only re-derives the line start and checks the prefix.
        """
        line_start = self.content.rfind(b"\n", 0, self.offset) + 1
        if self._prefix_re.fullmatch(self.content[line_start : self.offset]) is None:
            return _fail(
                "the indexed line is not a plain 'from <module> import *' statement"
            )
        return _PASS


# ---------------------------------------------------------------------------
# Tuplify (TASK-125)
# ---------------------------------------------------------------------------


class InferredListBinding(Precondition):
    """The variable's type annotation still records an inferred list literal (slug ``"text-mismatch"``).

    Takes the re-resolved symbol and its freshly loaded file index as
    constructor arguments (mid-plan values, same shape and order as
    :class:`NotReassigned`).

    This is the pointwise verification of the ``type-annotation`` trait that
    :func:`pypeeker.check.rules.prefer_tuple` quantifies over every candidate
    in a file (see :mod:`pypeeker.analysis.type_annotation`) — the first pair
    where the ∀ side and the pointwise side guard the *same* remedy: the rule
    selects the symbol and attaches the ``TuplifyIntent``, and this
    precondition re-checks the fact on a reloaded index before any bytes are
    written. The derivation reads ``Symbol.type_annotation`` off the index it
    is handed and touches no files, so it is simulation-safe with no store
    routing.
    """

    name = "inferred-list-binding"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, name: str, symbol: Symbol, index: FileIndex) -> None:
        self.var_name = name
        self.symbol = symbol
        self._index = index

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        annotation_trait = get_trait_provider(TYPE_ANNOTATION)
        assert annotation_trait is not None, (
            f"'{TYPE_ANNOTATION}' trait provider not registered — "
            "pypeeker.analysis.type_annotation failed to import"
        )
        if not is_inferred_list(annotation_trait(self._index, self.symbol.symbol_id)):
            return _fail(f"'{self.var_name}' is no longer bound to an inferred list literal")
        return _PASS


class AssignmentBindsList(Precondition):
    """The variable's assignment still opens with a list literal (slug ``"text-mismatch"``).

    ``open_offset`` is the caller's own ``literals._expect_assignment_list``
    result, for the same reverse-import reason :class:`ImportSegmentsLocatable`
    takes ``segments`` rather than the parser.
    """

    name = "assignment-binds-list"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, name: str, open_offset: int | None) -> None:
        self.var_name = name
        self.open_offset = open_offset

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.open_offset is None:
            return _fail(f"assignment to '{self.var_name}' no longer binds a list literal")
        return _PASS


class ScannableLiteral(Precondition):
    """The list literal's brackets can be safely byte-scanned (slug ``"ambiguous"``).

    ``scan_result`` is the caller's own ``literals._match_list_literal``
    result: a ``(close_offset, top_level_commas, has_elements,
    is_comprehension)`` tuple on success, or the scanner's decline reason
    string — kept out of this module for the same reverse-import reason
    :class:`ImportSegmentsLocatable` takes its scanner's result.
    """

    name = "scannable-literal"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, scan_result: tuple[int, int, bool, bool] | str) -> None:
        self.scan_result = scan_result
        self.close_offset: int = 0
        self.top_level_commas: int = 0
        self.has_elements: bool = False
        self.is_comprehension: bool = False

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if isinstance(self.scan_result, str):
            return _fail(self.scan_result)
        (
            self.close_offset,
            self.top_level_commas,
            self.has_elements,
            self.is_comprehension,
        ) = self.scan_result
        return _PASS


# ---------------------------------------------------------------------------
# Replace-text (TASK-125)
# ---------------------------------------------------------------------------


class OccurrenceExists(Precondition):
    """The expected text occurs at least once in the current file (slug ``"text-mismatch"``)."""

    name = "occurrence-exists"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, needle: bytes, old_text: str, file_path: str) -> None:
        self.content = content
        self.needle = needle
        self.old_text = old_text
        self.file_path = file_path
        self.first: int = -1

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        first = self.content.find(self.needle)
        if first == -1:
            return _fail(f"expected text {self.old_text!r} not found in {self.file_path}")
        self.first = first
        return _PASS


class UniqueOccurrence(Precondition):
    """The expected text occurs exactly once, so re-anchoring is unambiguous (slug ``"ambiguous"``)."""

    name = "unique-occurrence"
    slug: ClassVar[str] = "ambiguous"

    def __init__(
        self, content: bytes, needle: bytes, first: int, old_text: str, file_path: str
    ) -> None:
        self.content = content
        self.needle = needle
        self.first = first
        self.old_text = old_text
        self.file_path = file_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.content.find(self.needle, self.first + 1) != -1:
            return _fail(
                f"expected text {self.old_text!r} occurs more than once in {self.file_path}"
            )
        return _PASS


# ---------------------------------------------------------------------------
# Rename-docstring-param (TASK-125)
#
# The sixth phase-4 remedy planner, converted after the other five: ported
# from the same superseded fix protocol (``_DocstringParamRenameFix``) as
# delete-symbol/remove-import/rewrite-star-import/tuplify/replace-text, and
# sharing their legacy slugs. See ``DocstringParamRenamePlanner`` in
# :mod:`pypeeker.refactor.docstring_ops`.
# ---------------------------------------------------------------------------


class DocstringStillPresent(Precondition):
    """The function still has a recorded docstring (slug ``"text-mismatch"``)."""

    name = "docstring-still-present"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, symbol_id: str, symbol: Symbol) -> None:
        self.symbol_id = symbol_id
        self.symbol = symbol

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.symbol.docstring:
            return _fail(f"'{self.symbol_id}' no longer has a docstring")
        return _PASS


class ParamsSectionPresent(Precondition):
    """The docstring still has a params section in the recorded style (slug ``"text-mismatch"``).

    Caches the parsed section as :attr:`section` on success.
    """

    name = "params-section-present"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, docstring: str, style: str) -> None:
        self.docstring = docstring
        self.style = style
        self.section: ParamsSection | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        section = parse_documented_params(self.docstring, self.style)
        if section is None:
            return _fail(
                f"the docstring no longer has a {self.style}-style params section"
            )
        self.section = section
        return _PASS


class DocumentedParamDriftSingle(Precondition):
    """The docstring/signature drift is still exactly a single-parameter rename (slug ``"ambiguous"``).

    Caches the re-derived ``(ghosts, missing)`` — documented-but-absent and
    present-but-undocumented parameter names — as :attr:`ghosts` /
    :attr:`missing` on success.
    """

    name = "documented-param-drift-single"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, section: ParamsSection, index: FileIndex, symbol: Symbol) -> None:
        self.section = section
        self._index = index
        self.symbol = symbol
        self.ghosts: list[str] = []
        self.missing: list[str] = []

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        ghosts, missing = param_drift(self.section, signature_params(self._index, self.symbol))
        if len(ghosts) != 1 or len(missing) != 1:
            return _fail(
                "the drift is no longer a single-parameter rename "
                f"({len(ghosts)} stale documented name(s), "
                f"{len(missing)} undocumented parameter(s))"
            )
        self.ghosts = ghosts
        self.missing = missing
        return _PASS


class DocumentedParamDriftMatches(Precondition):
    """The re-derived drift is still the requested rename (slug ``"text-mismatch"``).

    Takes the resolved ``ghosts``/``missing`` as constructor arguments
    (mid-plan values produced by :class:`DocumentedParamDriftSingle`).
    """

    name = "documented-param-drift-matches"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(
        self, ghosts: list[str], missing: list[str], old_param: str, new_param: str
    ) -> None:
        self.ghosts = ghosts
        self.missing = missing
        self.old_param = old_param
        self.new_param = new_param

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.ghosts[0] != self.old_param or self.missing[0] != self.new_param:
            return _fail(
                f"the drift changed: '{self.ghosts[0]}' -> '{self.missing[0]}' "
                f"rather than '{self.old_param}' -> '{self.new_param}'"
            )
        return _PASS


class DocstringScopeLocated(Precondition):
    """The function's scope span is recorded and fits inside the current file (slug ``"text-mismatch"``).

    Combines the docstring-rename port's two scope-location checks — a
    recorded scope entry and a span end line that fits inside the current
    file — since both decline under the same legacy slug, mirroring
    :class:`DeletableScope`. Caches the located :attr:`scope`,
    :attr:`line_starts`, the byte offset the scope span starts at
    (:attr:`region_start`) and the scope's byte span (:attr:`region`) on
    success.
    """

    name = "docstring-scope-located"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, index: FileIndex, content: bytes, symbol_id: str) -> None:
        self._index = index
        self.content = content
        self.symbol_id = symbol_id
        self.scope: Scope | None = None
        self.line_starts: list[int] = []
        self.region_start: int = 0
        self.region: bytes = b""

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        scope = next(
            (sc for sc in self._index.scopes if sc.scope_id == self.symbol_id), None
        )
        if scope is None:
            return _fail(f"no scope recorded for '{self.symbol_id}'")
        line_starts = line_start_offsets(self.content)
        if scope.span.end.line >= len(line_starts):
            return _fail("indexed scope span is out of range")
        self.scope = scope
        self.line_starts = line_starts
        self.region_start = line_starts[scope.span.start.line]
        region_end = line_starts[scope.span.end.line] + scope.span.end.column
        self.region = self.content[self.region_start : region_end]
        return _PASS


class DocstringTextFound(Precondition):
    """The indexed docstring text occurs at least once inside the function body (slug ``"text-mismatch"``).

    Caches the byte offset of the first match, relative to ``region``, as
    :attr:`first` on success.
    """

    name = "docstring-text-found"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, region: bytes, doc_bytes: bytes) -> None:
        self.region = region
        self.doc_bytes = doc_bytes
        self.first: int = -1

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        first = self.region.find(self.doc_bytes)
        if first == -1:
            return _fail("the docstring text was not found inside the function body")
        self.first = first
        return _PASS


class DocstringTextUnique(Precondition):
    """The indexed docstring text occurs exactly once inside the function body (slug ``"ambiguous"``)."""

    name = "docstring-text-unique"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, region: bytes, doc_bytes: bytes, first: int) -> None:
        self.region = region
        self.doc_bytes = doc_bytes
        self.first = first

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.region.find(self.doc_bytes, self.first + 1) != -1:
            return _fail("the docstring text occurs more than once inside the function body")
        return _PASS


class DocstringTokenFound(Precondition):
    """The old parameter name occurs as a bare name token in the docstring (slug ``"text-mismatch"``).

    ``token`` is the caller's own compiled bare-token pattern (kept out of
    this module for the same reverse-import reason
    :class:`ImportSegmentsLocatable` takes its scanner's result rather than
    the scanner). Caches the regex matches as :attr:`matches` on success.
    """

    name = "docstring-token-found"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, doc_bytes: bytes, token: Pattern[bytes], old_param: str) -> None:
        self.doc_bytes = doc_bytes
        self._token = token
        self.old_param = old_param
        self.matches: list = []

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        matches = list(self._token.finditer(self.doc_bytes))
        if not matches:
            return _fail(
                f"'{self.old_param}' does not occur as a name token in the docstring"
            )
        self.matches = matches
        return _PASS


class DocstringTokenUnique(Precondition):
    """The old parameter name occurs exactly once as a bare name token (slug ``"ambiguous"``).

    Takes the resolved regex matches as a constructor argument (mid-plan
    value produced by :class:`DocstringTokenFound`).
    """

    name = "docstring-token-unique"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, matches: list, old_param: str) -> None:
        self.matches = matches
        self.old_param = old_param

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.matches) > 1:
            return _fail(
                f"'{self.old_param}' occurs {len(self.matches)} times in the "
                "docstring; rewriting one occurrence is unsafe"
            )
        return _PASS


# ---------------------------------------------------------------------------
# Move-symbol (TASK-131)
#
# Every precondition below leaves :attr:`Precondition.slug` at its default
# ``None``. ``move-symbol`` is a CLI operation, not a ``check --fix`` remedy,
# so it has no legacy refusal code to map onto: its refusals surface through
# the CLI's standard ``plan-refused`` envelope, named by
# :attr:`Precondition.name`. The frozen slug vocabulary is untouched.
#
# Where the move reuses a *shared* precondition that does carry a slug —
# ``UndecoratedDefinition``, ``DeletableScope``, ``ScopeSpanClean``,
# ``AnchorFileExists``, ``AnchorIndexFresh``, ``ImportLineSurgerySafe`` — the
# move planner deliberately does not read ``.slug`` (see
# :mod:`pypeeker.refactor.move`), so no slug reaches the CLI from a move.
# ---------------------------------------------------------------------------


class TopLevelDefinition(Precondition):
    """The target is a module-level FUNCTION or CLASS (move-symbol v1's scope).

    Mirrors ``DeleteSymbolPlanner``'s definition kinds and adds the
    module-level requirement: a method or a nested def carries an enclosing
    scope that cannot travel with it, and moving one would need the class or
    the closure to move too — a cascade, not a move.
    """

    name = "top-level-definition"

    _KINDS = (SymbolKind.FUNCTION, SymbolKind.CLASS)

    def __init__(self, symbol: Symbol) -> None:
        self.symbol = symbol

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.symbol.kind not in self._KINDS:
            return _fail(
                f"'{self.symbol.symbol_id}' is a {self.symbol.kind.value}; "
                "move-symbol moves module-level functions and classes only"
            )
        if self.symbol.parent_scope_id != module_of(self.symbol.symbol_id):
            return _fail(
                f"'{self.symbol.symbol_id}' is not defined at module level; "
                "move-symbol moves top-level definitions only"
            )
        return _PASS


class UnconditionalDefinition(Precondition):
    """The definition starts at column zero — it is not nested inside a block.

    :class:`TopLevelDefinition` asks ``parent_scope_id == module``, and that
    cannot tell a genuinely top-level ``def`` from one guarded by
    ``if TYPE_CHECKING:`` or ``try:``: neither statement opens a scope, so
    both record the module as their parent. Only the span distinguishes them,
    and something has to, because every downstream check reads the span as
    though it were column-zero text. The recorded span of a nested definition
    carries its indentation; ``tree-sitter`` parses an indented fragment
    *without error*, so :class:`MovedBodyClosed`'s "parses on its own" check
    waves it through; and the move then corrupts both files at once — the
    source is left with an ``if`` whose block is gone
    (``IndentationError: expected an indented block``) and the destination
    gains an over-indented one (``IndentationError: unexpected indent``).

    Moving a conditionally-defined function is not a span problem that could
    be fixed by dedenting the slice, either: the condition is what gives the
    definition its meaning, and reproducing it at the destination is a
    cascade, not a move. So this refuses by name.
    """

    name = "unconditional-definition"

    def __init__(self, symbol: Symbol, scope: Scope) -> None:
        self.symbol = symbol
        self.scope = scope

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.scope.span.start.column != 0:
            return _fail(
                f"'{self.symbol.name}' is defined inside a nested block (its "
                f"definition starts at column {self.scope.span.start.column + 1}, "
                "not at the left margin); move-symbol moves unconditional "
                "module-level definitions only"
            )
        return _PASS


class ValidModulePath(Precondition):
    """The destination is a dotted path of Python identifiers."""

    name = "valid-module-path"

    def __init__(self, module_path: str) -> None:
        self.module_path = module_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        parts = self.module_path.split(".")
        if not self.module_path or not all(part.isidentifier() for part in parts):
            return _fail(f"Invalid module path: {self.module_path}")
        return _PASS


class MoveIsNotSelf(Precondition):
    """The destination module differs from the one the symbol already lives in."""

    name = "move-is-not-self"

    def __init__(self, symbol_id: str, source_module: str, dest_module: str) -> None:
        self.symbol_id = symbol_id
        self.source_module = source_module
        self.dest_module = dest_module

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.source_module == self.dest_module:
            return _fail(
                f"'{self.symbol_id}' already lives in '{self.dest_module}'"
            )
        return _PASS


class DestinationModuleResolvable(Precondition):
    """The destination module maps to a path that is either indexed or absent.

    Three outcomes, two of which pass: the path is indexed and fresh (the
    move *extends* an existing module — :attr:`index` caches it), or nothing
    is there at all (the move *creates* it). A path that exists on disk
    without a matching index entry refuses as stale, because the collision
    and free-name checks that follow read the index, and planning a move
    against bytes nobody indexed would append a definition to a module whose
    contents are unknown.

    ``dest_path`` is the caller's
    :func:`~pypeeker.intents.module_file_path` result — kept out of this
    module for the same reason :class:`ImportSegmentsLocatable` takes its
    segments from the caller.

    Deliberately scoped to the leaf *file*. Whether the dotted path around
    that file is a legal module location — no ancestor shadowed by a
    ``.py``, no package directory already wearing the destination's name — is
    :class:`DestinationPathUnobstructed`'s question, and it runs next.
    """

    name = "destination-module-resolvable"

    def __init__(
        self, index_store: IndexStore, dest_module: str, dest_path: str | None
    ) -> None:
        self._index_store = index_store
        self.dest_module = dest_module
        self.dest_path = dest_path
        self.index: FileIndex | None = None
        self.exists = False

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.dest_path is None:
            return _fail(
                f"cannot determine where module '{self.dest_module}' would live: "
                "the index holds no module to derive the project's source layout from"
            )
        index = self._index_store.load(self.dest_path)
        on_disk = self._index_store.file_exists(self.dest_path)
        if index is None:
            if on_disk:
                return _fail(
                    f"'{self.dest_path}' exists but is not indexed; "
                    "run 'pypeeker index' first"
                )
            return _PASS
        if not on_disk:
            return _fail(
                f"'{self.dest_path}' is indexed but no longer exists on disk; "
                "re-index and re-plan"
            )
        if index.file_hash != self._index_store.file_hash(self.dest_path):
            return _fail(
                f"'{self.dest_path}' changed since it was indexed; re-index and re-plan"
            )
        self.index = index
        self.exists = True
        return _PASS


class DestinationPathUnobstructed(Precondition):
    """The destination's dotted path is a location Python could actually import.

    :class:`DestinationModuleResolvable` asks three questions about the
    destination's *leaf file* — indexed? on disk? hash-fresh? — and passes on
    "nothing is there at all". That is the file-birth half of the lifecycle
    chain's only judgement point, and on its own it is not enough: the leaf
    can be free while the dotted path around it is not, and every later guard
    (the applier's pre-flight absence check, the flatten authorization rule,
    rollback) then faithfully materialises a module Python cannot import.
    Two shapes do it, both reproduced end to end:

    * **an ancestor segment is a module, not a package.** ``pkg.lib`` is the
      file ``pkg/lib.py``, so ``pkg.lib.sub`` has no importable location.
      Creating ``pkg/lib/sub.py`` beside ``pkg/lib.py`` and rewriting every
      importer to ``from pkg.lib.sub import ...`` yields
      ``ModuleNotFoundError: ... 'pkg.lib' is not a package``.
    * **the destination's own name is already a package directory.** A
      regular module beats a namespace portion in Python's finder, so a
      newborn ``ns.py`` next to an existing ``ns/`` shadows the whole
      package and breaks modules the move never touched.

    Both are read through the store surface (``file_exists`` plus the indexed
    file list), never the filesystem, so the check means the same thing under
    :class:`~pypeeker.storage.overlay.OverlayIndexStore` as on disk. A
    destination whose recorded path does not end in its own dotted form (an
    index bound with an explicit module override) is not reasoned about and
    passes — there is no layout to walk.
    """

    name = "destination-path-unobstructed"

    def __init__(
        self, index_store: IndexStore, dest_module: str, dest_path: str | None
    ) -> None:
        self._index_store = index_store
        self.dest_module = dest_module
        self.dest_path = dest_path

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.dest_path is None:
            # DestinationModuleResolvable owns that refusal and runs first.
            return _PASS
        normalized = self.dest_path.replace("\\", "/")
        stem = self.dest_module.replace(".", "/")
        if normalized.endswith(f"{stem}.py"):
            prefix = normalized[: -len(f"{stem}.py")]
            leaf_is_package = False
        elif normalized.endswith(f"{stem}/__init__.py"):
            prefix = normalized[: -len(f"{stem}/__init__.py")]
            leaf_is_package = True
        else:
            return _PASS

        parts = self.dest_module.split(".")
        for depth in range(1, len(parts)):
            ancestor = prefix + "/".join(parts[:depth])
            if self._index_store.file_exists(f"{ancestor}.py"):
                return _fail(
                    f"'{'.'.join(parts[:depth])}' is a module ('{ancestor}.py'), not a "
                    f"package, so '{self.dest_module}' names no importable location; "
                    f"creating '{self.dest_path}' beside it would leave every "
                    "rewritten import unresolvable"
                )
        if leaf_is_package:
            return _PASS
        directory = prefix + stem
        occupied = self._index_store.file_exists(f"{directory}/__init__.py") or any(
            path.replace("\\", "/").startswith(f"{directory}/")
            for path in self._index_store.list_indexed_files()
        )
        if occupied:
            return _fail(
                f"'{self.dest_module}' is already a package directory "
                f"('{directory}/'); a module file wins over a package in Python's "
                f"finder, so creating '{self.dest_path}' would shadow it and make "
                "the modules inside it unimportable"
            )
        return _PASS


class NoDestinationNameCollision(Precondition):
    """The destination module binds no module-level name matching the target.

    ``IMPORT`` bindings count: a destination that already does
    ``from old import X`` binds ``X``, and appending a definition of ``X``
    below it shadows the import (or is shadowed by it, depending on order) —
    silently, since both are module-level names. A destination carrying a
    star import refuses outright: what a star supplies is not enumerable from
    the destination's own index, so a collision cannot be ruled out.

    ``importer_files`` closes the aliased variant of the same collision: a
    destination doing ``from old import X as Y`` binds ``Y``, not ``X``, so
    the name check above misses it — and rewriting that import in place
    (which is what the importer half of the move would otherwise do) would
    leave the destination importing the symbol *from itself*.
    """

    name = "no-destination-name-collision"

    def __init__(
        self,
        dest_module: str,
        dest_path: str,
        dest_index: FileIndex | None,
        name: str,
        importer_files: Iterable[str] = (),
    ) -> None:
        self.dest_module = dest_module
        self.dest_path = dest_path
        self.dest_index = dest_index
        self.symbol_name = name
        self.importer_files = set(importer_files)

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.dest_path in self.importer_files:
            return _fail(
                f"'{self.dest_module}' already imports '{self.symbol_name}'; "
                "moving the definition there would leave the module importing "
                "itself"
            )
        if self.dest_index is None:
            return _PASS
        module_id = next(
            (s.symbol_id for s in self.dest_index.symbols if s.kind is SymbolKind.MODULE),
            None,
        )
        top_level = [
            s for s in self.dest_index.symbols if s.parent_scope_id == module_id
        ]
        for symbol in top_level:
            if symbol.name == self.symbol_name:
                return _fail(
                    f"'{self.symbol_name}' is already bound at module level in "
                    f"'{self.dest_module}' as {symbol.symbol_id}"
                )
        for symbol in top_level:
            if symbol.kind is SymbolKind.IMPORT and symbol.name == "*":
                return _fail(
                    f"'{self.dest_path}' contains a star import, so the names it "
                    "already binds cannot be enumerated; move-symbol refuses rather "
                    f"than risk shadowing one with '{self.symbol_name}'"
                )
        return _PASS


class MoveQualifiedUseUnsupported(Precondition):
    """No consumer reaches the target through ``import m`` + ``m.name``.

    move-symbol rewrites ``from <module> import <name>`` statements — the
    edges :class:`~pypeeker.intents.anchors.EdgeAnchor` names. The qualified
    form binds the *module*, not the symbol, so repairing it means rewriting
    every call site's receiver rather than one import line. Half-rewriting it
    (moving the definition and leaving ``m.name`` pointing at the old module)
    produces code that imports cleanly and fails at the call, so v1 refuses
    by name instead.
    """

    name = "move-qualified-use-unsupported"

    def __init__(self, symbol_name: str, qualified: list) -> None:
        self.symbol_name = symbol_name
        self.qualified = qualified

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.qualified:
            return _PASS
        first = self.qualified[0]
        return _fail(
            f"'{self.symbol_name}' is used through a qualified module reference at "
            f"{first.location.file_path}:{first.location.span.start.line + 1}; "
            "move-symbol rewrites 'from <module> import <name>' statements only, so "
            "the 'import <module>' + '<module>.<name>' form is refused rather than "
            "half-rewritten"
        )


class SourceStarImportOpaque(Precondition):
    """No module reaches the source module's surface through ``from <src> import *``.

    The mirror of :class:`NoDestinationNameCollision`'s star clause, on the
    side that actually loses a name. A star import binds the local name
    ``*`` (``binder/imports.py``), so ``find_importers`` produces no edge for
    it and the planner's "every collected edge either produces an edit or
    refuses by name" contract holds *vacuously* — the edge is never
    collected. What reaches the user is a module that still spells
    ``helper()`` with nothing supplying ``helper``: a ``NameError`` at run
    time, and an unresolved reference the project's own ``check`` reports.

    A package ``__init__`` doing ``from .mod import *`` is the same failure
    one level out — the barrel's public surface silently loses the name, so
    every ``from pkg import helper`` consumer breaks too.

    Refusing is the only honest answer available. Rewriting is impossible for
    the same reason the destination-side clause gives: what a star supplies
    is not enumerable, so there is no entry to split out of the line and no
    way to tell whether this particular star is even what bound the name.

    ``star_files`` is the caller's scan of the store (the same
    caller-computes / precondition-judges split
    :class:`ImportSegmentsLocatable` uses), sorted so the named file is
    deterministic.
    """

    name = "source-star-import-opaque"

    def __init__(
        self, source_module: str, symbol_name: str, star_files: list[str]
    ) -> None:
        self.source_module = source_module
        self.symbol_name = symbol_name
        self.star_files = star_files

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.star_files:
            return _PASS
        return _fail(
            f"'{self.star_files[0]}' does 'from {self.source_module} import *', so "
            f"whether it binds '{self.symbol_name}' is not enumerable and the import "
            "cannot be rewritten to the destination; move-symbol refuses rather than "
            "leave that name unbound"
        )


def _expression_root_identifiers(source: bytes) -> list[str]:
    """Root identifiers of every dotted chain in ``source``, parsed as an expression.

    ``Base`` from ``Base``, ``pkg`` from ``pkg.Base``, ``Other`` from
    ``list[Other]``. The attribute half of an attribute access and a keyword
    argument's name bind nothing, so both are skipped. Unparseable input
    yields nothing: a string in a type position that is not an expression
    (``Literal["some text"]``) names no free variable, and guessing at one
    would produce refusals that have nothing to do with the move.
    """
    root = cst.parse(source)
    if root.has_error:
        return []
    names: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "identifier":
            continue
        parent = node.parent
        if parent is not None:
            if (
                parent.type == "attribute"
                and parent.child_by_field_name("attribute") == node
            ):
                continue
            if (
                parent.type == "keyword_argument"
                and parent.child_by_field_name("name") == node
            ):
                continue
        names.append(node.text.decode("utf-8"))
    return names


def _string_annotation_names(root: Node) -> list[str]:
    """Every name spelled inside a quoted type annotation under ``root``, de-duplicated.

    Walks to each ``type`` node (a parameter annotation, a return
    annotation, an annotated assignment) and reads the identifiers out of any
    string literal inside it — including nested ones, so ``list["Base"]`` and
    ``"list[Base]"`` are both seen. Order is first-appearance so a refusal
    names the same free name every run.
    """
    names: list[str] = []
    seen: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type != "type":
            continue
        strings = [node]
        while strings:
            current = strings.pop()
            if current.type != "string":
                strings.extend(current.children)
                continue
            content = b"".join(
                child.text or b""
                for child in current.children
                if child.type == "string_content"
            )
            for name in _expression_root_identifiers(content):
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


class MovedBodyClosed(Precondition):
    """The moved definition parses on its own and its free names travel with it.

    Two checks, one question — *is this definition self-contained enough to
    land somewhere else?*

    1. The definition's own text must parse cleanly in isolation. A span the
       index says is a definition but whose bytes do not close (an unbalanced
       bracket the binder recovered from, a scope span that no longer matches
       the file) would be appended to the destination as a syntax error.
    2. Every name the body uses that binds to a **module-level symbol of the
       source module** must be an ``IMPORT``: those bindings are copied to
       the destination (cached on :attr:`imports`). A free name binding to a
       non-import source-module symbol — a sibling function, a module
       constant — would be left behind, so the move refuses. The alternative,
       synthesizing a back-import into the source module, is a semantic
       cascade this project's "precise, not clever" principle rules out; it
       is a named follow-up, not v1.

    Membership is decided by *line span*, not by ``Reference.in_scope_id``: a
    return annotation is recorded in the enclosing module's scope even though
    its bytes sit on the ``def`` line, so a scope-keyed test would miss
    exactly the free names most likely to break at the destination.

    **Quoted annotations are read as names, not as strings.** A recorded
    ``Reference`` is what check 2 quantifies over, and a *string* annotation
    (``def f(x: "Base") -> "Thing"``, whether hand-written or the shape
    ``from __future__ import annotations`` encourages) produces none — so
    without this, the guard's verdict would flip on quoting alone, accepting
    the very body it refuses one pair of quotes away. It is a real break, not
    a cosmetic one: ``typing.get_type_hints``, dataclasses, and pydantic all
    resolve those strings in the module the function ends up in, where the
    name is not defined. So the annotation strings are parsed and their names
    joined to the same free-name question — carried when they bind to an
    import, refused when they bind to anything else the source module keeps.
    """

    name = "moved-body-closed"

    def __init__(
        self,
        index: FileIndex,
        content: bytes,
        line_starts: list[int],
        symbol: Symbol,
        scope: Scope,
        source_module: str,
    ) -> None:
        self._index = index
        self.content = content
        self.line_starts = line_starts
        self.symbol = symbol
        self.scope = scope
        self.source_module = source_module
        self.imports: list[Symbol] = []
        self.definition_text = ""
        self.definition_start = 0
        self.definition_end = 0

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        start_line = self.scope.span.start.line
        end_line = self.scope.span.end.line
        start = self.line_starts[start_line]
        end = (
            self.line_starts[end_line + 1]
            if end_line + 1 < len(self.line_starts)
            else len(self.content)
        )
        text = self.content[start:end]
        parsed = cst.parse(text)
        if parsed.has_error:
            return _fail(
                f"the definition of '{self.symbol.name}' does not parse on its own; "
                "its recorded span is not a complete, self-contained definition"
            )

        module_level = {
            s.symbol_id: s
            for s in self._index.symbols
            if s.parent_scope_id == self.source_module
        }
        needed: dict[str, Symbol] = {}
        for ref in self._index.references:
            line = ref.location.span.start.line
            if line < start_line or line > end_line:
                continue
            if ref.kind is ReferenceKind.DEFINITION:
                continue
            bound = module_level.get(ref.symbol_id)
            if bound is None or bound.symbol_id == self.symbol.symbol_id:
                continue
            if bound.kind is SymbolKind.IMPORT:
                needed.setdefault(bound.symbol_id, bound)
                continue
            return _fail(
                f"'{self.symbol.name}' uses '{bound.name}' ({bound.symbol_id}), which "
                "stays in the source module; move-symbol carries import bindings to "
                "the destination but never leaves a definition referring to a name "
                "it can no longer see"
            )

        by_name: dict[str, Symbol] = {}
        for candidate in module_level.values():
            by_name.setdefault(candidate.name, candidate)
        for free in _string_annotation_names(parsed):
            bound = by_name.get(free)
            if bound is None or bound.symbol_id == self.symbol.symbol_id:
                continue
            if bound.kind is SymbolKind.IMPORT:
                needed.setdefault(bound.symbol_id, bound)
                continue
            return _fail(
                f"'{self.symbol.name}' names '{bound.name}' ({bound.symbol_id}) in a "
                "string annotation, and it stays in the source module; a quoted "
                "annotation is resolved where the definition lands, so the move "
                "would produce a definition whose annotation cannot be resolved"
            )

        self.definition_start = start
        self.definition_end = end
        self.definition_text = text.decode("utf-8")
        self.imports = [needed[key] for key in sorted(needed)]
        return _PASS


class DestinationImportsCompatible(Precondition):
    """The destination can host every import binding the moved body needs.

    An import the destination already binds identically (same local name,
    same ``imported_from``) is simply not re-added; anything left over is
    cached on :attr:`missing` for the planner to write. A destination that
    binds one of those names to something *else* refuses: appending a second
    binding of the name would shadow whichever came first, silently, and
    picking a fresh alias for the moved body is a rewrite of the body this
    refactoring does not do.

    A destination that does not exist yet (``dest_index is None``) needs
    every binding, which is exactly what a newborn module gets.
    """

    name = "destination-imports-compatible"

    def __init__(
        self, dest_module: str, dest_index: FileIndex | None, imports: list[Symbol]
    ) -> None:
        self.dest_module = dest_module
        self.dest_index = dest_index
        self.imports = imports
        self.missing: list[Symbol] = []

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.dest_index is None:
            self.missing = list(self.imports)
            return _PASS
        module_id = next(
            (s.symbol_id for s in self.dest_index.symbols if s.kind is SymbolKind.MODULE),
            None,
        )
        bound = {
            s.name: s
            for s in self.dest_index.symbols
            if s.parent_scope_id == module_id
        }
        missing: list[Symbol] = []
        for symbol in self.imports:
            existing = bound.get(symbol.name)
            if existing is None:
                missing.append(symbol)
                continue
            if (
                existing.kind is SymbolKind.IMPORT
                and existing.imported_from == symbol.imported_from
            ):
                continue
            return _fail(
                f"the moved definition needs '{symbol.name}' "
                f"(imported from '{symbol.imported_from}'), but '{self.dest_module}' "
                f"already binds that name as {existing.symbol_id}"
            )
        self.missing = missing
        return _PASS


class ImportBindingReproducible(Precondition):
    """Every import binding the move carries can be written as a statement.

    ``bindings`` pairs each needed IMPORT symbol with the statement text that
    would rebind it at the destination, or ``None`` when the caller could not
    reconstruct one (the same caller-computes / precondition-judges split
    :class:`ImportSegmentsLocatable` uses). Refusing on a ``None`` is what
    keeps a move from writing a destination module whose imports were
    *guessed*: the alternative is a plausible-looking import line that binds
    a different object.

    :attr:`statements` caches the de-duplicated, sorted statement list the
    planner writes into the destination.
    """

    name = "import-binding-reproducible"

    def __init__(self, bindings: list[tuple[Symbol, str | None]]) -> None:
        self.bindings = bindings
        self.statements: list[str] = []

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        for symbol, statement in self.bindings:
            if statement is None:
                return _fail(
                    f"the moved definition needs the import binding '{symbol.name}' "
                    f"(from '{symbol.imported_from}'), which cannot be re-expressed "
                    "as an import statement at the destination"
                )
        self.statements = sorted({statement for _, statement in self.bindings})
        return _PASS


class SourceModuleFree(Precondition):
    """Nothing else in the source module uses the definition being moved.

    The mirror of :class:`MovedBodyClosed`: that one refuses when the body
    would lose sight of a name, this one when the *module* would lose sight
    of the body. A same-module caller binds directly to the definition's id
    (there is no import symbol to rewrite), so after the move it would
    resolve to nothing. Repairing it means adding a back-import to the source
    module — the same cascade :class:`MovedBodyClosed` declines — so the
    honest v1 answer is a named refusal.
    """

    name = "source-module-free"

    def __init__(self, index: FileIndex, symbol: Symbol, scope: Scope) -> None:
        self._index = index
        self.symbol = symbol
        self.scope = scope

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        start_line = self.scope.span.start.line
        end_line = self.scope.span.end.line
        for ref in self._index.references:
            if ref.symbol_id != self.symbol.symbol_id:
                continue
            if ref.kind is ReferenceKind.DEFINITION:
                continue
            line = ref.location.span.start.line
            if start_line <= line <= end_line:
                continue
            return _fail(
                f"'{self.symbol.name}' is still used by its own module at "
                f"{ref.location.file_path}:{line + 1}; moving it would leave that "
                "use unresolved, and move-symbol does not add back-imports"
            )
        return _PASS


def _touches_all_opaquely(root: Node) -> bool:
    """True when ``__all__`` is reached through an attribute or a subscript.

    ``__all__.extend([...])``, ``__all__.append(...)``, ``__all__[0] = ...``
    — every shape that can change the export list without leaving a
    scannable right-hand side behind. Read-only uses of the same shapes
    (``__all__.index("x")``) are swept up with them: this answers "can the
    export list be read off the source?", and the honest answer for a module
    that manipulates ``__all__`` through method calls is no.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        stack.extend(current.children)
        if current.type != "identifier" or current.text != b"__all__":
            continue
        parent = current.parent
        if parent is None:
            continue
        if parent.type == "attribute" and parent.child_by_field_name("object") == current:
            return True
        if parent.type == "subscript" and parent.child_by_field_name("value") == current:
            return True
    return False


def _names_string(node: Node, needle: bytes) -> bool:
    """True when any string literal under ``node`` has exactly ``needle`` as content."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "string_content" and current.text == needle:
            return True
        stack.extend(current.children)
    return False


class SourceExportListClean(Precondition):
    """The source module's ``__all__`` does not name the symbol being moved.

    An ``__all__`` entry is a *string*, so it binds nothing and no reference
    points at it — which means :class:`SourceModuleFree`, whose whole basis is
    references, cannot see it. Left alone it becomes an export list naming a
    symbol the module no longer defines: ``from mod import *`` raises at
    import time and every static consumer of the public surface is wrong.

    Refusing rather than rewriting is the same call the rest of this
    refactoring makes. ``__all__`` is a deliberate public-API declaration;
    deciding whether the moved name should leave the source module's surface,
    join the destination's, or be re-exported from both is a judgement about
    the package's API, not a mechanical consequence of relocating a
    definition. The user edits ``__all__`` and re-runs.

    Two statement shapes count as a readable declaration — ``__all__ = ...``
    (annotated or not) and ``__all__ += ...`` — because both are ordinary
    module-level statements whose right-hand side can be scanned for the
    name. Anything else (``__all__.extend(...)``, ``__all__[0] = ...``, an
    ``__all__`` assembled in a loop, one assigned inside an ``if``) is
    *unreadable*, not absent: the module says it has an export list and this
    check cannot see what is in it, so it refuses too. Silence there would be
    the same accept-then-break the class exists to prevent, just one
    statement shape further out.
    """

    name = "source-export-list-clean"

    def __init__(
        self, content: bytes, index: FileIndex, symbol: Symbol, source_module: str
    ) -> None:
        self.content = content
        self._index = index
        self.symbol = symbol
        self.source_module = source_module

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        declared = any(
            s.name == "__all__" and s.parent_scope_id == self.source_module
            for s in self._index.symbols
        )
        if not declared:
            return _PASS
        needle = self.symbol.name.encode("utf-8")
        readable = 0
        root = cst.parse(self.content)
        for statement in root.children:
            for child in statement.children:
                if child.type not in ("assignment", "augmented_assignment"):
                    continue
                left = child.child_by_field_name("left")
                right = child.child_by_field_name("right")
                if left is None or right is None or left.text != b"__all__":
                    continue
                readable += 1
                if _names_string(right, needle):
                    return _fail(
                        f"'{self.source_module}' exports '{self.symbol.name}' through "
                        "__all__; moving the definition would leave the export list "
                        "naming a symbol the module no longer defines. Decide what the "
                        "module's public surface should be and edit __all__ first"
                    )
        if not readable or _touches_all_opaquely(root):
            return _fail(
                f"'{self.source_module}' declares __all__ in a form this refactoring "
                "cannot read (no module-level '__all__ = ...' or '__all__ += ...' "
                "statement, or __all__ reached through an attribute or a subscript), "
                f"so whether it exports '{self.symbol.name}' cannot be determined. "
                "move-symbol refuses rather than risk leaving the export list naming "
                "a symbol the module no longer defines"
            )
        return _PASS


class ImportEdgeRewritable(Precondition):
    """One import edge's statement can be rewritten by byte surgery.

    Named after the edge rather than the file: a move's importer half is a
    set of :class:`~pypeeker.intents.anchors.EdgeAnchor` relationships, and a
    refusal that says *which edge* is actionable in a way that "some importer
    file could not be rewritten" is not.

    The line must be a single-line ``from <module> import ...`` statement
    whose module part is locatable and on which exactly one comma-separated
    entry binds the imported name. Unlike rename's ``_build_edits``, which
    silently drops a location whose text does not match, **every collected
    edge must either produce an edit or refuse here** — a rename that skips
    an import leaves the import valid, while a move that skips one leaves it
    dangling.

    ``segments`` is the caller's
    ``imports_ops._import_name_segments`` result (kept out of this module to
    avoid a reverse import, the same pattern
    :class:`ImportSegmentsLocatable` uses). On success
    :attr:`module_start`/:attr:`module_end` are the module part's byte
    columns within ``body`` and :attr:`segment_index` the matching entry.
    """

    name = "import-edge-rewritable"

    def __init__(self, edge, body: bytes, bound_name: str, segments: list | None) -> None:
        self.edge = edge
        self.body = body
        self.bound_name = bound_name
        self.segments = segments
        self.module_start = 0
        self.module_end = 0
        self.segment_index = 0

    def _describe(self) -> str:
        """The edge, as it appears in every refusal message here."""
        return f"import edge '{self.edge.source_id}' -> '{self.edge.target_id}'"

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        stripped = self.body.lstrip()
        indent = len(self.body) - len(stripped)
        if not stripped.startswith(b"from "):
            return _fail(
                f"{self._describe()} is not a 'from <module> import <name>' statement"
            )
        marker = self.body.find(b" import ")
        module_start = indent + len(b"from ")
        if marker <= module_start:
            return _fail(
                f"{self._describe()}: could not locate the module part of the line"
            )
        if self.segments is None:
            return _fail(
                f"{self._describe()}: could not locate the imported-names part of the line"
            )
        expected = self.bound_name.encode("utf-8")
        matches = [
            i for i, segment in enumerate(self.segments) if segment.bound_name == expected
        ]
        if len(matches) != 1:
            return _fail(
                f"{self._describe()}: {len(matches)} entries on the line bind "
                f"'{self.bound_name}'; exactly one is required to rewrite it"
            )
        self.module_start = module_start
        self.module_end = marker
        self.segment_index = matches[0]
        return _PASS
