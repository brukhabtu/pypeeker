"""Rename-docstring-param preconditions (:mod:`pypeeker.refactor.docstring_ops`, TASK-125)."""

from __future__ import annotations

from re import Pattern
from typing import ClassVar

from pypeeker.analysis import (
    ParamsSection,
    param_drift,
    parse_documented_params,
    signature_params,
)
from pypeeker.models import (
    FileIndex,
    Scope,
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
# Rename-docstring-param (TASK-125)
#
# The sixth phase-4 remedy planner, converted after the other five: ported
# from the same superseded fix protocol (``_DocstringParamRenameFix``) as
# delete-symbol/remove-import/rewrite-star-import/tuplify/replace-text, and
# sharing their legacy slugs. See ``DocstringParamRenamePlanner`` in
# :mod:`pypeeker.refactor.docstring_ops`.
# ---------------------------------------------------------------------------


class DocstringStillPresent(Precondition):
    """The function still has a recorded docstring (slug ``"text-mismatch"``)."""

    name = "docstring-still-present"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, symbol_id: str, symbol: Symbol) -> None:
        self.symbol_id = symbol_id
        self.symbol = symbol

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if not self.symbol.docstring:
            return _fail(f"'{self.symbol_id}' no longer has a docstring")
        return _PASS


class ParamsSectionPresent(Precondition):
    """The docstring still has a params section in the recorded style (slug ``"text-mismatch"``).

    Caches the parsed section as :attr:`section` on success.
    """

    name = "params-section-present"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, docstring: str, style: str) -> None:
        self.docstring = docstring
        self.style = style
        self.section: ParamsSection | None = None

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        section = parse_documented_params(self.docstring, self.style)
        if section is None:
            return _fail(
                f"the docstring no longer has a {self.style}-style params section"
            )
        self.section = section
        return _PASS


class DocumentedParamDriftSingle(Precondition):
    """The docstring/signature drift is still exactly a single-parameter rename (slug ``"ambiguous"``).

    Caches the re-derived ``(ghosts, missing)`` — documented-but-absent and
    present-but-undocumented parameter names — as :attr:`ghosts` /
    :attr:`missing` on success.
    """

    name = "documented-param-drift-single"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, section: ParamsSection, index: FileIndex, symbol: Symbol) -> None:
        self.section = section
        self._index = index
        self.symbol = symbol
        self.ghosts: list[str] = []
        self.missing: list[str] = []

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        ghosts, missing = param_drift(self.section, signature_params(self._index, self.symbol))
        if len(ghosts) != 1 or len(missing) != 1:
            return _fail(
                "the drift is no longer a single-parameter rename "
                f"({len(ghosts)} stale documented name(s), "
                f"{len(missing)} undocumented parameter(s))"
            )
        self.ghosts = ghosts
        self.missing = missing
        return _PASS


class DocumentedParamDriftMatches(Precondition):
    """The re-derived drift is still the requested rename (slug ``"text-mismatch"``).

    Takes the resolved ``ghosts``/``missing`` as constructor arguments
    (mid-plan values produced by :class:`DocumentedParamDriftSingle`).
    """

    name = "documented-param-drift-matches"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(
        self, ghosts: list[str], missing: list[str], old_param: str, new_param: str
    ) -> None:
        self.ghosts = ghosts
        self.missing = missing
        self.old_param = old_param
        self.new_param = new_param

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.ghosts[0] != self.old_param or self.missing[0] != self.new_param:
            return _fail(
                f"the drift changed: '{self.ghosts[0]}' -> '{self.missing[0]}' "
                f"rather than '{self.old_param}' -> '{self.new_param}'"
            )
        return _PASS


class DocstringScopeLocated(Precondition):
    """The function's scope span is recorded and fits inside the current file (slug ``"text-mismatch"``).

    Combines the docstring-rename port's two scope-location checks — a
    recorded scope entry and a span end line that fits inside the current
    file — since both decline under the same legacy slug, mirroring
    :class:`DeletableScope`. Caches the located :attr:`scope`,
    :attr:`line_starts`, the byte offset the scope span starts at
    (:attr:`region_start`) and the scope's byte span (:attr:`region`) on
    success.
    """

    name = "docstring-scope-located"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, index: FileIndex, content: bytes, symbol_id: str) -> None:
        self._index = index
        self.content = content
        self.symbol_id = symbol_id
        self.scope: Scope | None = None
        self.line_starts: list[int] = []
        self.region_start: int = 0
        self.region: bytes = b""

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        scope = next(
            (sc for sc in self._index.scopes if sc.scope_id == self.symbol_id), None
        )
        if scope is None:
            return _fail(f"no scope recorded for '{self.symbol_id}'")
        line_starts = line_start_offsets(self.content)
        if scope.span.end.line >= len(line_starts):
            return _fail("indexed scope span is out of range")
        self.scope = scope
        self.line_starts = line_starts
        self.region_start = line_starts[scope.span.start.line]
        region_end = line_starts[scope.span.end.line] + scope.span.end.column
        self.region = self.content[self.region_start : region_end]
        return _PASS


class DocstringTextFound(Precondition):
    """The indexed docstring text occurs at least once inside the function body (slug ``"text-mismatch"``).

    Caches the byte offset of the first match, relative to ``region``, as
    :attr:`first` on success.
    """

    name = "docstring-text-found"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, region: bytes, doc_bytes: bytes) -> None:
        self.region = region
        self.doc_bytes = doc_bytes
        self.first: int = -1

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        first = self.region.find(self.doc_bytes)
        if first == -1:
            return _fail("the docstring text was not found inside the function body")
        self.first = first
        return _PASS


class DocstringTextUnique(Precondition):
    """The indexed docstring text occurs exactly once inside the function body (slug ``"ambiguous"``)."""

    name = "docstring-text-unique"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, region: bytes, doc_bytes: bytes, first: int) -> None:
        self.region = region
        self.doc_bytes = doc_bytes
        self.first = first

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if self.region.find(self.doc_bytes, self.first + 1) != -1:
            return _fail("the docstring text occurs more than once inside the function body")
        return _PASS


class DocstringTokenFound(Precondition):
    """The old parameter name occurs as a bare name token in the docstring (slug ``"text-mismatch"``).

    ``token`` is the caller's own compiled bare-token pattern (kept out of
    this module for the same reverse-import reason
    :class:`ImportSegmentsLocatable` takes its scanner's result rather than
    the scanner). Caches the regex matches as :attr:`matches` on success.
    """

    name = "docstring-token-found"
    slug: ClassVar[str] = "text-mismatch"

    def __init__(self, doc_bytes: bytes, token: Pattern[bytes], old_param: str) -> None:
        self.doc_bytes = doc_bytes
        self._token = token
        self.old_param = old_param
        self.matches: list = []

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        matches = list(self._token.finditer(self.doc_bytes))
        if not matches:
            return _fail(
                f"'{self.old_param}' does not occur as a name token in the docstring"
            )
        self.matches = matches
        return _PASS


class DocstringTokenUnique(Precondition):
    """The old parameter name occurs exactly once as a bare name token (slug ``"ambiguous"``).

    Takes the resolved regex matches as a constructor argument (mid-plan
    value produced by :class:`DocstringTokenFound`).
    """

    name = "docstring-token-unique"
    slug: ClassVar[str] = "ambiguous"

    def __init__(self, matches: list, old_param: str) -> None:
        self.matches = matches
        self.old_param = old_param

    def evaluate(self) -> PreconditionResult:
        """Evaluate this precondition against its captured inputs."""
        if len(self.matches) > 1:
            return _fail(
                f"'{self.old_param}' occurs {len(self.matches)} times in the "
                "docstring; rewriting one occurrence is unsafe"
            )
        return _PASS
