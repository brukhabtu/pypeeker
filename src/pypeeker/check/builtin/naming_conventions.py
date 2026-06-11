"""Builtin rule: naming-conventions (advisory, opt-in).

PEP 8 naming, detection only: ``snake_case`` for functions and methods,
``PascalCase`` for classes, checked per file from each symbol's kind + name —
no resolution needed. ``variable`` / ``parameter`` checks exist but are OFF
by default (locals and parameters mirror external APIs often enough to be
noisy); opt in via the ``kinds`` option. Each finding embeds the symbol id
and a suggested conforming name, so a converter
(:func:`pypeeker.refactor.convention_renames.convention_rename_intents`) can
turn findings into batch rename intents — the *fix* for a naming violation is
a cross-module rename, far beyond the per-file Fix protocol. Use
:func:`rename_pair` to extract the ``(symbol_id, suggested_name)`` pair from
a finding.

Tolerances (never flagged):

* dunder names (``__init__``) — protocol surface, not style;
* names that are *only* underscores (``_``, ``__``);
* leading underscores are stripped before matching and preserved in the
  suggestion (``_helperName`` suggests ``_helper_name``), so visibility
  prefixes never count as violations;
* module-level VARIABLEs in ``UPPER_SNAKE_CASE`` — the default ``variable``
  convention accepts both ``snake_case`` and ``UPPER_SNAKE_CASE`` because
  constants are not statically distinguishable from variables in v1
  (a dedicated UPPER_SNAKE-for-constants check is future work).

Suggested-name edge cases (see :func:`to_snake_case` / :func:`to_pascal_case`):
consecutive capitals split before the last (``HTTPServer`` -> ``http_server``),
digits stick to the preceding word (``parseHTML2Text`` -> ``parse_html2_text``),
underscore runs collapse (``get_Value`` -> ``get_value``). With a custom
``conventions`` regex the suggestion still comes from the kind's default
converter, so it is best-effort; when no different name can be suggested the
message carries no suggestion and :func:`rename_pair` returns ``None``.

Options (``[tool.pypeeker.naming-conventions]``):
    ``kinds``       — symbol kinds to check, from function / method / class /
                      property / variable / parameter
                      (default function + method + class).
    ``conventions`` — mapping of kind -> regex overriding that kind's default
                      pattern (matched against the underscore-stripped name);
                      invalid regexes are ignored (the default is kept).
    ``allow``       — fnmatch patterns matched against the symbol's bare
                      name, its symbol_id, or its module path; matches are
                      never flagged.

Advisory and **opt-in** (not enabled by default): naming is a convention,
not a defect, and existing codebases interoperating with camelCase APIs
(Qt, unittest, protocols) would drown in findings.

Import discipline: this module imports only concrete ``pypeeker.check.*``
modules — importing ``pypeeker.check`` itself from a builtin rule module
recurses into the engine import and creates a cycle.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models.index import FileIndex
from pypeeker.models.symbol_id import module_of
from pypeeker.models.symbols import SymbolKind

NAMING_CONVENTIONS = "naming-conventions"

_DEFAULT_KINDS: tuple[str, ...] = ("function", "method", "class")
"""Kinds checked by default; variable/parameter are opt-in (noisy)."""

_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_SNAKE_OR_UPPER_RE = re.compile(r"^(?:[a-z][a-z0-9_]*|[A-Z][A-Z0-9_]*)$")

_CAMEL_BOUNDARY_BEFORE_WORD = re.compile(r"(.)([A-Z][a-z]+)")
"""A capitalized word following anything: the last capital of an acronym run
(``HTTPServer`` -> split before ``Server``)."""

_CAMEL_BOUNDARY_AFTER_LOWER = re.compile(r"([a-z0-9])([A-Z])")
"""A capital following a lowercase letter or digit (``getValue``)."""


def _to_snake_case(name: str) -> str:
    """Best-effort ``snake_case`` form of a camelCase/PascalCase ``name``.

    Edge cases, documented because the converter feeds rename suggestions:

    * consecutive capitals split before the last one of the run, so acronyms
      stay whole: ``HTTPServer`` -> ``http_server``, ``getHTTPResponse`` ->
      ``get_http_response``;
    * digits stick to the word they follow: ``parseHTML2Text`` ->
      ``parse_html2_text``, ``getHTTP2`` -> ``get_http2``;
    * underscore runs produced by mixed inputs collapse to one
      (``get_Value`` -> ``get_value``);
    * already-snake names pass through unchanged.

    Leading underscores are the *caller's* concern: the rule strips them
    before suggesting and re-attaches them afterwards.
    """
    s = _CAMEL_BOUNDARY_BEFORE_WORD.sub(r"\1_\2", name)
    s = _CAMEL_BOUNDARY_AFTER_LOWER.sub(r"\1_\2", s)
    return re.sub(r"_+", "_", s).lower()


def _to_pascal_case(name: str) -> str:
    """Best-effort ``PascalCase`` form of a snake_case/camelCase ``name``.

    Splits on underscores and upper-cases the first letter of each part,
    leaving the rest of the part untouched — so acronym parts survive
    (``HTTP_server`` -> ``HTTPServer``) while ordinary parts capitalize
    (``bad_class`` -> ``BadClass``); a camelCase input simply gets its first
    letter capitalized (``badClass`` -> ``BadClass``). Parts starting with a
    digit are kept as-is (``foo_2d`` -> ``Foo2d``). Like
    :func:`to_snake_case`, leading underscores are the caller's concern.
    """
    parts = [part for part in name.split("_") if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


@dataclass(frozen=True)
class _Convention:
    """One kind's naming contract: a label, a pattern, and a suggester."""

    label: str
    pattern: re.Pattern[str]
    suggest: Callable[[str], str]


_DEFAULT_CONVENTIONS: dict[SymbolKind, _Convention] = {
    SymbolKind.FUNCTION: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.METHOD: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.PROPERTY: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.PARAMETER: _Convention("snake_case", _SNAKE_RE, _to_snake_case),
    SymbolKind.VARIABLE: _Convention(
        # Constants are indistinguishable from variables in v1 (see module
        # docstring), so UPPER_SNAKE is tolerated rather than mis-flagged.
        "snake_case (or UPPER_SNAKE_CASE)",
        _SNAKE_OR_UPPER_RE,
        _to_snake_case,
    ),
    SymbolKind.CLASS: _Convention("PascalCase", _PASCAL_RE, _to_pascal_case),
}

_KIND_CHOICES = frozenset(_DEFAULT_CONVENTIONS)
"""Kinds the ``kinds`` option may select; anything else is ignored."""

_MESSAGE_RE = re.compile(
    r"^\S+ '(?P<symbol_id>[^']+)' does not match the .+ naming convention"
    r" — suggested name: '(?P<suggestion>[^']+)'$"
)
"""Parser for the finding message; must mirror the format in the rule body."""


def _rename_pair(violation: Violation) -> tuple[str, str] | None:
    """The ``(symbol_id, suggested_name)`` pair carried by a finding, or None.

    This is the handoff to the rename converter
    (:func:`pypeeker.refactor.convention_renames.convention_rename_intents`):
    ``check`` may not be imported from ``refactor``, so the converter takes
    plain pairs and the *caller* (tests, a CLI follow-up) extracts them from
    violations with this helper. Returns ``None`` for violations of other
    rules and for findings without a suggestion (the converter could not
    produce a different conforming name).
    """
    if violation.rule != NAMING_CONVENTIONS:
        return None
    match = _MESSAGE_RE.match(violation.message)
    if match is None:
        return None
    return match.group("symbol_id"), match.group("suggestion")


def _as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings ('' / None / [] -> [])."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


def _selected_kinds(raw: Any) -> frozenset[SymbolKind]:
    """Coerce the ``kinds`` option to SymbolKinds; unknown values are ignored."""
    out: set[SymbolKind] = set()
    for value in _as_str_list(raw) or list(_DEFAULT_KINDS):
        try:
            kind = SymbolKind(value)
        except ValueError:
            continue
        if kind in _KIND_CHOICES:
            out.add(kind)
    return frozenset(out)


def _configured_conventions(raw: Any) -> dict[SymbolKind, _Convention]:
    """Defaults overlaid with the ``conventions`` option's per-kind regexes.

    An override keeps the kind's default *suggester* (the regex only redefines
    what conforms, not what shape to convert to). Unknown kinds and invalid
    regexes are ignored, matching the option-coercion style of the sibling
    rules (silently conservative rather than crashing the check run).
    """
    conventions = dict(_DEFAULT_CONVENTIONS)
    if not isinstance(raw, Mapping):
        return conventions
    for key, value in raw.items():
        try:
            kind = SymbolKind(str(key))
            pattern = re.compile(str(value))
        except (ValueError, re.error):
            continue
        if kind in _KIND_CHOICES:
            conventions[kind] = _Convention(
                f"pattern '{value}'", pattern, conventions[kind].suggest
            )
    return conventions


def _allowed(symbol_name: str, symbol_id: str, patterns: list[str]) -> bool:
    """True when any ``allow`` pattern matches the name, id, or module path."""
    module_path = module_of(symbol_id)
    return any(
        fnmatch.fnmatchcase(symbol_name, pattern)
        or fnmatch.fnmatchcase(symbol_id, pattern)
        or fnmatch.fnmatchcase(module_path, pattern)
        for pattern in patterns
    )


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) > 4


@register_rule(NAMING_CONVENTIONS)
def _naming_conventions(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag symbols whose name violates their kind's naming convention.

    Per-file and resolution-free: kind + name is all the evidence needed.
    See the module docstring for defaults, tolerances, options, and the
    finding-to-rename handoff (:func:`rename_pair`).
    """
    kinds = _selected_kinds(options.get("kinds"))
    conventions = _configured_conventions(options.get("conventions"))
    allow = _as_str_list(options.get("allow"))

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind not in kinds:
            continue
        name = symbol.name
        if _is_dunder(name):
            continue
        stripped = name.lstrip("_")
        if not stripped:
            continue  # underscore-only names (_, __) are idiomatic
        convention = conventions[symbol.kind]
        if convention.pattern.match(stripped):
            continue
        if _allowed(name, symbol.symbol_id, allow):
            continue
        prefix = name[: len(name) - len(stripped)]
        suggestion = convention.suggest(stripped)
        message = (
            f"{symbol.kind.value} '{symbol.symbol_id}' does not match the "
            f"{convention.label} naming convention"
        )
        if suggestion and suggestion != stripped:
            message += f" — suggested name: '{prefix}{suggestion}'"
        violations.append(
            Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=NAMING_CONVENTIONS,
                message=message,
            )
        )
    return violations
