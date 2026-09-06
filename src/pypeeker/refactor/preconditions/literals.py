"""Tuplify preconditions (:mod:`pypeeker.refactor.literals`, TASK-125)."""

from __future__ import annotations

from typing import ClassVar

from pypeeker.analysis import (
    TYPE_ANNOTATION,
    get_trait_provider,
    is_inferred_list,
)
from pypeeker.models import (
    FileIndex,
    Symbol,
)
from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)


# ---------------------------------------------------------------------------
# Tuplify (TASK-125)
# ---------------------------------------------------------------------------


class InferredListBinding(Precondition):
    """The variable's type annotation still records an inferred list literal (slug ``"text-mismatch"``).

    Takes the re-resolved symbol and its freshly loaded file index as
    constructor arguments (mid-plan values, same shape and order as
    :class:`NotReassigned`).

    This is the pointwise verification of the ``type-annotation`` trait that
    the ``prefer-tuple`` rule in :data:`pypeeker.dsl.RULES` quantifies over every candidate
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
