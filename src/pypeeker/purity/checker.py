"""Heuristic purity checker.

Analyzes a function's body via its index data and reports evidence of side
effects. The verdict is heuristic: PROBABLY_PURE means no impurity was found,
not that the function is provably pure.
"""

from __future__ import annotations

from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.purity.impure_builtins import is_impure_attribute, is_impure_builtin
from pypeeker.purity.models import Evidence, EvidenceKind, PurityResult, PurityVerdict
from pypeeker.query.engine import SemanticQueryEngine
from pypeeker.storage.store import IndexStore

UNRESOLVED_PREFIX = "<unresolved>."


class PurityChecker:
    """Analyze functions for evidence of impurity using indexed semantic data."""

    def __init__(self, store: IndexStore) -> None:
        self._store = store
        self._engine = SemanticQueryEngine(store)

    def check(self, symbol_id: str) -> PurityResult:
        """Check if the function identified by `symbol_id` appears pure."""
        target = self._find_function_symbol(symbol_id)
        if target is None:
            return PurityResult(
                symbol_id=symbol_id,
                verdict=PurityVerdict.UNKNOWN,
                confidence=Confidence.HEURISTIC,
                evidence=[Evidence(kind=EvidenceKind.NOT_FOUND)],
            )

        if target.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            return PurityResult(
                symbol_id=target.symbol_id,
                verdict=PurityVerdict.UNKNOWN,
                confidence=Confidence.HEURISTIC,
                evidence=[
                    Evidence(
                        kind=EvidenceKind.NOT_A_FUNCTION,
                        detail=f"symbol kind is {target.kind.value}",
                    )
                ],
            )

        file_index = self._store.load(target.location.file_path)
        if file_index is None:
            return PurityResult(
                symbol_id=target.symbol_id,
                verdict=PurityVerdict.UNKNOWN,
                confidence=Confidence.HEURISTIC,
                evidence=[
                    Evidence(
                        kind=EvidenceKind.NOT_FOUND,
                        detail=f"file index missing for {target.location.file_path}",
                    )
                ],
            )

        func_scope_id = self._function_scope_id(target, file_index)
        if func_scope_id is None:
            return PurityResult(
                symbol_id=target.symbol_id,
                verdict=PurityVerdict.UNKNOWN,
                confidence=Confidence.HEURISTIC,
                evidence=[
                    Evidence(
                        kind=EvidenceKind.NOT_A_FUNCTION,
                        detail="no function scope found for symbol",
                    )
                ],
            )

        subtree = self._scope_subtree(file_index, func_scope_id)
        local_symbol_ids = {
            s.symbol_id for s in file_index.symbols if s.parent_scope_id in subtree
        }
        local_variable_ids = {
            s.symbol_id
            for s in file_index.symbols
            if s.parent_scope_id in subtree and s.kind == SymbolKind.VARIABLE
        }
        # Index reads by line so we can suppress attribute-mutation evidence
        # when the receiver is a local variable (e.g. ``x = []; x.append(1)``).
        reads_by_line: dict[int, set[str]] = {}
        for ref in file_index.references:
            if (
                ref.in_scope_id in subtree
                and ref.kind == ReferenceKind.READ
            ):
                reads_by_line.setdefault(
                    ref.location.span.start.line, set()
                ).add(ref.symbol_id)

        evidence: list[Evidence] = []
        for ref in file_index.references:
            if ref.in_scope_id not in subtree:
                continue
            evidence.extend(
                self._classify_reference(
                    ref, local_symbol_ids, local_variable_ids, reads_by_line
                )
            )

        verdict = (
            PurityVerdict.IMPURE if evidence else PurityVerdict.PROBABLY_PURE
        )
        return PurityResult(
            symbol_id=target.symbol_id,
            verdict=verdict,
            confidence=Confidence.HEURISTIC,
            evidence=evidence,
        )

    def _classify_reference(
        self,
        ref,
        local_symbol_ids: set[str],
        local_variable_ids: set[str],
        reads_by_line: dict[int, set[str]],
    ) -> list[Evidence]:
        """Inspect a single reference and produce zero or more evidence items."""
        line = ref.location.span.start.line
        if ref.kind == ReferenceKind.WRITE:
            if ref.symbol_id in local_symbol_ids:
                return []
            if ref.symbol_id.startswith(UNRESOLVED_PREFIX):
                return [
                    Evidence(
                        kind=EvidenceKind.ATTRIBUTE_WRITE,
                        line=line,
                        target=ref.symbol_id,
                    )
                ]
            return [
                Evidence(
                    kind=EvidenceKind.WRITES_OUTER_SCOPE,
                    line=line,
                    target=ref.symbol_id,
                )
            ]
        if ref.kind == ReferenceKind.CALL:
            return self._classify_call(ref, line, local_variable_ids, reads_by_line)
        return []

    def _classify_call(
        self,
        ref,
        line: int,
        local_variable_ids: set[str],
        reads_by_line: dict[int, set[str]],
    ) -> list[Evidence]:
        """Match a CALL reference against the impure-name denylists."""
        sid = ref.symbol_id
        if sid.startswith(UNRESOLVED_PREFIX):
            tail = sid[len(UNRESOLVED_PREFIX):]
            if not is_impure_attribute(tail):
                return []
            # Suppress if the receiver on this line appears to be a local
            # variable (e.g. ``x = []; x.append(1)``). Parameters are NOT
            # suppressed: mutating them is a side effect on caller state.
            same_line_reads = reads_by_line.get(line, set())
            if same_line_reads & local_variable_ids:
                return []
            return [
                Evidence(
                    kind=EvidenceKind.CALLS_IMPURE_STDLIB,
                    line=line,
                    target=sid,
                    detail=tail,
                )
            ]
        # Bare unresolved name (builtin or out-of-project)
        if not ref.resolved and ":" not in sid and is_impure_builtin(sid):
            return [
                Evidence(
                    kind=EvidenceKind.CALLS_IMPURE_BUILTIN,
                    line=line,
                    target=sid,
                )
            ]
        return []

    def _find_function_symbol(self, symbol_id: str) -> Symbol | None:
        """Resolve a symbol_id (full or partial) to a Symbol, preferring functions."""
        matches = self._engine.find_symbol(symbol_id)
        if not matches:
            return None
        # Prefer function/method over other kinds when name is ambiguous.
        for s in matches:
            if s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                return s
        return matches[0]

    def _scope_subtree(self, file_index: FileIndex, root_scope_id: str) -> set[str]:
        """Return all scope_ids reachable from `root_scope_id` via child_scope_ids."""
        scope_map = {s.scope_id: s for s in file_index.scopes}
        result: set[str] = set()
        stack = [root_scope_id]
        while stack:
            scope_id = stack.pop()
            if scope_id in result:
                continue
            scope = scope_map.get(scope_id)
            if scope is None:
                continue
            result.add(scope_id)
            stack.extend(scope.child_scope_ids)
        return result

    def _function_scope_id(self, target: Symbol, file_index: FileIndex) -> str | None:
        """Find the scope created by this function symbol."""
        # Function scopes have scope_id == symbol_id in pypeeker's binder.
        for scope in file_index.scopes:
            if scope.scope_id == target.symbol_id and scope.kind in (
                ScopeKind.FUNCTION,
                ScopeKind.LAMBDA,
            ):
                return scope.scope_id
        return None
