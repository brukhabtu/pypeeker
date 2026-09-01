"""Byte-splice engine shared by the applier and the batch overlay.

Both :class:`~pypeeker.refactor.applier.TransactionApplier` (bytes to disk)
and :func:`~pypeeker.refactor.batch.apply_to_overlay` (bytes into an
in-memory overlay) rewrite one file's content from a list of
:class:`~pypeeker.models.EditEntry` values. The algorithm is the same on
both sides — verify each edit's recorded ``old`` text against the bytes it
replaces, then splice bottom-to-top so earlier offsets stay valid — and
lives once here; each caller wraps :class:`SpliceMismatch` in its own error
type.

**Invariant: the edits for one file must not overlap.** Bottom-to-top
splicing keeps every *earlier* offset valid only because each edit rewrites
a span no other edit touches; two edits whose ``[start, end)`` ranges
intersect have no well-defined result (whichever splices first rewrites
bytes the other verifies against). Producers — planners, and the batch
scheduler's de-conflicting — are responsible for never emitting such a
pair; :func:`assert_no_overlapping_edits` is the check, and the applier's pre-flight
refuses a transaction that violates it before anything is touched. Touching
spans (``end == start`` of the next) are *not* overlaps: an insertion at an
edit's boundary is an ordinary adjacent edit.
"""

from __future__ import annotations

from collections.abc import Iterable

from pypeeker.models import EditEntry


class SpliceMismatch(Exception):
    """An edit's recorded ``old`` text does not match the bytes it replaces."""

    def __init__(self, edit: EditEntry, actual: bytes) -> None:
        self.edit = edit
        self.actual = actual
        super().__init__(
            f"content mismatch in {edit.file} at byte {edit.start}: "
            f"expected {edit.old!r}, found {actual.decode('utf-8', 'replace')!r}"
        )


def splice_edits(content: bytes, edits: Iterable[EditEntry]) -> bytes:
    """Apply one file's edits to ``content``, bottom-to-top, verifying each ``old``.

    Sorting by start offset descending means each splice leaves every earlier
    offset valid, which is what lets a transaction record plan-time offsets.
    Every edit's ``old`` text is checked against the bytes it replaces first;
    a mismatch raises :class:`SpliceMismatch` (the plan was made against
    bytes that no longer exist) and nothing partial is returned.

    Assumes the module invariant: ``edits`` do not overlap. Callers that
    cannot trust their producer run :func:`assert_no_overlapping_edits` first.
    """
    result = bytearray(content)
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        actual = bytes(result[edit.start : edit.end])
        if actual != edit.old.encode("utf-8"):
            raise SpliceMismatch(edit, actual)
        result[edit.start : edit.end] = edit.new.encode("utf-8")
    return bytes(result)


def spans_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    """Whether two half-open byte spans ``(start, end)`` intersect.

    Touching spans do not overlap: ``(0, 5)`` and ``(5, 9)`` are adjacent,
    and a zero-width insertion at either boundary is adjacent too.
    """
    return first[0] < second[1] and second[0] < first[1]


def _overlapping_edits(
    edits: Iterable[EditEntry],
) -> tuple[EditEntry, EditEntry] | None:
    """Return the first pair of same-file edits whose spans overlap, or ``None``.

    The check that states the splice invariant: edits are grouped by file
    and swept in ``(start, end)`` order against the furthest-reaching span
    seen so far, so an edit enclosing several later ones is caught against
    each of them, not just its immediate successor. Which pair is returned
    is deterministic for a given input; callers use it for the error
    message, not for repair.
    """
    by_file: dict[str, list[EditEntry]] = {}
    for edit in edits:
        by_file.setdefault(edit.file, []).append(edit)
    for path in sorted(by_file):
        ordered = sorted(by_file[path], key=lambda e: (e.start, e.end))
        reach: EditEntry | None = None
        for edit in ordered:
            if reach is not None and spans_overlap(
                (reach.start, reach.end), (edit.start, edit.end)
            ):
                return reach, edit
            if reach is None or edit.end > reach.end:
                reach = edit
    return None


class OverlappingEdits(ValueError):
    """Two edits for one file rewrite intersecting byte spans."""

    def __init__(self, first: EditEntry, second: EditEntry) -> None:
        self.first = first
        self.second = second
        super().__init__(
            f"overlapping edits in {first.file}: bytes {first.start}-{first.end} "
            f"({first.old!r} -> {first.new!r}) and bytes {second.start}-{second.end} "
            f"({second.old!r} -> {second.new!r})"
        )


def assert_no_overlapping_edits(edits: Iterable[EditEntry]) -> None:
    """Raise :class:`OverlappingEdits` if any two same-file edits overlap.

    The splice invariant as a guard: run it before :func:`splice_edits`
    wherever the edits come from a producer the caller does not control.
    """
    pair = _overlapping_edits(edits)
    if pair is not None:
        raise OverlappingEdits(*pair)


__all__ = [
    "OverlappingEdits",
    "SpliceMismatch",
    "assert_no_overlapping_edits",
    "spans_overlap",
    "splice_edits",
]
