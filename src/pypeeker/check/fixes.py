"""Fix protocol: violations carry plannable fixes.

Connects the two halves of pypeeker: rules emit :class:`Violation` objects,
refactors apply :class:`~pypeeker.models.transaction.EditEntry` transactions.
A rule that knows how to repair what it flagged attaches a :class:`Fix` to the
violation (via :func:`with_fix`); a consumer (``check --fix``, or a composite
planner wrapping fixes as guarded intents) later calls :meth:`Fix.plan` to
turn the fix into concrete edits against the *current* file state.

The contract
============

``plan(store)`` receives the project's :class:`~pypeeker.storage.IndexStore`
and returns either:

* :class:`FixPlan` — a list of ``EditEntry`` byte edits whose offsets and
  ``file_hash`` values were computed from the file bytes **as read at plan
  time** (through ``store.project_root``), so the hashes are fresh and the
  edits apply cleanly via ``TransactionApplier``; or
* :class:`FixDeclined` — a machine-readable :class:`DeclineReason` when the
  fix cannot be planned safely (anchor text gone, ambiguous target, missing
  file, stale index).

Fixes must be **replannable**: ``plan()`` may be called any number of times,
arbitrarily long after detection, against state that has since changed. A fix
therefore never caches byte offsets from detection time — it anchors on
something re-resolvable (a symbol id, or a location plus the expected text)
and re-verifies the anchor against current bytes before emitting edits, the
same pattern as ``RenamePlanner._build_edits``. If the anchor no longer holds,
it declines rather than guessing.

Violations (and the fixes they carry) are in-memory only — nothing in
pypeeker serializes or persists them. Only the *output* of ``plan()`` is ever
persisted, as EditEntry lines in a transaction JSONL.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.storage.index_store import IndexStore

if TYPE_CHECKING:
    from pypeeker.check.models import Violation


class DeclineReason(str, Enum):
    """Machine-readable reason a fix could not be planned.

    * ``STALE_INDEX``   — the fix needs index data (e.g. a symbol location)
                          but the index no longer matches the file on disk.
    * ``TEXT_MISMATCH`` — the expected anchor text is no longer present.
    * ``AMBIGUOUS``     — the anchor moved and now matches more than one
                          candidate location, so re-anchoring is unsafe.
    * ``FILE_MISSING``  — the target file no longer exists.
    """

    STALE_INDEX = "stale-index"
    TEXT_MISMATCH = "text-mismatch"
    AMBIGUOUS = "ambiguous"
    FILE_MISSING = "file-missing"


@dataclass(frozen=True)
class FixPlan:
    """A successfully planned fix: edits valid against current file state.

    ``edits`` are byte-offset :class:`EditEntry` objects whose ``file_hash``
    was computed at plan time, so they round-trip through ``TransactionStore``
    / ``TransactionApplier`` without further translation.
    """

    fix_id: str
    description: str
    edits: list[EditEntry]


@dataclass(frozen=True)
class FixDeclined:
    """A fix that refused to plan against the current state.

    ``reason`` is machine-readable (for ``--fix`` reporting and intent
    guards); ``detail`` is a human-readable elaboration.
    """

    fix_id: str
    reason: DeclineReason
    detail: str = ""


@runtime_checkable
class Fix(Protocol):
    """What a violation-attached fix must provide.

    Implementations carry a stable ``fix_id`` (machine-readable, stable across
    runs for the same logical repair) and a human-readable ``description``,
    and implement :meth:`plan` per the module-level contract: read current
    file bytes through ``store``, emit fresh-hash edits or decline. ``plan``
    must be safe to call repeatedly and must never rely on byte offsets
    captured at detection time.
    """

    @property
    def fix_id(self) -> str:
        """Stable identifier for this fix (e.g. ``"prefer-tuple:listify"``)."""
        ...

    @property
    def description(self) -> str:
        """One-line human-readable summary of what applying the fix does."""
        ...

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Produce edits valid for the *current* file state, or decline."""
        ...


@dataclass(frozen=True)
class ReplaceTextFix:
    """Reference :class:`Fix`: replace one occurrence of known text.

    Anchored on the location where the rule saw ``old_text`` (0-indexed
    ``line``/``column``, byte column, matching index conventions) plus the
    expected text itself. At plan time it re-reads the file and re-resolves
    the anchor:

    1. If ``old_text`` still sits exactly at (line, column), plan there.
    2. Otherwise — the file changed since detection — fall back to searching
       the current bytes: a *unique* occurrence of ``old_text`` re-anchors the
       fix (benign unrelated edits re-plan fine); zero occurrences decline
       with ``TEXT_MISMATCH``; multiple occurrences decline with
       ``AMBIGUOUS``.

    Offsets and the ``file_hash`` are always computed from the bytes read at
    plan time, never cached from detection.
    """

    fix_id: str
    description: str
    file_path: str  # project-root-relative, like Violation.file_path
    line: int  # 0-indexed line of the anchor (index convention)
    column: int  # 0-indexed byte column of the anchor
    old_text: str
    new_text: str

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Plan the replacement against current bytes; verify or re-anchor."""
        source = store.project_root / self.file_path
        if not source.exists():
            return FixDeclined(
                self.fix_id,
                DeclineReason.FILE_MISSING,
                f"{self.file_path} no longer exists",
            )
        content = source.read_bytes()
        old_bytes = self.old_text.encode("utf-8")

        start = self._resolve_anchor(content, old_bytes)
        if isinstance(start, FixDeclined):
            return start

        edit = EditEntry(
            op=EditOp.REPLACE,
            file=self.file_path,
            start=start,
            end=start + len(old_bytes),
            old=self.old_text,
            new=self.new_text,
            file_hash=IndexStore.compute_file_hash(source),
        )
        return FixPlan(self.fix_id, self.description, [edit])

    def _resolve_anchor(
        self, content: bytes, old_bytes: bytes
    ) -> int | FixDeclined:
        """Byte offset where ``old_text`` verifiably sits, or a decline."""
        offset = _position_to_byte_offset(content, self.line, self.column)
        if offset is not None and content[offset : offset + len(old_bytes)] == old_bytes:
            return offset
        # The recorded location no longer holds the text: re-anchor only if
        # the expected text occurs exactly once in the current file.
        first = content.find(old_bytes)
        if first == -1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"expected text {self.old_text!r} not found in {self.file_path}",
            )
        if content.find(old_bytes, first + 1) != -1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"expected text {self.old_text!r} occurs more than once "
                f"in {self.file_path}",
            )
        return first


def with_fix(violation: Violation, fix: Fix) -> Violation:
    """THE way rules attach a fix to a violation.

    Returns a copy of ``violation`` carrying ``fix``; the original is
    untouched (Violation is frozen). Because ``Violation.fix`` is excluded
    from comparison and repr, the returned violation sorts, compares, and
    prints exactly like the original.
    """
    return dataclasses.replace(violation, fix=fix)


def _position_to_byte_offset(content: bytes, line: int, column: int) -> int | None:
    """0-indexed line/byte-column to byte offset; None when out of range.

    Same arithmetic as ``pypeeker.refactor.planner.position_to_byte_offset``
    but returns ``None`` instead of raising — for a replannable fix, an
    out-of-range detection-time location is an anchor miss, not an error.
    """
    offset = 0
    for i, file_line in enumerate(content.split(b"\n")):
        if i == line:
            if column > len(file_line):
                return None
            return offset + column
        offset += len(file_line) + 1  # +1 for the newline
    return None
