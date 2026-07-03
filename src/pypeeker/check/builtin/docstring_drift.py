"""docstring-drift: documented parameters vs the actual signature (TASK-93).

A docstring that documents a parameter the function no longer has (or has
under a different name) is actively misleading — worse than no docstring.
darglint is unmaintained and ruff's coverage of this is shallow, so this rule
closes the gap from the index: every FUNCTION/METHOD symbol carries its
``docstring`` and its parameters are PARAMETER symbols whose
``parent_scope_id`` is the function's scope, which is all the rule needs.

Scope of ambition (deliberate): **parameter-name drift only**, in the three
common docstring styles. Type drift, return/raises sections, multi-name numpy
entries (``x, y : int``) and exotic markup are out of scope.

Recognized params sections (autodetected per docstring — the style whose
marker appears **first** in the text wins; the ``style`` option forces one):

* **google** — an ``Args:`` (or ``Arguments:``) header followed by indented
  ``name: description`` / ``name (type): description`` entries; deeper-indented
  lines are continuations. A blank line or a dedent ends the section.
* **numpy** — a ``Parameters`` header underlined with dashes; entry names sit
  at the header's indent, optionally followed by ``: type``. A blank line, a
  dedent, or the next underlined header ends the section.
* **sphinx** — ``:param name:`` / ``:param type name:`` field lines anywhere
  in the docstring.

Documented ``*args`` / ``**kwargs`` are normalized to their bare names (the
index stores parameters without stars), so documenting them with or without
stars matches either way.

Two violation kinds:

* documented-but-absent — the docstring documents a parameter the signature
  does not have. Always reported when a params section is recognized.
* present-but-undocumented — a signature parameter missing from a recognized
  params section. Gated by ``require-complete`` (default false), and ONLY
  emitted when a params section exists: demanding sections where none exist
  is ``require-docstrings``' turf, not drift.

``self``/``cls`` as the leading parameter is never expected in a docstring
and is skipped on the signature side.

The repair (conservative): when exactly ONE documented name is absent from
the signature and exactly ONE signature parameter is undocumented — the shape
of "the parameter was renamed and the docstring did not follow" — the
documented-but-absent violation carries a :class:`DocstringParamRenameFix`
that rewrites just that name token inside the docstring. Anything ambiguous
(zero or several undocumented parameters, several stale names, several token
occurrences inside the docstring, a docstring that no longer parses in the
detected style) is report-only / declines at plan time.

Options (``[tool.pypeeker.docstring-drift]``):
    ``style``            — force one of ``google`` / ``numpy`` / ``sphinx``
                           instead of autodetecting (unknown values fall back
                           to autodetect).
    ``require-complete`` — also flag signature parameters missing from an
                           existing params section. Default false.
    ``allow``            — fnmatch patterns matched against the function's
                           ``symbol_id`` (``"pkg.mod:func"``) or its module
                           path; matching functions are never flagged.

Advisory and **opt-in** (not enabled by default): docstring conventions vary
per project, and the parsers cover the common shapes, not every dialect.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses into
the engine import and creates a cycle.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pypeeker.check.fixes import (
    DeclineReason,
    FixDeclined,
    FixPlan,
    _current_state,
    _line_start_offsets,
    with_fix,
)
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models import EditEntry, EditOp, FileIndex, Symbol, SymbolKind
from pypeeker.storage.index_store import IndexStore

DOCSTRING_DRIFT = "docstring-drift"

_STYLES = ("google", "numpy", "sphinx")

# google: an "Args:"/"Arguments:" header line on its own.
_GOOGLE_HEADER = re.compile(r"(?m)^[ \t]*(?:Args|Arguments):[ \t]*$")
# google entry: "name: desc" or "name (type): desc", stars allowed.
_GOOGLE_ENTRY = re.compile(r"(\*{0,2}[A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:")
# numpy: a "Parameters" header underlined with dashes on the next line.
_NUMPY_HEADER = re.compile(r"(?m)^[ \t]*Parameters[ \t]*\r?\n[ \t]*-{3,}[ \t]*$")
# numpy entry: "name" or "name : type" at the section margin, stars allowed.
_NUMPY_ENTRY = re.compile(r"(\*{0,2}[A-Za-z_]\w*)\s*(?::.*)?$")
# sphinx: ":param name:" / ":param type name:" field lines.
_SPHINX_PARAM = re.compile(r"(?m)^[ \t]*:param\s+([^:\n]+):")

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*\Z")


@dataclass(frozen=True)
class _ParamsSection:
    """A recognized params section: the style that matched plus the names.

    ``names`` are the documented parameter names in document order,
    deduplicated and normalized (leading stars / escape backslashes
    stripped, so ``*args`` and ``\\*args`` both read as ``args``).
    """

    style: str
    names: tuple[str, ...]


def _parse_documented_params(
    docstring: str, style: str | None = None
) -> _ParamsSection | None:
    """Parse the documented parameter names out of ``docstring``.

    ``style`` forces one parser; with ``None`` the style is autodetected —
    the style whose section marker appears first in the text wins. Returns
    ``None`` when no recognizable params section exists.
    """
    if style is not None:
        names = _PARSERS[style](docstring)
        return None if names is None else _ParamsSection(style, tuple(names))
    detected = _detect_style(docstring)
    if detected is None:
        return None
    names = _PARSERS[detected](docstring)
    return None if names is None else _ParamsSection(detected, tuple(names))


def _detect_style(docstring: str) -> str | None:
    """The style whose section marker appears first in the text, or None."""
    candidates: list[tuple[int, str]] = []
    for name, marker in (
        ("google", _GOOGLE_HEADER),
        ("numpy", _NUMPY_HEADER),
        ("sphinx", _SPHINX_PARAM),
    ):
        match = marker.search(docstring)
        if match is not None:
            candidates.append((match.start(), name))
    if not candidates:
        return None
    return min(candidates)[1]


def _indent(line: str) -> int:
    """Leading whitespace width of ``line`` (tabs count as one column)."""
    return len(line) - len(line.lstrip())


def _normalize(name: str) -> str:
    """Strip leading stars / escape backslashes: ``\\**kwargs`` -> ``kwargs``."""
    return name.lstrip("*\\")


def _dedupe(names: list[str]) -> list[str]:
    """Order-preserving deduplication."""
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _parse_google(docstring: str) -> list[str] | None:
    """Documented names from a google-style ``Args:`` section, or None."""
    lines = docstring.splitlines()
    for i, line in enumerate(lines):
        if not _GOOGLE_HEADER.fullmatch(line):
            continue
        header_indent = _indent(line)
        names: list[str] = []
        entry_indent: int | None = None
        for raw in lines[i + 1 :]:
            if not raw.strip():
                break  # blank line ends the section
            indent = _indent(raw)
            if indent <= header_indent:
                break  # dedent: the next section starts
            if entry_indent is None:
                entry_indent = indent
            if indent > entry_indent:
                continue  # continuation of the previous entry's description
            match = _GOOGLE_ENTRY.match(raw.strip())
            if match:
                names.append(_normalize(match.group(1)))
        return _dedupe(names)
    return None


def _parse_numpy(docstring: str) -> list[str] | None:
    """Documented names from a numpy-style ``Parameters`` section, or None."""
    lines = docstring.splitlines()
    for i in range(len(lines) - 1):
        if lines[i].strip() != "Parameters":
            continue
        underline = lines[i + 1].strip()
        if not underline or set(underline) != {"-"}:
            continue
        base_indent = _indent(lines[i])
        names: list[str] = []
        for j in range(i + 2, len(lines)):
            raw = lines[j]
            if not raw.strip():
                break  # blank line ends the section
            indent = _indent(raw)
            if indent < base_indent:
                break
            if indent == base_indent:
                following = lines[j + 1].strip() if j + 1 < len(lines) else ""
                if following and set(following) == {"-"}:
                    break  # the next underlined header starts here
                match = _NUMPY_ENTRY.fullmatch(raw.strip())
                if match:
                    names.append(_normalize(match.group(1)))
        return _dedupe(names)
    return None


def _parse_sphinx(docstring: str) -> list[str] | None:
    """Documented names from ``:param ...:`` field lines, or None."""
    names: list[str] = []
    found = False
    for match in _SPHINX_PARAM.finditer(docstring):
        found = True
        # ":param type name:" — the name is the last whitespace-separated
        # token of the field head.
        token = _normalize(match.group(1).strip().split()[-1])
        if _IDENTIFIER.fullmatch(token):
            names.append(token)
    return _dedupe(names) if found else None


_PARSERS = {
    "google": _parse_google,
    "numpy": _parse_numpy,
    "sphinx": _parse_sphinx,
}


def _signature_params(file_index: FileIndex, function: Symbol) -> list[str]:
    """The function's parameter names in declaration order, sans self/cls.

    Parameters are PARAMETER symbols whose ``parent_scope_id`` is the
    function's scope id (== its symbol_id). A leading ``self``/``cls`` is
    dropped: it is never expected in a params section.
    """
    names = [
        s.name
        for s in file_index.symbols
        if s.kind is SymbolKind.PARAMETER
        and s.parent_scope_id == function.symbol_id
    ]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return names


def _drift(
    section: _ParamsSection, signature: list[str]
) -> tuple[list[str], list[str]]:
    """(documented-but-absent, present-but-undocumented), both ordered."""
    signature_set = set(signature)
    documented_set = set(section.names)
    ghosts = [n for n in section.names if n not in signature_set]
    missing = [n for n in signature if n not in documented_set]
    return ghosts, missing


@register_rule(DOCSTRING_DRIFT, scope="file")
def _docstring_drift(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag docstring params sections that drifted from the signature.

    See the module docstring for the recognized styles, the two violation
    kinds, the repair conditions, and the options.
    """
    style_opt = options.get("style")
    style = style_opt if style_opt in _STYLES else None
    require_complete = bool(options.get("require-complete"))
    allow = _as_str_list(options.get("allow"))

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            continue
        if not symbol.docstring:
            continue
        if _matches_any(symbol.symbol_id, allow):
            continue
        section = _parse_documented_params(symbol.docstring, style)
        if section is None:
            continue  # no params section: require-docstrings' turf, not drift
        signature = _signature_params(file_index, symbol)
        ghosts, missing = _drift(section, signature)

        # The repair applies only to the unambiguous rename shape: one stale
        # documented name, one undocumented signature parameter.
        renameable = len(ghosts) == 1 and len(missing) == 1

        for ghost in ghosts:
            violation = Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=DOCSTRING_DRIFT,
                message=(
                    f"docstring of {symbol.kind.value} '{symbol.name}' "
                    f"documents parameter '{ghost}' which does not exist"
                ),
            )
            if renameable:
                violation = with_fix(
                    violation,
                    _DocstringParamRenameFix(
                        file_path=symbol.location.file_path,
                        symbol_id=symbol.symbol_id,
                        old_param=ghost,
                        new_param=missing[0],
                        style=section.style,
                    ),
                )
            violations.append(violation)

        if require_complete:
            for name in missing:
                violations.append(
                    Violation(
                        file_path=symbol.location.file_path,
                        line=symbol.location.span.start.line + 1,
                        rule=DOCSTRING_DRIFT,
                        message=(
                            f"docstring of {symbol.kind.value} "
                            f"'{symbol.name}' does not document parameter "
                            f"'{name}'"
                        ),
                    )
                )
    return sorted(violations)


@dataclass(frozen=True)
class _DocstringParamRenameFix:
    """Rewrite one stale documented parameter name inside a docstring.

    Anchored on the FUNCTION/METHOD symbol id plus the detected docstring
    style. ``plan()`` follows the index-anchored fix discipline (see
    ``pypeeker.check.fixes``): it re-reads the file, refuses on a stale index
    (``STALE_INDEX``), re-locates the symbol and re-derives the drift from
    the CURRENT docstring — never from detection-time offsets — and proceeds
    only while the rename shape still holds: exactly one documented-but-absent
    name (``old_param``) and exactly one undocumented signature parameter
    (``new_param``).

    The docstring region is re-located textually: the indexed docstring text
    (the first string in the def body, triple-quote-stripped) must occur
    exactly once inside the function's scope span, and ``old_param`` must
    occur exactly once as a bare name token inside that region (not preceded
    by ``*`` or a word character). One REPLACE edit covering just the name
    token is emitted; every other case declines (``AMBIGUOUS`` for plural
    candidates, ``TEXT_MISMATCH`` when the anchor is gone).
    """

    file_path: str  # project-root-relative, like Violation.file_path
    symbol_id: str  # the FUNCTION/METHOD whose docstring drifted
    old_param: str  # documented name absent from the signature
    new_param: str  # the single undocumented signature parameter
    style: str  # docstring style detected (or forced) at detection time

    @property
    def fix_id(self) -> str:
        """Stable id: ``docstring-drift:rename-param:<symbol_id>:<old>``."""
        return f"docstring-drift:rename-param:{self.symbol_id}:{self.old_param}"

    @property
    def description(self) -> str:
        """One-line summary of the rewrite."""
        return (
            f"rename documented parameter '{self.old_param}' to "
            f"'{self.new_param}' in the docstring of '{self.symbol_id}'"
        )

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Re-derive the drift from the current index and rewrite the token."""
        state = _current_state(store, self.file_path, self.fix_id)
        if isinstance(state, FixDeclined):
            return state
        content, index = state

        symbol = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == self.symbol_id
                and s.kind in (SymbolKind.FUNCTION, SymbolKind.METHOD)
            ),
            None,
        )
        if symbol is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"function '{self.symbol_id}' is no longer in the index",
            )
        if not symbol.docstring:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"'{self.symbol_id}' no longer has a docstring",
            )
        section = _parse_documented_params(symbol.docstring, self.style)
        if section is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"the docstring no longer has a {self.style}-style params section",
            )
        ghosts, missing = _drift(section, _signature_params(index, symbol))
        if len(ghosts) != 1 or len(missing) != 1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                "the drift is no longer a single-parameter rename "
                f"({len(ghosts)} stale documented name(s), "
                f"{len(missing)} undocumented parameter(s))",
            )
        if ghosts[0] != self.old_param or missing[0] != self.new_param:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"the drift changed: '{ghosts[0]}' -> '{missing[0]}' rather "
                f"than '{self.old_param}' -> '{self.new_param}'",
            )

        region = self._docstring_region(content, index, symbol.docstring)
        if isinstance(region, FixDeclined):
            return region
        doc_start, doc_bytes = region

        token = re.compile(
            rb"(?<![\w*])" + re.escape(self.old_param.encode("utf-8")) + rb"(?!\w)"
        )
        matches = list(token.finditer(doc_bytes))
        if not matches:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"'{self.old_param}' does not occur as a name token in the docstring",
            )
        if len(matches) > 1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"'{self.old_param}' occurs {len(matches)} times in the "
                "docstring; rewriting one occurrence is unsafe",
            )
        start = doc_start + matches[0].start()
        edit = EditEntry(
            op=EditOp.REPLACE,
            file=self.file_path,
            start=start,
            end=start + len(self.old_param.encode("utf-8")),
            old=self.old_param,
            new=self.new_param,
            file_hash=IndexStore.compute_file_hash(
                store.project_root / self.file_path
            ),
        )
        return FixPlan(self.fix_id, self.description, [edit])

    def _docstring_region(
        self, content: bytes, index: FileIndex, docstring: str
    ) -> tuple[int, bytes] | FixDeclined:
        """(byte offset, bytes) of the docstring text inside the scope span.

        The indexed docstring is the first string in the def body with its
        quotes stripped, so its text appears verbatim in the file; it must
        occur exactly once within the function's scope span to anchor safely.
        """
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
        region_start = line_starts[scope.span.start.line]
        region_end = line_starts[scope.span.end.line] + scope.span.end.column
        region = content[region_start:region_end]

        # The indexed docstring is verified against current bytes by
        # _current_state's hash check; this find re-anchors it to an offset.
        doc_bytes = docstring.encode("utf-8")
        first = region.find(doc_bytes)
        if first == -1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "the docstring text was not found inside the function body",
            )
        if region.find(doc_bytes, first + 1) != -1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                "the docstring text occurs more than once inside the "
                "function body",
            )
        return region_start + first, content[
            region_start + first : region_start + first + len(doc_bytes)
        ]


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


def _matches_any(symbol_id: str, patterns: list[str]) -> bool:
    """True when any fnmatch pattern matches the symbol_id or its module path."""
    module_path = symbol_id.split(":", 1)[0]
    return any(
        fnmatch.fnmatchcase(symbol_id, pattern)
        or fnmatch.fnmatchcase(module_path, pattern)
        for pattern in patterns
    )
