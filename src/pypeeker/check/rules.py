"""Linter rule implementations.

Each rule is a callable ``(FileIndex, options) -> list[Violation]``. The index
stores 0-indexed line numbers (matching tree-sitter); we emit 1-indexed lines
in violations to match ruff/mypy convention.
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
    """Flag symbols whose ``docstring`` is None and whose kind+visibility match.

    Options:
        ``kinds``      — list of SymbolKind values (default function/method/class)
        ``visibility`` — list of Visibility values (default public only)
    """
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
        violations.append(
            Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=REQUIRE_DOCSTRINGS,
                message=(
                    f"{symbol.visibility.value} {symbol.kind.value} "
                    f"'{symbol.name}' has no docstring"
                ),
            )
        )
    return violations


def no_unresolved_refs(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag references that the binder couldn't resolve.

    Skips ``<unresolved>.*`` symbol_ids — those are attribute chains on a
    receiver we know is unresolved, which is a different (and noisier)
    concern. Builtins land on ``<builtins>.*`` with resolved=True so they
    are naturally excluded.
    """
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
    values = [raw] if isinstance(raw, str) else list(raw)
    out = set()
    for v in values:
        try:
            out.add(enum_cls(v))
        except ValueError:
            continue
    return frozenset(out)
