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

from pypeeker.models.capabilities import Confidence
from pypeeker.models.symbols import SymbolKind
from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.storage.index_store import IndexStore

if TYPE_CHECKING:
    from pypeeker.models.index import FileIndex

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


# ── index-anchored fixes ────────────────────────────────────────────────────
# The three first-wave autofixes (TASK-84) share one anchoring strategy:
# plan() re-reads the file, re-loads its index entry, and refuses to plan
# unless the index hash matches the bytes on disk (STALE_INDEX otherwise).
# The symbol is then RE-LOCATED from the current index — never from offsets
# captured at detection time — and the located text is verified before any
# edit is emitted, the same belt-and-braces pattern as ReplaceTextFix.


@dataclass(frozen=True)
class PreferTupleFix:
    """Rewrite a never-mutated list literal ``[...]`` as a tuple ``(...)``.

    Anchored on the VARIABLE symbol id: plan() re-locates the name token via
    the current index, expects ``name = [`` (an inferred-list binding, i.e.
    a bare assignment whose RHS starts with a list literal), and matches the
    closing ``]`` with a small byte-level bracket scanner. Only the two
    bracket bytes are replaced; a single-element list closes with ``,)`` so
    ``[x]`` becomes ``(x,)`` rather than a parenthesized expression.

    The scanner is deliberately conservative — strings and comments make
    byte-level bracket matching fragile, so it declines (``AMBIGUOUS``) on
    anything it cannot scan safely: f-strings (3.12+ allows same-quote
    nesting inside ``{...}``), triple-quoted strings, and unterminated
    strings. Plain/raw/byte single-line strings and ``#`` comments inside
    the literal are scanned through correctly.
    """

    file_path: str  # project-root-relative, like Violation.file_path
    symbol_id: str  # the VARIABLE symbol bound to the list literal
    name: str  # the variable name (for anchoring and messages)

    @property
    def fix_id(self) -> str:
        """Stable id: ``prefer-tuple:tuplify:<symbol_id>``."""
        return f"prefer-tuple:tuplify:{self.symbol_id}"

    @property
    def description(self) -> str:
        """One-line summary of the rewrite."""
        return f"rewrite the list literal bound to '{self.name}' as a tuple"

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Re-locate the literal via the current index and rewrite its brackets."""
        state = _current_state(store, self.file_path, self.fix_id)
        if isinstance(state, FixDeclined):
            return state
        content, index = state

        symbol = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == self.symbol_id and s.kind is SymbolKind.VARIABLE
            ),
            None,
        )
        if symbol is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"variable '{self.symbol_id}' is no longer in the index",
            )
        ann = symbol.type_annotation
        if ann is None or ann.raw != "list" or ann.confidence is not Confidence.INFERRED:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"'{self.name}' is no longer bound to an inferred list literal",
            )

        name_bytes = self.name.encode("utf-8")
        offset = _position_to_byte_offset(
            content, symbol.location.span.start.line, symbol.location.span.start.column
        )
        if offset is None or content[offset : offset + len(name_bytes)] != name_bytes:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"name '{self.name}' not found at its indexed location",
            )

        open_off = _expect_assignment_list(content, offset + len(name_bytes))
        if open_off is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"assignment to '{self.name}' no longer binds a list literal",
            )

        scan = _match_list_literal(content, open_off)
        if isinstance(scan, str):  # scanner gave up: a human must look
            return FixDeclined(self.fix_id, DeclineReason.AMBIGUOUS, scan)
        close_off, top_level_commas, has_elements = scan

        # ``[x]`` must become ``(x,)`` — ``(x)`` is just x — while ``[]``
        # and any literal with a top-level comma keep a plain ``)``.
        close_new = ",)" if has_elements and top_level_commas == 0 else ")"
        file_hash = IndexStore.compute_file_hash(store.project_root / self.file_path)
        edits = [
            EditEntry(
                op=EditOp.REPLACE,
                file=self.file_path,
                start=open_off,
                end=open_off + 1,
                old="[",
                new="(",
                file_hash=file_hash,
            ),
            EditEntry(
                op=EditOp.REPLACE,
                file=self.file_path,
                start=close_off,
                end=close_off + 1,
                old="]",
                new=close_new,
                file_hash=file_hash,
            ),
        ]
        return FixPlan(self.fix_id, self.description, edits)


@dataclass(frozen=True)
class RemoveUnusedImportFix:
    """Delete an unused import binding.

    Anchored on the IMPORT symbol id: plan() re-locates the bound name token
    via the current index and edits the physical import line. A single-name
    line (``import x`` / ``from m import x`` / ``import x as y``) is deleted
    whole, including its newline; on a multi-name line only the matching
    name entry and its adjacent comma are deleted.

    Conservative declines (``AMBIGUOUS``): parenthesized import lists,
    backslash-continued lines, and multi-name lines where the bound name
    cannot be matched to exactly one comma-separated entry (e.g. a trailing
    comment glued to the entry).
    """

    file_path: str  # project-root-relative, like Violation.file_path
    symbol_id: str  # the IMPORT symbol to remove
    name: str  # the locally bound name (alias when aliased)

    @property
    def fix_id(self) -> str:
        """Stable id: ``unused-imports:remove:<symbol_id>``."""
        return f"unused-imports:remove:{self.symbol_id}"

    @property
    def description(self) -> str:
        """One-line summary of the deletion."""
        return f"remove the unused import '{self.name}'"

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Re-locate the import via the current index and delete it."""
        state = _current_state(store, self.file_path, self.fix_id)
        if isinstance(state, FixDeclined):
            return state
        content, index = state

        symbol = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == self.symbol_id and s.kind is SymbolKind.IMPORT
            ),
            None,
        )
        if symbol is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"import '{self.symbol_id}' is no longer in the index",
            )

        line_no = symbol.location.span.start.line
        line_starts = _line_start_offsets(content)
        if line_no >= len(line_starts):
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "indexed import line is out of range",
            )
        line_start = line_starts[line_no]
        line_end = (
            line_starts[line_no + 1] if line_no + 1 < len(line_starts) else len(content)
        )
        line = content[line_start:line_end]  # includes the trailing newline (if any)
        body = line.rstrip(b"\r\n")

        name_bytes = self.name.encode("utf-8")
        column = symbol.location.span.start.column
        if body[column : column + len(name_bytes)] != name_bytes:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"name '{self.name}' not found at its indexed location",
            )

        stripped = body.lstrip()
        if not (stripped.startswith(b"import ") or stripped.startswith(b"from ")):
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "indexed line is not an import statement",
            )
        if b"(" in body or body.rstrip().endswith(b"\\"):
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                "parenthesized or continued import lists are not edited",
            )

        segments = _import_name_segments(body, stripped.startswith(b"from "))
        if segments is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "could not locate the imported-names part of the line",
            )

        file_hash = IndexStore.compute_file_hash(store.project_root / self.file_path)
        if len(segments) == 1:
            # Single-name import: delete the whole line, newline included.
            edit = EditEntry(
                op=EditOp.DELETE,
                file=self.file_path,
                start=line_start,
                end=line_end,
                old=line.decode("utf-8"),
                new="",
                file_hash=file_hash,
            )
            return FixPlan(self.fix_id, self.description, [edit])

        matches = [
            i for i, seg in enumerate(segments) if seg.bound_name == name_bytes
        ]
        if len(matches) != 1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"'{self.name}' does not match exactly one name on the import line",
            )
        i = matches[0]
        if i + 1 < len(segments):
            # Not the last entry: delete from its text through the start of
            # the next entry's text (covers the comma and following space).
            del_start, del_end = segments[i].text_start, segments[i + 1].text_start
        else:
            # Last entry: delete the preceding comma run through its text end.
            del_start, del_end = segments[i - 1].text_end, segments[i].text_end
        edit = EditEntry(
            op=EditOp.DELETE,
            file=self.file_path,
            start=line_start + del_start,
            end=line_start + del_end,
            old=body[del_start:del_end].decode("utf-8"),
            new="",
            file_hash=file_hash,
        )
        return FixPlan(self.fix_id, self.description, [edit])


@dataclass(frozen=True)
class DeleteUnusedSymbolFix:
    """Delete an unreferenced module-level function or class definition.

    Anchored on the symbol id: plan() re-locates the definition via the
    current index, derives the byte range from the symbol's scope span
    (definition line through the scope's last line) plus any trailing blank
    lines up to the next non-blank, and verifies the range starts with the
    expected ``def``/``async def``/``class`` header before emitting one
    DELETE edit.

    Conservative declines (``AMBIGUOUS``): decorated symbols (decorators sit
    above the scope span, and extending the deletion upward is not verified
    territory), and a last scope line carrying trailing non-comment content.
    """

    file_path: str  # project-root-relative, like Violation.file_path
    symbol_id: str  # the FUNCTION/CLASS symbol to delete
    name: str  # the symbol name (for anchoring and messages)

    @property
    def fix_id(self) -> str:
        """Stable id: ``unused-symbol:delete:<symbol_id>``."""
        return f"unused-symbol:delete:{self.symbol_id}"

    @property
    def description(self) -> str:
        """One-line summary of the deletion."""
        return f"delete the unreferenced definition of '{self.name}'"

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Re-locate the definition via the current index and delete its span."""
        state = _current_state(store, self.file_path, self.fix_id)
        if isinstance(state, FixDeclined):
            return state
        content, index = state

        symbol = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == self.symbol_id
                and s.kind in (SymbolKind.FUNCTION, SymbolKind.CLASS)
            ),
            None,
        )
        if symbol is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"symbol '{self.symbol_id}' is no longer in the index",
            )
        if symbol.decorators:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"'{self.name}' is decorated; decorator lines sit above the "
                "scope span and are not deleted",
            )
        scope = next(
            (sc for sc in index.scopes if sc.scope_id == self.symbol_id), None
        )
        if scope is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"no scope recorded for '{self.symbol_id}'",
            )

        line_starts = _line_start_offsets(content)
        if scope.span.end.line >= len(line_starts):
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "indexed scope span is out of range",
            )
        start = line_starts[scope.span.start.line]

        # Text anchor: the first deleted line must be the expected header.
        header = content[start : _line_end(line_starts, content, scope.span.start.line)]
        if not _is_definition_header(header, symbol.kind.value, self.name):
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"expected a '{symbol.kind.value} {self.name}' header at the "
                "indexed definition line",
            )

        # The last scope line must hold nothing after the span end except
        # whitespace or a comment — anything else would be deleted too.
        end_line = scope.span.end.line
        span_end = line_starts[end_line] + scope.span.end.column
        tail = content[span_end : _line_end(line_starts, content, end_line)]
        if tail.strip() and not tail.lstrip().startswith(b"#"):
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                "the definition's last line carries trailing code",
            )
        end = (
            line_starts[end_line + 1]
            if end_line + 1 < len(line_starts)
            else len(content)
        )
        # Eat trailing blank lines up to the next non-blank line.
        for next_line in range(end_line + 1, len(line_starts)):
            line_end = _line_end(line_starts, content, next_line)
            if content[line_starts[next_line] : line_end].strip():
                break
            end = (
                line_starts[next_line + 1]
                if next_line + 1 < len(line_starts)
                else len(content)
            )

        edit = EditEntry(
            op=EditOp.DELETE,
            file=self.file_path,
            start=start,
            end=end,
            old=content[start:end].decode("utf-8"),
            new="",
            file_hash=IndexStore.compute_file_hash(
                store.project_root / self.file_path
            ),
        )
        return FixPlan(self.fix_id, self.description, [edit])


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


# ── shared anchoring/scanning helpers for the index-anchored fixes ──────────


def _current_state(
    store: IndexStore, file_path: str, fix_id: str
) -> tuple[bytes, FileIndex] | FixDeclined:
    """Current file bytes plus a verified-fresh index entry, or a decline.

    The index-anchored fixes re-locate their targets through the stored
    :class:`~pypeeker.models.index.FileIndex`, so the entry must describe the
    bytes on disk: a missing file declines ``FILE_MISSING``; a missing entry
    or a hash mismatch (the file changed after indexing — e.g. it was edited
    between detection and plan without a re-index) declines ``STALE_INDEX``.
    """
    source = store.project_root / file_path
    if not source.exists():
        return FixDeclined(
            fix_id, DeclineReason.FILE_MISSING, f"{file_path} no longer exists"
        )
    index = store.load(file_path)
    if index is None:
        return FixDeclined(
            fix_id, DeclineReason.STALE_INDEX, f"{file_path} is not indexed"
        )
    if index.file_hash != IndexStore.compute_file_hash(source):
        return FixDeclined(
            fix_id,
            DeclineReason.STALE_INDEX,
            f"{file_path} changed since it was indexed; re-index and re-plan",
        )
    return source.read_bytes(), index


def _line_start_offsets(content: bytes) -> list[int]:
    """Byte offset of the start of every physical line in ``content``."""
    offsets = [0]
    for i, byte in enumerate(content):
        if byte == 0x0A and i + 1 < len(content):  # b"\n"
            offsets.append(i + 1)
    return offsets


def _line_end(line_starts: list[int], content: bytes, line: int) -> int:
    """Byte offset of the end of ``line`` (its newline excluded)."""
    end = line_starts[line + 1] if line + 1 < len(line_starts) else len(content)
    return end - 1 if end > 0 and content[end - 1 : end] == b"\n" else end


def _is_definition_header(header: bytes, kind: str, name: str) -> bool:
    """True when ``header`` is the ``def``/``async def``/``class`` line of ``name``."""
    stripped = header.strip()
    keywords = (b"class",) if kind == "class" else (b"def", b"async def")
    name_bytes = name.encode("utf-8")
    for keyword in keywords:
        prefix = keyword + b" " + name_bytes
        if stripped.startswith(prefix):
            rest = stripped[len(prefix) : len(prefix) + 1]
            if not rest or not (rest.isalnum() or rest == b"_"):
                return True
    return False


def _expect_assignment_list(content: bytes, after_name: int) -> int | None:
    """Offset of the ``[`` opening ``name = [...]``, or None when absent.

    Starting just past the variable name token, skips spaces/tabs, requires a
    single ``=`` (not ``==``), skips spaces/tabs again, and requires ``[``.
    Anything else — an annotation, an augmented assignment, a non-literal
    RHS — is not the shape the prefer-tuple rule detected, so no offset.
    """
    i = after_name
    n = len(content)
    while i < n and content[i : i + 1] in (b" ", b"\t"):
        i += 1
    if i >= n or content[i : i + 1] != b"=" or content[i + 1 : i + 2] == b"=":
        return None
    i += 1
    while i < n and content[i : i + 1] in (b" ", b"\t"):
        i += 1
    if i >= n or content[i : i + 1] != b"[":
        return None
    return i


def _match_list_literal(
    content: bytes, open_off: int
) -> tuple[int, int, bool] | str:
    """Match the ``]`` closing the ``[`` at ``open_off`` by scanning bytes.

    Returns ``(close_offset, top_level_commas, has_elements)`` on success, or
    a human-readable reason string when the scan cannot proceed safely (the
    caller declines ``AMBIGUOUS``). The scanner counts square-bracket depth,
    skips ``#`` comments to end-of-line (legal inside brackets), and skips
    single-line string literals with escape handling (correct for plain, raw,
    and byte strings — a backslash always neutralizes the following quote at
    the tokenizer level). It refuses f-strings (same-quote nesting inside
    ``{...}`` is legal on 3.12+) and triple-quoted strings, where byte-level
    quote matching is not reliable.
    """
    depth = 0
    commas = 0
    has_elements = False
    i = open_off
    n = len(content)
    while i < n:
        b = content[i : i + 1]
        if b == b"[":
            depth += 1
            if i > open_off:
                has_elements = True  # a nested literal/subscript is content
            i += 1
            continue
        if b == b"]":
            depth -= 1
            if depth == 0:
                return i, commas, has_elements
            has_elements = True
            i += 1
            continue
        if b == b"#":
            newline = content.find(b"\n", i)
            i = n if newline == -1 else newline
            continue
        if b in (b"'", b'"'):
            if _string_prefix_has_f(content, i):
                return "the literal contains an f-string"
            if content[i : i + 3] in (b"'''", b'"""'):
                return "the literal contains a triple-quoted string"
            close = _scan_string(content, i)
            if close is None:
                return "the literal contains an unterminated string"
            has_elements = True
            i = close + 1
            continue
        if b == b",":
            if depth == 1:
                commas += 1
            has_elements = True
        elif b not in (b" ", b"\t", b"\r", b"\n"):
            has_elements = True
        i += 1
    return "no matching ']' found for the list literal"


def _scan_string(content: bytes, quote_off: int) -> int | None:
    """Offset of the quote closing the single-line string at ``quote_off``.

    Honors backslash escapes; returns None at a newline or end-of-file (an
    unterminated single-quoted string — invalid source, so the caller gives
    up rather than guessing).
    """
    quote = content[quote_off : quote_off + 1]
    i = quote_off + 1
    n = len(content)
    while i < n:
        b = content[i : i + 1]
        if b == b"\\":
            i += 2
            continue
        if b == quote:
            return i
        if b == b"\n":
            return None
        i += 1
    return None


def _string_prefix_has_f(content: bytes, quote_off: int) -> bool:
    """True when the string starting at ``quote_off`` carries an f/F prefix."""
    i = quote_off - 1
    prefix = b""
    while i >= 0 and content[i : i + 1].isalpha() and len(prefix) < 2:
        prefix = content[i : i + 1] + prefix
        i -= 1
    return b"f" in prefix.lower()


@dataclass(frozen=True)
class _ImportSegment:
    """One comma-separated entry on an import line.

    ``text_start``/``text_end`` are byte columns of the stripped entry text
    within the line; ``bound_name`` is the local name the entry binds (the
    alias after ``as`` when present, the first dotted segment for a plain
    ``import a.b``, the entry itself for a ``from`` import).
    """

    text_start: int
    text_end: int
    bound_name: bytes


def _import_name_segments(
    body: bytes, is_from_import: bool
) -> list[_ImportSegment] | None:
    """Split the names part of a single-line import into segments.

    ``body`` is the physical line without its newline. Returns None when the
    ``import`` keyword cannot be located (not expected for indexed import
    symbols, but anchors are verified, never assumed).
    """
    if is_from_import:
        marker = b" import "
        at = body.find(marker)
        if at == -1:
            return None
        names_start = at + len(marker)
    else:
        indent = len(body) - len(body.lstrip())
        if not body.lstrip().startswith(b"import "):
            return None
        names_start = indent + len(b"import ")

    segments: list[_ImportSegment] = []
    part_start = names_start
    for part in body[names_start:].split(b","):
        stripped = part.strip()
        text_start = part_start + (len(part) - len(part.lstrip()))
        text_end = text_start + len(stripped)
        if b" as " in stripped:
            bound = stripped.rsplit(b" as ", 1)[1].strip()
        elif is_from_import:
            bound = stripped
        else:
            bound = stripped.split(b".", 1)[0]
        segments.append(_ImportSegment(text_start, text_end, bound))
        part_start += len(part) + 1  # +1 for the comma
    return segments
