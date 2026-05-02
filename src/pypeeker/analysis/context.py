"""Per-function analysis context shared across fact extractors.

The context is built once per function under analysis and passed to every
fact extractor. This avoids re-scanning the index for each fact and gives
extractors a uniform view of "what's local to this function".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pypeeker.models.index import FileIndex
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.storage.store import IndexStore


@dataclass(frozen=True)
class AnalysisContext:
    """Everything fact extractors need to inspect a single function."""

    file_index: FileIndex
    function_symbol: Symbol
    function_scope_id: str
    subtree: frozenset[str]
    """All scope_ids inside the function (recursive)."""
    local_symbol_ids: frozenset[str]
    """Every symbol declared inside the function's scope subtree."""
    local_variable_ids: frozenset[str]
    """Subset of local_symbol_ids whose kind is VARIABLE (not parameters)."""
    reads_by_line: dict[int, frozenset[str]] = field(default_factory=dict)
    """READ symbol_ids grouped by line, scoped to this function."""
    local_type_names: dict[str, str] = field(default_factory=dict)
    """Bare type names (e.g. 'Path', 'list', 'IO') keyed by symbol_id, for
    every local symbol whose ``type_annotation.raw`` could be normalized.
    Empty when no annotations are present. Used by check-layer policy to
    apply type-specific denylists (TASK-14)."""

    @classmethod
    def for_function(
        cls, store: IndexStore, symbol_id: str
    ) -> "AnalysisContext | ContextError":
        """Build a context for the function identified by `symbol_id`.

        Returns ContextError on resolution failure so the caller can decide
        how to surface it (e.g. as PurityResult(verdict=UNKNOWN)).
        """
        engine = SemanticQueryEngine(store)
        target = _resolve_function(engine, symbol_id)
        if target is None:
            return ContextError(reason="not_found", symbol_id=symbol_id)
        if target.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            return ContextError(
                reason="not_a_function",
                symbol_id=target.symbol_id,
                detail=f"symbol kind is {target.kind.value}",
            )

        file_index = store.load(target.location.file_path)
        if file_index is None:
            return ContextError(
                reason="not_found",
                symbol_id=target.symbol_id,
                detail=f"file index missing for {target.location.file_path}",
            )

        func_scope_id = _function_scope_id(target, file_index)
        if func_scope_id is None:
            return ContextError(
                reason="not_a_function",
                symbol_id=target.symbol_id,
                detail="no function scope found for symbol",
            )

        subtree = _scope_subtree(file_index, func_scope_id)
        locals_ = frozenset(
            s.symbol_id for s in file_index.symbols if s.parent_scope_id in subtree
        )
        local_vars = frozenset(
            s.symbol_id
            for s in file_index.symbols
            if s.parent_scope_id in subtree and s.kind == SymbolKind.VARIABLE
        )

        reads: dict[int, set[str]] = {}
        for ref in file_index.references:
            if ref.in_scope_id in subtree and ref.kind == ReferenceKind.READ:
                reads.setdefault(ref.location.span.start.line, set()).add(
                    ref.symbol_id
                )

        types: dict[str, str] = {}
        for s in file_index.symbols:
            if s.parent_scope_id not in subtree:
                continue
            if s.type_annotation is None:
                continue
            bare = _bare_type_name(s.type_annotation.raw)
            if bare:
                types[s.symbol_id] = bare

        return cls(
            file_index=file_index,
            function_symbol=target,
            function_scope_id=func_scope_id,
            subtree=frozenset(subtree),
            local_symbol_ids=locals_,
            local_variable_ids=local_vars,
            reads_by_line={line: frozenset(ids) for line, ids in reads.items()},
            local_type_names=types,
        )


@dataclass(frozen=True)
class ContextError:
    """Returned by AnalysisContext.for_function when context cannot be built."""

    reason: str
    """One of: 'not_found', 'not_a_function'."""
    symbol_id: str
    detail: str | None = None


def _bare_type_name(annotation: str | None) -> str | None:
    """Normalize a raw type annotation to a single bare type name.

    Handles the common shapes seen in real code: ``Path``, ``pathlib.Path``,
    ``Path | None``, ``Optional[Path]``, ``Union[Path, str]``, ``list[int]``.
    Returns the leftmost concrete name, with module prefix and generic args
    stripped. None for empty / unparseable annotations.

    This is intentionally simple — full type resolution is out of scope.
    """
    if not annotation:
        return None
    s = annotation.strip()

    # Optional[X] -> X
    if s.startswith("Optional[") and s.endswith("]"):
        s = s[len("Optional["):-1].strip()
    # Union[A, B, ...] -> A (first arg)
    if s.startswith("Union[") and s.endswith("]"):
        inner = s[len("Union["):-1]
        s = inner.split(",", 1)[0].strip()
    # PEP 604 unions (A | B | None) -> A
    if "|" in s:
        s = s.split("|", 1)[0].strip()
    # Strip generic params: list[int] -> list, IO[str] -> IO
    if "[" in s:
        s = s[: s.index("[")].strip()
    # Strip module prefix: pathlib.Path -> Path
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s or None


def _resolve_function(engine: SemanticQueryEngine, symbol_id: str) -> Symbol | None:
    matches = engine.find_symbol(symbol_id)
    if not matches:
        return None
    for s in matches:
        if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            return s
    return matches[0]


def _function_scope_id(target: Symbol, file_index: FileIndex) -> str | None:
    for scope in file_index.scopes:
        if scope.scope_id == target.symbol_id and scope.kind in (
            ScopeKind.FUNCTION,
            ScopeKind.LAMBDA,
        ):
            return scope.scope_id
    return None


def _scope_subtree(file_index: FileIndex, root_scope_id: str) -> set[str]:
    """Return the scope subtree containing ``root`` and its inline children.

    For purity analysis, "the function's body" includes the function's own
    scope plus any scopes that *execute inline* during the function call —
    comprehensions and the bodies of lambdas/inner functions run only when
    called, so we explicitly stop at FUNCTION / LAMBDA boundaries even
    though they appear as child_scope_ids structurally.

    Comprehension scopes are included because their body executes immediately
    when control reaches them.
    """
    scope_map = {s.scope_id: s for s in file_index.scopes}
    result: set[str] = set()
    stack = [root_scope_id]
    is_root = True
    while stack:
        scope_id = stack.pop()
        if scope_id in result:
            continue
        scope = scope_map.get(scope_id)
        if scope is None:
            continue
        # Stop at nested function/lambda boundaries (their bodies don't
        # execute as part of the enclosing function call). Always include
        # the root, even if it's a function/lambda.
        if not is_root and scope.kind in (ScopeKind.FUNCTION, ScopeKind.LAMBDA):
            continue
        result.add(scope_id)
        is_root = False
        stack.extend(scope.child_scope_ids)
    return result
