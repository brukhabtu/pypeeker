"""Precondition framework and the checks every planner shares.

The contract lives on the package docstring
(:mod:`pypeeker.refactor.preconditions`); this module holds the
:class:`Precondition` base, :class:`PreconditionResult`,
:func:`evaluate_in_order`, and the checks shared by more than one planner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Iterable

from pypeeker.storage import IndexStore


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


class SourceIsUtf8(Precondition):
    """The file's raw bytes decode as UTF-8.

    Deliberately NOT a member of :meth:`ExtractMethodPlanner.preconditions`'s
    enumerated set: nothing about the *extraction* requires UTF-8 — the
    selected range may be pure ASCII inside an otherwise undecodable file.
    The real requirement is representability of the *edit*:
    :class:`~pypeeker.refactor.models.EditEntry` carries ``old``/``new`` as
    ``str``, and extract-method must line-split the whole file, so a planner
    that cannot decode the file cannot express an edit over it at all. This
    precondition is therefore evaluated where the planner first needs text
    (at the decode site, not in the enumerated set) and lends that refusal
    its stable ``name`` and wording so the envelope stays uniform with every
    other refusal. It caches the decoded text as :attr:`text` on a
    successful evaluation so the planner decodes exactly once.

    ``content`` need not be the whole file — a caller that only needs a
    *region* of it (extract-variable guards just the spans it actually
    decodes, so an ASCII selection inside an otherwise undecodable file
    stays extractable) passes that region alongside ``byte_offset``, the
    region's start in the file, so the reported byte in a failure stays
    file-absolute rather than relative to the region.
    """

    name = "source-is-utf8"

    def __init__(self, content: bytes, file_path: str, *, byte_offset: int = 0) -> None:
        self.content = content
        self.file_path = file_path
        self.byte_offset = byte_offset
        self.text: str = ""

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        try:
            self.text = self.content.decode("utf-8")
        except UnicodeDecodeError as error:
            return _fail(
                f"File is not valid UTF-8: {self.file_path} "
                f"(byte {self.byte_offset + error.start}: {error.reason})"
            )
        return _PASS

