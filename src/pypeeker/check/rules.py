"""Linter rule implementations.

Rules come in two scopes:

* **file** — ``(FileIndex, options) -> list[Violation]``, run once per indexed
  file. The original (and still default) shape.
* **project** — ``(CheckContext, options) -> list[Violation]``, run once per
  check with access to every index, a shared resolver, and the symbol tree
  (see :class:`pypeeker.check.context.CheckContext`). For cross-file rules.

The index stores 0-indexed line numbers (matching tree-sitter); we emit
1-indexed lines in violations to match ruff/mypy convention.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbol_id import is_unresolved_attr
from pypeeker.models.symbols import SymbolKind, Visibility

Rule = Callable[[FileIndex, Mapping[str, Any]], list[Violation]]
ProjectRule = Callable[[CheckContext, Mapping[str, Any]], list[Violation]]

_RULE_SCOPES = ("file", "project")
_RuleT = TypeVar("_RuleT", Rule, ProjectRule)

REQUIRE_DOCSTRINGS = "require-docstrings"
NO_UNRESOLVED_REFS = "no-unresolved-refs"
IMPORT_BOUNDARIES = "import-boundaries"
PREFER_TUPLE = "prefer-tuple"
UNUSED_PUBLIC_SYMBOL = "unused-public-symbol"

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
        if is_unresolved_attr(ref.symbol_id):
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


# ── project-scoped rules ────────────────────────────────────────────────────


def unused_public_symbol(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag module-level public functions/classes with no references anywhere.

    A symbol counts as used when any reference in any indexed file resolves to
    it through the shared :class:`CrossModuleResolver` — direct use, use via an
    import alias, use through a barrel re-export, or qualified attribute
    access. Conservative exclusions:

    * non-public symbols and non-module-level symbols (methods, nested defs);
    * dunder-named symbols and ``main``, plus anything in a ``__main__.py``;
    * symbols re-exported by a package ``__init__`` barrel — those are
      deliberate public API surface even when nothing in-repo consumes them.

    Best-effort and **opt-in** (not enabled by default): static references are
    the only signal, so this rule over-flags symbols reached dynamically —
    decorator-registered handlers/entry points discovered by framework name,
    ``getattr``/``globals()`` access, CLI entry points declared in
    ``pyproject.toml``, and anything consumed only outside the indexed tree.
    """
    resolver = context.resolver

    # Canonical definition ids referenced anywhere in the project.
    referenced: set[str] = set()
    for index in context.indexes:
        for ref in index.references:
            referenced.add(resolver.resolve_reference(ref))

    # Canonical ids re-exported by an __init__ barrel: public API surface.
    barrel_exported: set[str] = set()
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        for symbol in index.symbols:
            if symbol.kind == SymbolKind.IMPORT:
                barrel_exported.add(resolver.resolve_definition(symbol.symbol_id))

    violations: list[Violation] = []
    for index in context.indexes:
        if index.file_path.endswith("__main__.py"):
            continue
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
            None,
        )
        if module_id is None:
            continue
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.CLASS):
                continue
            if symbol.visibility is not Visibility.PUBLIC:
                continue
            if symbol.parent_scope_id != module_id:
                continue
            if symbol.name == "main" or (
                symbol.name.startswith("__") and symbol.name.endswith("__")
            ):
                continue
            canonical = resolver.resolve_definition(symbol.symbol_id)
            if canonical in referenced or canonical in barrel_exported:
                continue
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=UNUSED_PUBLIC_SYMBOL,
                    message=(
                        f"public {symbol.kind.value} '{symbol.name}' "
                        f"has no references in the project"
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

PROJECT_REGISTRY: dict[str, ProjectRule] = {
    UNUSED_PUBLIC_SYMBOL: unused_public_symbol,
}

# Rules registered by consumer projects via :func:`register_rule`. Kept separate
# from the built-in registries; built-ins take precedence on name clashes.
_REGISTERED: dict[str, Rule] = {}
_REGISTERED_PROJECT: dict[str, ProjectRule] = {}


def register_rule(name: str, *, scope: str = "file") -> Callable[[_RuleT], _RuleT]:
    """Register a custom check rule under ``name`` (decorator).

    Consumer projects decorate a rule function, then enable it via
    ``[tool.pypeeker].rules`` once the defining module is listed in
    ``[tool.pypeeker].plugins``. The expected signature depends on ``scope``:

    * ``scope="file"`` (default) — ``(FileIndex, options) -> list[Violation]``,
      called once per indexed file. Existing plugins keep working unchanged.
    * ``scope="project"`` — ``(CheckContext, options) -> list[Violation]``,
      called once per check run with cross-file context.
    """
    if scope not in _RULE_SCOPES:
        raise ValueError(
            f"unknown rule scope '{scope}' (expected one of {_RULE_SCOPES})"
        )

    def _decorate(rule: _RuleT) -> _RuleT:
        if scope == "project":
            _REGISTERED_PROJECT[name] = rule
        else:
            _REGISTERED[name] = rule
        return rule

    return _decorate


def get_rule(name: str) -> Rule | None:
    """Look up a per-file rule by name: built-ins first, then custom rules."""
    return REGISTRY.get(name) or _REGISTERED.get(name)


def get_project_rule(name: str) -> ProjectRule | None:
    """Look up a project-scoped rule by name: built-ins first, then custom."""
    return PROJECT_REGISTRY.get(name) or _REGISTERED_PROJECT.get(name)


def _as_enum_set(raw: Any, enum_cls: type) -> frozenset:
    values = [raw] if isinstance(raw, str) else list(raw)
    out = set()
    for v in values:
        try:
            out.add(enum_cls(v))
        except ValueError:
            continue
    return frozenset(out)
