"""Preconditions shared by the phase-4 remedy planners (TASK-125).

delete-symbol, remove-import, rewrite-star-import, tuplify, replace-text and
rename-docstring-param all resolve a symbol match and re-anchor against the
current file through this set; each carries the legacy ``check --fix`` refusal
slug its failure maps onto.
"""

from __future__ import annotations

from typing import ClassVar

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
from pypeeker.storage import IndexStore


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
        """Evaluate this precondition against its captured inputs.

        Freshness is the store's own :meth:`~pypeeker.storage.IndexStore.is_stale`
        (overlay-aware on an :class:`~pypeeker.storage.OverlayIndexStore`);
        the missing-index case is separated out first only so the two
        historical messages stay distinct.
        """
        content = self._index_store.read_file(self.file_path)
        index = self._index_store.load(self.file_path)
        if index is None:
            return _fail(f"{self.file_path} is not indexed")
        if self._index_store.is_stale(self.file_path):
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
