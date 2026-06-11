"""Class hierarchy facts: resolved base classes and method override edges.

The binder records superclass identifiers as ordinary references: in
``visit_class_definition`` the ``superclasses`` field is walked *before* the
class scope is pushed, so a base-class reference lives in the class's
**parent** scope while its location falls inside the class scope's span (the
span of the whole ``class_definition`` node). No hierarchy model exists in
the index itself — this module reconstructs one after the fact.

Base-reference discriminator
----------------------------
For a class symbol ``C`` the *candidate header references* are the references
in the same file with:

- ``in_scope_id == C.parent_scope_id`` (body references sit in ``C``'s scope
  or deeper; decorators sit *outside* the ``class_definition`` span), and
- a location inside ``C``'s class-scope span, at or after the end of the
  class-name token.

Location alone cannot tell a positional base from a ``metaclass=`` keyword
value or a subscript argument (``int`` in ``Base[int]``) — all produce plain
READ references in the header. So the class header text is re-read from
source and the superclass argument list is split into top-level segments
(tracking bracket depth, strings and comments):

- keyword segments (``name=value``) are dropped — not bases;
- a segment whose leading token is a dotted name followed by nothing, or by a
  subscript (``Base[T]`` → ``Base``), contributes a base: the *last* candidate
  reference whose start falls inside that leading-name region (for ``a.b.C``
  the attribute-access reference for the full chain comes last) is resolved
  through the :class:`~pypeeker.resolve.CrossModuleResolver`; if the
  canonical id is a project ``CLASS`` symbol the base is *known*, otherwise
  it is an :class:`unknown <BaseRef>` marker (external / stdlib bases);
- anything dynamic (``*bases`` unpacking, a call like ``namedtuple(...)``, a
  conditional expression) is an unknown marker;
- if the header cannot be parsed, or candidate references exist but the
  source is unavailable, every base is an unknown marker (conservative).

Known limits, honestly: triple-quoted strings inside a class header are not
handled; ``Base[T].Inner`` is treated as ``Base``; a base that the binder
recorded no reference for becomes unknown. All failure modes degrade to
*unknown*, never to a wrong "known" edge, so consumers that treat
:meth:`Hierarchy.mro_unknown` conservatively stay safe.

Override semantics
------------------
``overrides`` / ``overridden_by`` walk the resolved base chains (depth-capped
at :data:`Hierarchy._MAX_DEPTH`, cycle-safe) and match members by name.
Name-mangled private methods (``__m`` but not dunders) never participate:
mangling makes same-named members of different classes distinct attributes.
A class whose base resolves to a project ``Protocol`` subclass gets ordinary
override edges — implementing a *project* Protocol is detected; bases that
are themselves ``typing.Protocol`` / ``abc.ABC`` are simply unknown-external.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable

from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.resolve import CrossModuleResolver

if TYPE_CHECKING:
    from pypeeker.models.index import FileIndex
    from pypeeker.models.references import Reference
    from pypeeker.storage import IndexStore

SourceReader = Callable[[str], "bytes | None"]
"""Maps a project-relative file path to its source bytes (None when unavailable)."""

_METHOD_KINDS = (SymbolKind.METHOD, SymbolKind.FUNCTION, SymbolKind.PROPERTY)

_KEYWORD_SEGMENT_RE = re.compile(rb"^[A-Za-z_]\w*\s*=(?!=)")
_LEADING_NAME_RE = re.compile(rb"^[A-Za-z_][\w.]*")


@dataclass(frozen=True)
class BaseRef:
    """One declared base of a class.

    ``class_id`` is the canonical project class symbol id, or ``None`` when
    the base is unknown (external / stdlib / dynamic / unparseable).
    ``text`` is the base expression as written, best-effort, for diagnostics.
    """

    text: str
    class_id: str | None = None

    @property
    def known(self) -> bool:
        """True when the base resolved to a project class."""
        return self.class_id is not None


class Hierarchy:
    """Queryable class-hierarchy facts built from indexes + resolver.

    Build with :meth:`build` (explicit indexes/resolver, e.g. in tests) or
    :meth:`from_store` (loads every index from an
    :class:`~pypeeker.storage.IndexStore` and reads sources from disk or the
    overlay).
    """

    _MAX_DEPTH = 32
    """Cap on inheritance-chain depth walked; deeper chains are treated as
    incomplete (``mro_unknown`` turns True) rather than walked further."""

    def __init__(
        self,
        bases: dict[str, list[BaseRef]],
        methods_by_class: dict[str, dict[str, str]],
        method_symbols: dict[str, Symbol],
    ) -> None:
        self._bases = bases
        self._methods_by_class = methods_by_class
        self._method_symbols = method_symbols
        self._children: dict[str, list[str]] = {}
        for class_id, base_refs in bases.items():
            for base in base_refs:
                if base.class_id is not None:
                    self._children.setdefault(base.class_id, []).append(class_id)
        self._ancestry_cache: dict[str, tuple[tuple[str, ...], bool]] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        indexes: Iterable[FileIndex],
        resolver: CrossModuleResolver,
        read_source: SourceReader | None = None,
    ) -> "Hierarchy":
        """Build hierarchy facts from file indexes.

        ``read_source`` supplies header text for the base-vs-keyword
        discrimination described in the module docstring; without it any
        class with header references gets only unknown bases.
        """
        indexes = list(indexes)
        symbols: dict[str, Symbol] = {}
        for index in indexes:
            for symbol in index.symbols:
                symbols[symbol.symbol_id] = symbol

        bases: dict[str, list[BaseRef]] = {}
        methods_by_class: dict[str, dict[str, str]] = {}
        method_symbols: dict[str, Symbol] = {}

        for index in indexes:
            scope_by_id = {s.scope_id: s for s in index.scopes}
            source: bytes | None = None
            source_read = False
            for symbol in index.symbols:
                if symbol.kind is not SymbolKind.CLASS:
                    continue
                # A class's scope_id equals its symbol_id.
                scope = scope_by_id.get(symbol.symbol_id)
                if scope is None:
                    bases[symbol.symbol_id] = []
                    continue
                candidates = _header_references(index, symbol, scope)
                if not candidates:
                    bases[symbol.symbol_id] = []
                    continue
                if not source_read:
                    source = read_source(index.file_path) if read_source else None
                    source_read = True
                bases[symbol.symbol_id] = _bases_for_class(
                    symbol, candidates, source, symbols, resolver
                )

            class_ids = {
                s.symbol_id for s in index.symbols if s.kind is SymbolKind.CLASS
            }
            for symbol in index.symbols:
                if (
                    symbol.kind in _METHOD_KINDS
                    and symbol.parent_scope_id in class_ids
                ):
                    methods_by_class.setdefault(symbol.parent_scope_id, {})[
                        symbol.name
                    ] = symbol.symbol_id
                    method_symbols[symbol.symbol_id] = symbol

        return cls(bases, methods_by_class, method_symbols)

    @classmethod
    def from_store(cls, store: "IndexStore") -> "Hierarchy":
        """Build from every index in a store, reading sources via the store.

        Works for both :class:`~pypeeker.storage.IndexStore` (reads from
        disk under the project root) and
        :class:`~pypeeker.storage.OverlayIndexStore` (reads through the
        overlay's ``read_file``).
        """
        indexes = []
        for file_path in store.list_indexed_files():
            index = store.load(file_path)
            if index is not None:
                indexes.append(index)
        resolver = CrossModuleResolver(indexes)

        def read_source(file_path: str) -> bytes | None:
            """Read source bytes for a project path (overlay-aware), or None."""
            try:
                reader = getattr(store, "read_file", None)
                if reader is not None:
                    return reader(file_path)
                return (store.project_root / file_path).read_bytes()
            except OSError:
                return None

        return cls.build(indexes, resolver, read_source)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def bases(self, class_id: str) -> list[BaseRef]:
        """The declared bases of ``class_id``, in declaration order."""
        return list(self._bases.get(class_id, []))

    def mro_unknown(self, class_id: str) -> bool:
        """True when the base chain of ``class_id`` is incomplete.

        Any unknown base anywhere in the chain, a declared-base cycle, a
        chain deeper than the cap, or an id this hierarchy has never seen all
        count — consumers must then be conservative.
        """
        if class_id not in self._bases:
            return True
        return self._ancestry(class_id)[1]

    def overrides(self, method_id: str) -> list[str]:
        """Ids of base-class methods that ``method_id`` overrides.

        Walks the known ancestor chain of the owning class (nearest first)
        and matches by method name. Empty for unknown ids, name-mangled
        private methods, and methods overriding nothing in project code.
        """
        symbol = self._method_symbols.get(method_id)
        if symbol is None or _is_mangled_private(symbol.name):
            return []
        ancestors, _ = self._ancestry(symbol.parent_scope_id)
        result: list[str] = []
        for ancestor_id in ancestors:
            target = self._methods_by_class.get(ancestor_id, {}).get(symbol.name)
            if target is not None:
                result.append(target)
        return result

    def overridden_by(self, method_id: str) -> list[str]:
        """Ids of subclass methods that override ``method_id``.

        Walks known subclasses of the owning class (depth-capped, cycle-safe)
        and matches by method name. Subclasses related only through unknown
        bases cannot be seen — pair with :meth:`mro_unknown` on consumers
        that must be conservative.
        """
        symbol = self._method_symbols.get(method_id)
        if symbol is None or _is_mangled_private(symbol.name):
            return []
        result: list[str] = []
        for descendant_id in self._descendants(symbol.parent_scope_id):
            target = self._methods_by_class.get(descendant_id, {}).get(symbol.name)
            if target is not None:
                result.append(target)
        return result

    # ------------------------------------------------------------------
    # Walks
    # ------------------------------------------------------------------

    def _ancestry(self, class_id: str) -> tuple[tuple[str, ...], bool]:
        """(known ancestor ids, chain-incomplete flag) for ``class_id``.

        DFS over known bases. Cycles are detected against the current path
        (so diamonds — legitimate re-visits via another path — do not trip
        it) and, like unknown bases and the depth cap, mark the chain
        incomplete.
        """
        cached = self._ancestry_cache.get(class_id)
        if cached is not None:
            return cached

        order: list[str] = []
        seen: set[str] = {class_id}
        unknown = False

        def walk(current_id: str, path: frozenset[str], depth: int) -> None:
            """Recursively walk resolved base classes, collecting method edges."""
            nonlocal unknown
            if depth >= self._MAX_DEPTH:
                unknown = True
                return
            for base in self._bases.get(current_id, ()):
                base_id = base.class_id
                if base_id is None:
                    unknown = True
                    continue
                if base_id in path:
                    unknown = True  # declared-base cycle
                    continue
                if base_id not in seen:
                    seen.add(base_id)
                    order.append(base_id)
                    walk(base_id, path | {base_id}, depth + 1)

        walk(class_id, frozenset({class_id}), 0)
        result = (tuple(order), unknown)
        self._ancestry_cache[class_id] = result
        return result

    def _descendants(self, class_id: str) -> list[str]:
        """Known transitive subclasses of ``class_id`` (depth-capped)."""
        order: list[str] = []
        seen: set[str] = {class_id}
        stack: list[tuple[str, int]] = [(class_id, 0)]
        while stack:
            current_id, depth = stack.pop()
            if depth >= self._MAX_DEPTH:
                continue
            for child_id in self._children.get(current_id, ()):
                if child_id in seen:
                    continue
                seen.add(child_id)
                order.append(child_id)
                stack.append((child_id, depth + 1))
        return order


def _is_mangled_private(name: str) -> bool:
    return name.startswith("__") and not name.endswith("__")


# ---------------------------------------------------------------------------
# Base extraction (header parsing + reference matching)
# ---------------------------------------------------------------------------


def _header_references(
    index: "FileIndex", class_symbol: Symbol, scope
) -> list["Reference"]:
    """Candidate header references for a class (see module docstring)."""
    name_end = (
        class_symbol.location.span.end.line,
        class_symbol.location.span.end.column,
    )
    scope_end = (scope.span.end.line, scope.span.end.column)
    out = []
    for ref in index.references:
        if ref.in_scope_id != class_symbol.parent_scope_id:
            continue
        start = (ref.location.span.start.line, ref.location.span.start.column)
        end = (ref.location.span.end.line, ref.location.span.end.column)
        if start >= name_end and end <= scope_end:
            out.append(ref)
    return out


def _bases_for_class(
    class_symbol: Symbol,
    candidates: list["Reference"],
    source: bytes | None,
    symbols: dict[str, Symbol],
    resolver: CrossModuleResolver,
) -> list[BaseRef]:
    """Turn a class's candidate header references into BaseRefs."""
    if source is None:
        # Header references exist but the header text can't be inspected:
        # conservative single unknown marker.
        return [BaseRef(text="<unreadable header>")]

    line_starts = _line_starts(source)
    name_end = _byte_offset(
        line_starts,
        class_symbol.location.span.end.line,
        class_symbol.location.span.end.column,
    )
    if name_end is None:
        return [BaseRef(text="<unreadable header>")]

    segments = _superclass_segments(source, name_end)
    if segments is None:
        return [BaseRef(text="<unparseable header>")]

    bases: list[BaseRef] = []
    for seg_start, seg_end in segments:
        lead_start = _skip_trivia(source, seg_start, seg_end)
        seg_text = source[lead_start:seg_end].strip()
        if not seg_text:
            continue  # comment-only segment
        if _KEYWORD_SEGMENT_RE.match(seg_text):
            continue  # metaclass=... and friends are not bases
        text = _display_text(seg_text)
        match = _LEADING_NAME_RE.match(seg_text)
        if match is None:
            bases.append(BaseRef(text=text))  # *bases, literals, ...
            continue
        lead_end = lead_start + match.end()
        rest_start = _skip_trivia(source, lead_end, seg_end)
        if rest_start < seg_end and source[rest_start:rest_start + 1] != b"[":
            # A call, conditional or other dynamic expression — unknown.
            bases.append(BaseRef(text=text))
            continue
        ref = _last_reference_in(candidates, line_starts, lead_start, lead_end)
        if ref is None:
            bases.append(BaseRef(text=text))
            continue
        canonical = resolver.resolve_reference(ref)
        target = symbols.get(canonical)
        if target is not None and target.kind is SymbolKind.CLASS:
            bases.append(BaseRef(text=text, class_id=canonical))
        else:
            bases.append(BaseRef(text=text))
    return bases


def _last_reference_in(
    candidates: list["Reference"],
    line_starts: list[int],
    start: int,
    end: int,
) -> "Reference | None":
    """The candidate ref starting latest within ``[start, end)``, if any.

    For a dotted base ``a.b.C`` the receiver-root ref (``a``) and the
    attribute-access ref (``.C``, carrying the receiver chain) both fall in
    the leading-name region; the latest one represents the full expression.
    """
    best: "Reference | None" = None
    best_offset = -1
    for ref in candidates:
        offset = _byte_offset(
            line_starts,
            ref.location.span.start.line,
            ref.location.span.start.column,
        )
        if offset is None or not (start <= offset < end):
            continue
        if offset > best_offset:
            best = ref
            best_offset = offset
    return best


def _line_starts(source: bytes) -> list[int]:
    starts = [0]
    for i, byte in enumerate(source):
        if byte == 0x0A:
            starts.append(i + 1)
    return starts


def _byte_offset(line_starts: list[int], line: int, column: int) -> int | None:
    """0-indexed (line, byte-column) to absolute byte offset, None if out of range."""
    if line < 0 or line >= len(line_starts):
        return None
    return line_starts[line] + column


def _superclass_segments(
    source: bytes, scan_from: int
) -> "list[tuple[int, int]] | None":
    """Top-level argument segments of a class header's superclass list.

    Scans from the end of the class-name token to the opening ``(`` of the
    superclass list (skipping PEP 695 type-parameter brackets), then splits
    the argument list at top-level commas, tracking bracket depth, strings
    and comments. Returns ``(start_offset, stripped_text)`` pairs, ``[]``
    when the class declares no superclass list, or ``None`` when the header
    could not be parsed. Segments are raw ``(start, end)`` byte ranges and
    may still contain leading/trailing trivia (whitespace, comments).
    """
    n = len(source)
    i = scan_from
    depth = 0
    open_paren = -1
    while i < n:
        c = source[i:i + 1]
        if c == b"#":
            i = _skip_comment(source, i)
            continue
        if c in (b"'", b'"'):
            i = _skip_string(source, i)
            if i < 0:
                return None
            continue
        if c == b"(" and depth == 0:
            open_paren = i
            break
        if c in b"([{":
            depth += 1
        elif c in b")]}":
            depth -= 1
        elif c == b":" and depth == 0:
            return []  # no superclass list
        i += 1
    if open_paren < 0:
        return [] if i >= n else None

    segments: list[tuple[int, int]] = []
    seg_start = open_paren + 1
    depth = 0
    i = open_paren + 1
    while i < n:
        c = source[i:i + 1]
        if c == b"#":
            i = _skip_comment(source, i)
            continue
        if c in (b"'", b'"'):
            i = _skip_string(source, i)
            if i < 0:
                return None
            continue
        if c == b")" and depth == 0:
            _append_segment(segments, source, seg_start, i)
            return segments
        if c in b"([{":
            depth += 1
        elif c in b")]}":
            depth -= 1
        elif c == b"," and depth == 0:
            _append_segment(segments, source, seg_start, i)
            seg_start = i + 1
        i += 1
    return None  # unterminated header


def _append_segment(
    segments: list[tuple[int, int]], source: bytes, start: int, end: int
) -> None:
    if not source[start:end].strip():
        return  # empty (e.g. trailing comma)
    segments.append((start, end))


def _skip_trivia(source: bytes, i: int, end: int) -> int:
    """Offset of the first non-whitespace, non-comment byte in ``[i, end)``."""
    while i < end:
        c = source[i:i + 1]
        if c == b"#":
            i = _skip_comment(source, i)
        elif c.isspace() or c == b"\\":
            i += 1
        else:
            break
    return i


def _display_text(segment: bytes) -> str:
    """Comment-free, whitespace-collapsed segment text for diagnostics."""
    cleaned = re.sub(rb"#[^\n]*", b"", segment)
    return b" ".join(cleaned.split()).decode("utf-8", errors="replace")


def _skip_comment(source: bytes, i: int) -> int:
    """Offset just past the comment starting at ``i`` (to end of line)."""
    end = source.find(b"\n", i)
    return len(source) if end < 0 else end + 1


def _skip_string(source: bytes, i: int) -> int:
    """Offset just past the single-quoted string starting at ``i``, -1 on failure.

    Handles backslash escapes; does not handle triple-quoted strings (absurd
    in a class header — the caller degrades to "unparseable" / unknown).
    """
    quote = source[i:i + 1]
    if source[i:i + 3] == quote * 3:
        return -1  # triple-quoted: give up, caller treats header as unparseable
    j = i + 1
    n = len(source)
    while j < n:
        c = source[j:j + 1]
        if c == b"\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == b"\n":
            return -1  # unterminated
        j += 1
    return -1
