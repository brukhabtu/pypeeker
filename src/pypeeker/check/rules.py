"""Linter rule implementations.

Each rule is a function ``(FileIndex, options) -> list[Violation]``. Lines in
the index are 0-indexed; we emit 1-indexed line numbers in violations to match
ruff/mypy conventions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pypeeker.check.models import Violation
from pypeeker.models.index import FileIndex
from pypeeker.models.symbols import SymbolKind, Visibility

Rule = Callable[[FileIndex, Mapping[str, Any]], list[Violation]]


REQUIRE_DOCSTRINGS = "require-docstrings"
NO_UNRESOLVED_REFS = "no-unresolved-refs"

_DOCSTRING_KINDS_DEFAULT: tuple[str, ...] = ("function", "method", "class")
_DOCSTRING_VISIBILITY_DEFAULT: tuple[str, ...] = ("public",)


def require_docstrings(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    kinds = _as_enum_set(
        options.get("kinds", _DOCSTRING_KINDS_DEFAULT), SymbolKind
    )
    visibilities = _as_enum_set(
        options.get("visibility", _DOCSTRING_VISIBILITY_DEFAULT), Visibility
    )

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind not in kinds:
            continue
        if symbol.visibility not in visibilities:
            continue
        if symbol.docstring is not None:
            continue
        message = (
            f"{symbol.visibility.value} {symbol.kind.value} "
            f"'{symbol.name}' has no docstring"
        )
        violations.append(
            Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=REQUIRE_DOCSTRINGS,
                message=message,
            )
        )
    return violations


def no_unresolved_refs(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    violations: list[Violation] = []
    for ref in file_index.references:
        if ref.resolved:
            continue
        if ref.symbol_id.startswith("<unresolved>."):
            continue
        violations.append(
            Violation(
                file_path=ref.location.file_path,
                line=ref.location.span.start.line + 1,
                rule=NO_UNRESOLVED_REFS,
                message=f"unresolved reference: '{ref.symbol_id}'",
            )
        )
    return violations


REGISTRY: dict[str, Rule] = {
    REQUIRE_DOCSTRINGS: require_docstrings,
    NO_UNRESOLVED_REFS: no_unresolved_refs,
}


def _as_enum_set(raw: Any, enum_cls: type) -> frozenset:
    if isinstance(raw, str):
        values = [raw]
    else:
        values = list(raw)
    out = set()
    for v in values:
        try:
            out.add(enum_cls(v))
        except ValueError:
            continue
    return frozenset(out)
