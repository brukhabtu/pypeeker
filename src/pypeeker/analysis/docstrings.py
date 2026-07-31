"""Docstring params sections: which parameter names a docstring documents.

Answers one question about indexed code — "which parameters does this
function's docstring claim to take, and how does that compare to its
signature?" — in the three common docstring styles. Extracted from
``check.builtin.docstring_drift`` (where it lived through TASK-93) so the
``docstring-drift`` rule and the planner that repairs what it flags can share
one parser: the rule lives in ``check`` and the planner in ``refactor``, and
neither package may import the other, so the shared half belongs in a package
both may import (see the ``import-boundaries`` layering).

Scope of ambition (deliberate, inherited from the rule): **parameter names
only**. Type drift, return/raises sections, multi-name numpy entries
(``x, y : int``) and exotic markup are out of scope.

Recognized params sections (autodetected per docstring — the style whose
marker appears **first** in the text wins; passing ``style`` forces one):

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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pypeeker.models import FileIndex, Symbol, SymbolKind

DOCSTRING_STYLES = ("google", "numpy", "sphinx")
"""The docstring styles :func:`parse_documented_params` can parse."""

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
class ParamsSection:
    """A recognized params section: the style that matched plus the names.

    ``names`` are the documented parameter names in document order,
    deduplicated and normalized (leading stars / escape backslashes
    stripped, so ``*args`` and ``\\*args`` both read as ``args``).
    """

    style: str
    names: tuple[str, ...]


def parse_documented_params(
    docstring: str, style: str | None = None
) -> ParamsSection | None:
    """Parse the documented parameter names out of ``docstring``.

    ``style`` forces one parser; with ``None`` the style is autodetected —
    the style whose section marker appears first in the text wins. Returns
    ``None`` when no recognizable params section exists.
    """
    if style is not None:
        names = _PARSERS[style](docstring)
        return None if names is None else ParamsSection(style, tuple(names))
    detected = _detect_style(docstring)
    if detected is None:
        return None
    names = _PARSERS[detected](docstring)
    return None if names is None else ParamsSection(detected, tuple(names))


def signature_params(file_index: FileIndex, function: Symbol) -> list[str]:
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


def param_drift(
    section: ParamsSection, signature: list[str]
) -> tuple[list[str], list[str]]:
    """(documented-but-absent, present-but-undocumented), both ordered."""
    signature_set = set(signature)
    documented_set = set(section.names)
    ghosts = [n for n in section.names if n not in signature_set]
    missing = [n for n in signature if n not in documented_set]
    return ghosts, missing


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


__all__ = [
    "DOCSTRING_STYLES",
    "ParamsSection",
    "param_drift",
    "parse_documented_params",
    "signature_params",
]
