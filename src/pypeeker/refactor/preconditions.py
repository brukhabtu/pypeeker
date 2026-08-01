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
    Scope,
    ScopeKind,
    Symbol,
    SymbolKind,
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
