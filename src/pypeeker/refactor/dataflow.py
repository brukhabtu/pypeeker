"""Data-flow analysis for a statement range, for structural refactors.

Answers the questions extract-method (and friends) need about a line range
inside a function: which names it depends on (inputs → parameters), which names
it produces that are used afterward (outputs → return values), whether control
flow escapes the range, and whether it is side-effect-free. Built on the
semantic layer (:class:`AnalysisContext`, purity observations) for data flow and
on the CST for control-flow shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.purity import observations
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import Scope, ScopeKind
from pypeeker.refactor import cst
from pypeeker.storage import IndexStore

_ESCAPE_NODE_TYPES = frozenset(
    {"return_statement", "break_statement", "continue_statement"}
)


@dataclass(frozen=True)
class RangeDataFlow:
    """Data-flow summary of a statement range within a function."""

    inputs: tuple[str, ...]
    """Local symbol_ids read in the range but defined outside it (parameters)."""
    outputs: tuple[str, ...]
    """Local symbol_ids written in the range and read after it (return values)."""
    has_escape: bool
    """True if a return/break/continue appears in the range."""
    is_pure: bool
    """True if the range has no impure observations (side-effect-free)."""


def analyze_range(
    index_store: IndexStore, file_path: str, start_line: int, end_line: int
) -> RangeDataFlow | None:
    """Summarize the data flow of lines ``[start_line, end_line]`` (0-indexed).

    Returns ``None`` if the file isn't indexed or the range isn't inside a
    function.
    """
    index = index_store.load(file_path)
    if index is None:
        return None
    func_scope = _enclosing_function_scope(index.scopes, start_line, end_line)
    if func_scope is None:
        return None
    ctx = AnalysisContext.for_function(index_store, func_scope.scope_id)
    if isinstance(ctx, ContextError):
        return None

    def _in_range(line: int) -> bool:
        return start_line <= line <= end_line

    decl_line = {s.symbol_id: s.location.span.start.line for s in index.symbols}

    reads_in_range: set[str] = set()
    for line, ids in ctx.reads_by_line.items():
        if _in_range(line):
            reads_in_range |= set(ids) & ctx.local_symbol_ids
    inputs = sorted(
        s for s in reads_in_range if not _in_range(decl_line.get(s, -1))
    )

    # Names produced in the range: locals first declared in it (a plain
    # assignment is a declaration, not a WRITE ref) plus any re-assigned in it
    # (augmented/subscript/rebind WRITE refs).
    produced = {s for s in ctx.local_symbol_ids if _in_range(decl_line.get(s, -1))}
    for ref in index.references:
        if (
            ref.kind == ReferenceKind.WRITE
            and ref.symbol_id in ctx.local_symbol_ids
            and _in_range(ref.location.span.start.line)
        ):
            produced.add(ref.symbol_id)

    reads_after: set[str] = set()
    for line, ids in ctx.reads_by_line.items():
        if line > end_line:
            reads_after |= set(ids) & ctx.local_symbol_ids
    outputs = sorted(produced & reads_after)

    has_escape = _range_has_escape(index_store, file_path, start_line, end_line)
    is_pure = not any(_in_range(o.line) for o in observations(ctx))

    return RangeDataFlow(
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        has_escape=has_escape,
        is_pure=is_pure,
    )


def _enclosing_function_scope(
    scopes: list[Scope], start_line: int, end_line: int
) -> Scope | None:
    """Innermost FUNCTION scope whose span contains the range, or None."""
    best: Scope | None = None
    best_size = None
    for scope in scopes:
        if scope.kind != ScopeKind.FUNCTION:
            continue
        if scope.span.start.line <= start_line and end_line <= scope.span.end.line:
            size = scope.span.end.line - scope.span.start.line
            if best_size is None or size < best_size:
                best, best_size = scope, size
    return best


def _range_has_escape(
    index_store: IndexStore, file_path: str, start_line: int, end_line: int
) -> bool:
    """True if a return/break/continue node starts within the range."""
    source = (index_store.project_root / file_path).read_bytes()
    root = cst.parse(source)
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        line = node.start_point[0]
        if line > end_line:
            continue
        if node.type in _ESCAPE_NODE_TYPES and start_line <= line <= end_line:
            return True
        stack.extend(node.children)
    return False
