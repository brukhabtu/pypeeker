"""Delete-symbol preconditions (:mod:`pypeeker.refactor.delete`, TASK-125)."""

from __future__ import annotations

from typing import ClassVar

from pypeeker.models import (
    FileIndex,
    Scope,
    Symbol,
)
from pypeeker.refactor.text_anchor import is_definition_header, line_end, line_start_offsets
from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)


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
