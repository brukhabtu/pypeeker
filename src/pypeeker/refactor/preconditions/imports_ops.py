"""Remove-import and rewrite-star-import preconditions (:mod:`pypeeker.refactor.imports_ops`, TASK-125)."""

from __future__ import annotations

from collections.abc import Mapping
from re import Pattern
from typing import ClassVar

from pypeeker.models import (
    FileIndex,
    Symbol,
)
from pypeeker.refactor.text_anchor import line_start_offsets
from pypeeker.refactor.preconditions.base import (
    _PASS,
    Precondition,
    PreconditionResult,
    _fail,
)


# ---------------------------------------------------------------------------
# Remove-import / rewrite-star-import (TASK-125)
# ---------------------------------------------------------------------------


class ImportLineInRange(Precondition):
    """The import symbol's recorded line still exists in the current file (slug ``"text-mismatch"``).

    Caches the file's line-start offsets and the physical line's byte
    span/content (:attr:`line_starts`, :attr:`line_start`, :attr:`line_stop`,
    :attr:`line`, :attr:`body`) for the checks that follow and the planner's
    own edit.
    """

    name = "import-line-in-range"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, line_no: int) -> None:
        self.content = content
        self.line_no = line_no
        self.line_starts: list[int] = []
        self.line_start: int = 0
        self.line_stop: int = 0
        self.line: bytes = b""
        self.body: bytes = b""

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        line_starts = line_start_offsets(self.content)
        if self.line_no >= len(line_starts):
            return _fail("indexed import line is out of range")
        self.line_starts = line_starts
        self.line_start = line_starts[self.line_no]
        self.line_stop = (
            line_starts[self.line_no + 1]
            if self.line_no + 1 < len(line_starts)
            else len(self.content)
        )
        self.line = self.content[self.line_start : self.line_stop]
        self.body = self.line.rstrip(b"\r\n")
        return _PASS


class ImportStatementLine(Precondition):
    """The recorded line still reads as an ``import``/``from`` statement (slug ``"text-mismatch"``)."""

    name = "import-statement-line"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, body: bytes) -> None:
        self.body = body

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        stripped = self.body.lstrip()
        if not (stripped.startswith(b"import ") or stripped.startswith(b"from ")):
            return _fail("indexed line is not an import statement")
        return _PASS


class ImportLineSurgerySafe(Precondition):
    """The import line has no parens or line-continuation (slug ``"ambiguous"``).

    Byte-level surgery on a parenthesized or backslash-continued import list
    is too fragile to attempt safely.
    """

    name = "import-line-surgery-safe"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, body: bytes) -> None:
        self.body = body

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if b"(" in self.body or self.body.rstrip().endswith(b"\\"):
            return _fail("parenthesized or continued import lists are not edited")
        return _PASS


class ImportSegmentsLocatable(Precondition):
    """The import line's comma-separated name segments could be parsed (slug ``"text-mismatch"``).

    ``segments`` is the caller's own
    ``imports_ops._import_name_segments(body, is_from_import)`` result — kept
    out of this module to avoid a reverse import, the same pattern
    :class:`ScannableLiteral` uses for tuplify's bracket scanner.
    """

    name = "import-segments-locatable"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, segments: list | None) -> None:
        self.segments = segments

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.segments is None:
            return _fail("could not locate the imported-names part of the line")
        return _PASS


class ImportNameUnambiguousOnLine(Precondition):
    """Exactly one comma-separated entry on the line binds the target name (slug ``"ambiguous"``)."""

    name = "import-name-unambiguous-on-line"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, name: str, index_matches: list[int]) -> None:
        self.import_name = name
        self.index_matches = index_matches

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.index_matches) != 1:
            return _fail(
                f"'{self.import_name}' does not match exactly one name on the import line"
            )
        return _PASS


class SingleStarImportInFile(Precondition):
    """At most one star import remains in the file (slug ``"ambiguous"``).

    First-star-wins attribution (see
    :mod:`pypeeker.refactor.imports_ops`'s module docstring) is heuristic
    once a second star import appears.
    """

    name = "single-star-import-in-file"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, file_path: str, stars: list[Symbol]) -> None:
        self.file_path = file_path
        self.stars = stars

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.stars) > 1:
            return _fail(
                f"{self.file_path} now has {len(self.stars)} star imports; "
                "first-star-wins attribution is heuristic there"
            )
        return _PASS


class StarTargetModuleIndexed(Precondition):
    """The star import's target module is indexed, so its supply is derivable (slug ``"ambiguous"``)."""

    name = "star-target-module-indexed"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, imported_from: str, modules: Mapping[str, FileIndex]) -> None:
        self.imported_from = imported_from
        self.modules = modules

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.imported_from not in self.modules:
            return _fail(
                f"target module '{self.imported_from}' is not indexed; the "
                "names the star supplies cannot be derived"
            )
        return _PASS


class StarAttributionUnambiguous(Precondition):
    """Every unresolved bare name attributes to a star-imported module's surface (slug ``"ambiguous"``)."""

    name = "star-attribution-unambiguous"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, unattributed: list[str]) -> None:
        self.unattributed = unattributed

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.unattributed:
            return _fail(
                "unresolved name(s) "
                + ", ".join(f"'{n}'" for n in self.unattributed)
                + " match no star-imported module's public surface — the star "
                "import may still supply them (e.g. via a transitive star "
                "import), so the rewrite cannot be proven complete"
            )
        return _PASS


class StarSupplyNonEmpty(Precondition):
    """The star import actually supplies at least one used name (slug ``"ambiguous"``)."""

    name = "star-supply-nonempty"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, imported_from: str, names: list[str]) -> None:
        self.imported_from = imported_from
        self.names = names

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.names:
            return _fail(
                f"no names from '{self.imported_from}' are used; delete the "
                "star import instead of rewriting it"
            )
        return _PASS


class StarTokenMatches(Precondition):
    """The ``*`` token still sits at its indexed byte location (slug ``"text-mismatch"``)."""

    name = "star-token-matches"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, offset: int | None) -> None:
        self.content = content
        self.offset = offset

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.offset is None or self.content[self.offset : self.offset + 1] != b"*":
            return _fail("the '*' token is not at its indexed location")
        return _PASS


class StarLinePlainForm(Precondition):
    """The star's line is a plain, single-line ``from <module> import *`` (slug ``"text-mismatch"``).

    ``prefix_re`` is the caller's own compiled pattern (``imports_ops.
    _STAR_LINE_PREFIX``), kept out of this module for the same reason
    :class:`ImportSegmentsLocatable` takes its scanner's result rather than
    the scanner itself.
    """

    name = "star-line-plain-form"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, content: bytes, offset: int, prefix_re: Pattern[bytes]) -> None:
        self.content = content
        self.offset = offset
        self._prefix_re = prefix_re

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs.

        Assumes :class:`StarTokenMatches` already verified ``offset`` points
        at the ``*`` byte (this precondition only runs after that one
        passes); it only re-derives the line start and checks the prefix.
        """
        line_start = self.content.rfind(b"\n", 0, self.offset) + 1
        if self._prefix_re.fullmatch(self.content[line_start : self.offset]) is None:
            return _fail(
                "the indexed line is not a plain 'from <module> import *' statement"
            )
        return _PASS
