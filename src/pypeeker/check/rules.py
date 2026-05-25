"""Linter rule implementations.

Each rule is a callable ``(FileIndex, options) -> list[Violation]``. The index
stores 0-indexed line numbers (matching tree-sitter); we emit 1-indexed lines
in violations to match ruff/mypy convention.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pypeeker.check.models import Violation
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbols import SymbolKind, Visibility

Rule = Callable[[FileIndex, Mapping[str, Any]], list[Violation]]

REQUIRE_DOCSTRINGS = "require-docstrings"
NO_UNRESOLVED_REFS = "no-unresolved-refs"
IMPORT_BOUNDARIES = "import-boundaries"
PREFER_TUPLE = "prefer-tuple"

# Methods that mutate a list in place. A list-literal local touched by none of
# these (and never subscript-written) is a tuple candidate.
_LIST_MUTATORS: frozenset[str] = frozenset({
    "append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse",
})

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


def import_boundaries(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag internal imports that cross a forbidden package boundary.

    Enforces declared layering. Each file's package is the first segment under
    the project root of its module path; an import's package is derived the
    same way from ``imported_from``. An import is flagged when the importing
    package is listed in ``allow`` and the imported package is neither the same
    package nor in that package's allow-list.

    Options:
        ``allow`` — mapping of package -> list of packages it may import.
                    Packages absent from the mapping are unconstrained.
        ``root``  — project root package (dotted prefix). Defaults to the first
                    segment of the importing module's path.

    External imports (under a different root) and same-package imports are
    never flagged.
    """
    allow_raw = options.get("allow")
    if not isinstance(allow_raw, Mapping) or not allow_raw:
        return []
    allow = {pkg: set(deps) for pkg, deps in allow_raw.items()}

    module_path = next(
        (s.symbol_id for s in file_index.symbols if s.kind == SymbolKind.MODULE),
        None,
    )
    if module_path is None:
        return []

    root = options.get("root") or module_path.split(".")[0]
    importer_pkg = _package_under(module_path, root)
    if importer_pkg is None or importer_pkg not in allow:
        return []
    allowed = allow[importer_pkg]

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind != SymbolKind.IMPORT or not symbol.imported_from:
            continue
        dep_pkg = _package_under(symbol.imported_from, root)
        if dep_pkg is None or dep_pkg == importer_pkg or dep_pkg in allowed:
            continue
        violations.append(
            Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=IMPORT_BOUNDARIES,
                message=(
                    f"package '{importer_pkg}' may not import '{dep_pkg}' "
                    f"(via '{symbol.imported_from}')"
                ),
            )
        )
    return violations


def _package_under(module_path: str, root: str) -> str | None:
    """Return the first package segment of ``module_path`` beneath ``root``.

    ``None`` when ``module_path`` is outside ``root`` (external) or is the root
    package itself (no segment beneath it).
    """
    parts = module_path.split(".")
    root_parts = root.split(".")
    if parts[: len(root_parts)] != root_parts:
        return None
    rest = parts[len(root_parts):]
    return rest[0] if rest else None


def prefer_tuple(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag function-local list literals that are never mutated.

    A list bound to a literal (``x = [...]``) that is never written through a
    subscript and never has a list-mutating method called on it could be a
    tuple. Scoped to function-local variables; module/class-level lists are
    skipped because cross-file mutation isn't visible to a per-file rule.

    Advisory and best-effort: a list passed to a function that mutates it, or
    aliased and mutated via the alias, can't be detected without escape
    analysis, so this rule can over-suggest. It is opt-in (not enabled by
    default).
    """
    scope_kind = {s.scope_id: s.kind for s in file_index.scopes}

    candidates: dict[str, object] = {}
    for symbol in file_index.symbols:
        if symbol.kind != SymbolKind.VARIABLE:
            continue
        ann = symbol.type_annotation
        if ann is None or ann.raw != "list" or ann.confidence is not Confidence.INFERRED:
            continue
        if scope_kind.get(symbol.parent_scope_id) not in (
            ScopeKind.FUNCTION,
            ScopeKind.LAMBDA,
        ):
            continue
        candidates[symbol.symbol_id] = symbol

    mutated: set[str] = set()
    for ref in file_index.references:
        if ref.kind == ReferenceKind.WRITE and ref.symbol_id in candidates:
            mutated.add(ref.symbol_id)  # subscript write: x[i] = v
        elif (
            ref.kind == ReferenceKind.CALL
            and ref.is_attribute_access
            and ref.receiver_root_symbol_id in candidates
            and ref.receiver_chain is not None
            and len(ref.receiver_chain) == 1
            and ref.symbol_id.rsplit(".", 1)[-1] in _LIST_MUTATORS
        ):
            mutated.add(ref.receiver_root_symbol_id)

    violations: list[Violation] = []
    for sid, symbol in candidates.items():
        if sid in mutated:
            continue
        violations.append(
            Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=PREFER_TUPLE,
                message=(
                    f"list '{symbol.name}' is never mutated — consider a tuple"
                ),
            )
        )
    return violations


REGISTRY: dict[str, Rule] = {
    REQUIRE_DOCSTRINGS: require_docstrings,
    NO_UNRESOLVED_REFS: no_unresolved_refs,
    IMPORT_BOUNDARIES: import_boundaries,
    PREFER_TUPLE: prefer_tuple,
}

# Rules registered by consumer projects via :func:`register_rule`. Kept separate
# from the built-in REGISTRY; built-ins take precedence on name clashes.
_REGISTERED: dict[str, Rule] = {}


def register_rule(name: str) -> Callable[[Rule], Rule]:
    """Register a custom check rule under ``name`` (decorator).

    Consumer projects define a rule ``(FileIndex, options) -> list[Violation]``,
    decorate it, and enable it via ``[tool.pypeeker].rules`` once the defining
    module is listed in ``[tool.pypeeker].plugins``.
    """

    def _decorate(rule: Rule) -> Rule:
        _REGISTERED[name] = rule
        return rule

    return _decorate


def get_rule(name: str) -> Rule | None:
    """Look up a rule by name: built-ins first, then registered custom rules."""
    return REGISTRY.get(name) or _REGISTERED.get(name)


def _as_enum_set(raw: Any, enum_cls: type) -> frozenset:
    values = [raw] if isinstance(raw, str) else list(raw)
    out = set()
    for v in values:
        try:
            out.add(enum_cls(v))
        except ValueError:
            continue
    return frozenset(out)
