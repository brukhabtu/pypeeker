"""Fix contract: the protocol and value types a violation-attached fix uses.

Extracted from :mod:`pypeeker.check.fixes` so :mod:`pypeeker.check.models`
(``Violation.fix``) can depend on the ``Fix`` protocol without importing the
concrete fixes — breaking the ``models`` <-> ``fixes`` import cycle. This is a
leaf within ``check``: it imports the semantic model and storage, never
``check.models`` or ``check.fixes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from pypeeker.models import EditEntry
from pypeeker.storage import IndexStore


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
