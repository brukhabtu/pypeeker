"""Extract refactorings: extract-variable and extract-method."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Iterator

from tree_sitter import Node

from pypeeker.intents import ExtractMethodIntent, ExtractVariableIntent
from pypeeker.models import (
    EditEntry,
    EditOp,
    Scope,
    TransactionSummary,
    leaf_name,
)
from pypeeker.refactor import cst
from pypeeker.refactor.plan_support import persist, simple_materializer
from pypeeker.refactor.preconditions import (
    ExpressionFound,
    FileExists,
    FileFresh,
    InsideStatement,
    NoControlFlowEscape,
    Precondition,
    RangeInsideFunction,
    SourceIsUtf8,
    TopLevelFunctionOnly,
    ValidIdentifier,
    evaluate_in_order,
)
from pypeeker.refactor.dataflow import RangeDataFlow
from pypeeker.refactor.registry import register_planner
from pypeeker.refactor.text_anchor import line_start_offsets
from pypeeker.storage import IndexStore, TransactionStore


class ExtractVariableError(Exception):
    """Raised when an extract-variable plan cannot be created.

    ``precondition`` (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition`.
    """

    def __init__(self, message: str, *, precondition: str | None = None) -> None:
        """Store the message alongside the name of the precondition that failed, if any."""
        super().__init__(message)
        self.precondition = precondition


@dataclass
class _ExtractVariableState:
    """Values computed while evaluating preconditions, reused to build edits."""

    source: bytes = b""
    file_hash: str = ""
    node: "Node | None" = None
    statement: "Node | None" = None


class ExtractVariablePlanner:
    """Plan an extract-variable refactor as a transaction."""

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store

    def plan(
        self,
        file_path: str,
        start: tuple[int, int],
        end: tuple[int, int],
        new_name: str,
    ) -> TransactionSummary:
        """Extract the expression spanning ``start``..``end`` into ``new_name``.

        ``start``/``end`` are 0-indexed ``(line, column)`` positions.
        """
        state = _ExtractVariableState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, file_path, start, end, new_name)
        )
        if failure is not None:
            raise ExtractVariableError(failure.reason, precondition=evaluated[-1].name)

        source = state.source
        file_hash = state.file_hash
        node = state.node
        statement = state.statement

        # The planner decodes exactly two spans below (the selection and the
        # statement's indent run), so the guard is scoped to those spans, not
        # to the whole file: extracting an ASCII expression out of a file that
        # holds undecodable bytes elsewhere works, and must keep working.
        # Same precondition, name and refusal wording as extract-method's
        # decode-site guard, so the envelope stays uniform.
        indent_start = cst.line_start_byte(statement)
        for span_start, span_end in (
            (node.start_byte, node.end_byte),
            (indent_start, statement.start_byte),
        ):
            decodable = SourceIsUtf8(
                source[span_start:span_end], file_path, byte_offset=span_start
            )
            decoded = decodable.evaluate()
            if not decoded.ok:
                raise ExtractVariableError(decoded.reason, precondition=decodable.name)

        expr_text = cst.node_text(node, source)
        indent = cst.indent_of(statement, source)
        insert_text = f"{indent}{new_name} = {expr_text}\n"

        edits = [
            cst.insert_edit(
                file_path, cst.line_start_byte(statement), insert_text, file_hash
            ),
            cst.replace_edit(file_path, node, new_name, file_hash, source),
        ]

        return persist(
            self._transaction_store, "extract_variable", "", expr_text, new_name, edits
        )

    def preconditions(
        self,
        file_path: str,
        start: tuple[int, int],
        end: tuple[int, int],
        new_name: str,
    ) -> list[Precondition]:
        """The ordered precondition set for this extraction, in enumerable form.

        Each precondition is evaluated as it is constructed (later ones are
        built from cached results of earlier ones, e.g. the inside-statement
        check needs the found expression node), so the returned objects
        reflect current state; if a precondition fails, the list ends at that
        precondition.
        """
        preconditions, _ = evaluate_in_order(
            self._iter_preconditions(
                _ExtractVariableState(), file_path, start, end, new_name
            )
        )
        return preconditions

    def _iter_preconditions(
        self,
        state: _ExtractVariableState,
        file_path: str,
        start: tuple[int, int],
        end: tuple[int, int],
        new_name: str,
    ) -> Iterator[Precondition]:
        """Yield this extraction's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`evaluate_in_order`); the source bytes/hash and the parsed
        nodes are stashed on ``state`` for :meth:`plan`.
        """
        yield ValidIdentifier(new_name)
        yield FileExists(self._index_store, file_path)
        yield FileFresh(self._index_store, file_path)

        state.source = self._index_store.read_file(file_path)
        state.file_hash = self._index_store.file_hash(file_path)
        root = cst.parse(state.source)

        expression = ExpressionFound(root, start, end, file_path)
        yield expression
        state.node = expression.node

        statement = InsideStatement(expression.node)
        yield statement
        state.statement = statement.statement


class ExtractMethodError(Exception):
    """Raised when an extract-method plan cannot be created.

    ``precondition`` (TASK-125, additive) names the failing
    :class:`~pypeeker.refactor.preconditions.Precondition`.
    """

    def __init__(self, message: str, *, precondition: str | None = None) -> None:
        """Store the message alongside the name of the precondition that failed, if any."""
        super().__init__(message)
        self.precondition = precondition


@dataclass
class _ExtractMethodState:
    """Values computed while evaluating preconditions, reused to build edits."""

    dataflow: "RangeDataFlow | None" = None
    func_scope: "Scope | None" = None


class ExtractMethodPlanner:
    """Plan extracting a statement range into a new top-level function.

    Despite the wire-facing kind (``"extract-method"``) and the name — both
    kept for compatibility — this is **extract-function**: the range must
    sit in a top-level function
    (:class:`~pypeeker.refactor.preconditions.TopLevelFunctionOnly`), and
    the extracted code becomes a new module-level ``def`` inserted above
    that function, called with the range's inputs as arguments. Extracting a
    range out of a method into a new method on the same class is not
    implemented.
    """

    def __init__(
        self, index_store: IndexStore, transaction_store: TransactionStore
    ) -> None:
        self._index_store = index_store
        self._transaction_store = transaction_store

    def plan(
        self, file_path: str, start_line: int, end_line: int, new_name: str
    ) -> TransactionSummary:
        """Extract lines ``[start_line, end_line]`` (0-indexed) into ``new_name``.

        v1 supports extracting from a top-level function and refuses ranges that
        contain control-flow escapes (return/break/continue).
        """
        state = _ExtractMethodState()
        evaluated, failure = evaluate_in_order(
            self._iter_preconditions(state, file_path, start_line, end_line, new_name)
        )
        if failure is not None:
            raise ExtractMethodError(failure.reason, precondition=evaluated[-1].name)

        rdf = state.dataflow
        func_scope = state.func_scope

        content = self._index_store.read_file(file_path)
        decodable = SourceIsUtf8(content, file_path)
        decoded = decodable.evaluate()
        if not decoded.ok:
            raise ExtractMethodError(decoded.reason, precondition=decodable.name)
        source = decodable.text
        file_hash = self._index_store.file_hash(file_path)
        lines = _physical_lines(source)

        params = [leaf_name(s) for s in rdf.inputs]
        returns = [leaf_name(s) for s in rdf.outputs]

        range_text = "".join(lines[start_line : end_line + 1])
        # The emitted function is top-level, so its body indent is one unit
        # from column 0. Four spaces is assumed rather than derived from the
        # source: deriving it would mean locating the enclosing function's
        # first body line past a possibly multi-line signature.
        body = textwrap.indent(textwrap.dedent(range_text), "    ")
        if not body.endswith("\n"):
            body += "\n"
        if returns:
            body += f"    return {', '.join(returns)}\n"
        new_func = f"def {new_name}({', '.join(params)}):\n{body}\n\n"

        call_indent = lines[start_line][: len(lines[start_line]) - len(lines[start_line].lstrip())]
        call_expr = f"{new_name}({', '.join(params)})"
        assignment = f"{', '.join(returns)} = " if returns else ""
        call_text = f"{call_indent}{assignment}{call_expr}\n"

        line_starts = line_start_offsets(content)
        range_start = line_starts[start_line]
        range_end = line_starts[end_line] + len(lines[end_line])
        func_start = line_starts[func_scope.span.start.line]

        edits = [
            EditEntry(
                file=file_path, start=func_start, end=func_start, old="",
                new=new_func, file_hash=file_hash, op=EditOp.INSERT,
            ),
            EditEntry(
                file=file_path, start=range_start, end=range_end,
                old=range_text, new=call_text, file_hash=file_hash,
                op=EditOp.REPLACE,
            ),
        ]

        return persist(
            self._transaction_store,
            "extract_method",
            func_scope.scope_id,
            "",
            new_name,
            edits,
        )

    def preconditions(
        self, file_path: str, start_line: int, end_line: int, new_name: str
    ) -> list[Precondition]:
        """The ordered precondition set for this extraction, in enumerable form.

        Each precondition is evaluated as it is constructed (later ones are
        built from cached results of earlier ones, e.g. the escape check needs
        the range dataflow), so the returned objects reflect current state; if
        a precondition fails, the list ends at that precondition.
        """
        preconditions, _ = evaluate_in_order(
            self._iter_preconditions(
                _ExtractMethodState(), file_path, start_line, end_line, new_name
            )
        )
        return preconditions

    def _iter_preconditions(
        self,
        state: _ExtractMethodState,
        file_path: str,
        start_line: int,
        end_line: int,
        new_name: str,
    ) -> Iterator[Precondition]:
        """Yield this extraction's preconditions in evaluation order.

        The consumer must evaluate each yielded precondition before advancing
        (see :func:`evaluate_in_order`); the range dataflow and enclosing
        function scope are stashed on ``state`` for :meth:`plan`.
        """
        yield ValidIdentifier(new_name)
        yield FileFresh(self._index_store, file_path)

        in_function = RangeInsideFunction(
            self._index_store, file_path, start_line, end_line
        )
        yield in_function
        state.dataflow = in_function.dataflow

        yield NoControlFlowEscape(in_function.dataflow)

        top_level = TopLevelFunctionOnly(
            self._index_store, file_path, start_line, end_line
        )
        yield top_level
        state.func_scope = top_level.func_scope


def _physical_lines(source: str) -> list[str]:
    """Split ``source`` into newline-kept lines on ``\\n`` only.

    The text-side twin of :func:`~pypeeker.refactor.text_anchor.line_start_offsets`:
    ``str.splitlines`` would also break on form feeds and Unicode line
    separators, which the index's line numbers (and the byte offsets computed
    alongside these lines) never do. A trailing newline ends the last line
    rather than starting an empty one; an empty source is one empty line.
    """
    parts = source.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines or [""]


_materialize_extract_variable = register_planner(ExtractVariableIntent.kind)(
    simple_materializer(
        ExtractVariableIntent,
        ExtractVariablePlanner,
        ExtractVariableError,
        lambda intent: (intent.file_path, intent.start, intent.end, intent.new_name),
    )
)

_materialize_extract_method = register_planner(ExtractMethodIntent.kind)(
    simple_materializer(
        ExtractMethodIntent,
        ExtractMethodPlanner,
        ExtractMethodError,
        lambda intent: (
            intent.file_path,
            intent.start_line,
            intent.end_line,
            intent.new_name,
        ),
    )
)
