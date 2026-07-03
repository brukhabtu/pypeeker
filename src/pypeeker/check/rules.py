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

import fnmatch
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pypeeker.analysis.calls import ReceiverKind
from pypeeker.analysis.observations import Observations
from pypeeker.analysis.purity import DEFAULT_POLICY, PurityPolicy, impurities
from pypeeker.check.context import CheckContext
from pypeeker.check.fixes import DeleteUnusedSymbolFix, PreferTupleFix, with_fix
from pypeeker.check.models import Violation
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.references import ReferenceKind
from pypeeker.models.scopes import ScopeKind
from pypeeker.models.symbol_id import builtin_id, is_unresolved_attr, module_of
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility
from pypeeker.project import VisibilityConfig, coerce_visibility
from pypeeker.query import SemanticQueryEngine
from pypeeker.resolve import CrossModuleResolver

Rule = Callable[[FileIndex, Mapping[str, Any]], list[Violation]]
ProjectRule = Callable[[CheckContext, Mapping[str, Any]], list[Violation]]

_RULE_SCOPES = ("file", "project")
_RuleT = TypeVar("_RuleT", Rule, ProjectRule)

REQUIRE_DOCSTRINGS = "require-docstrings"
NO_UNRESOLVED_REFS = "no-unresolved-refs"
IMPORT_BOUNDARIES = "import-boundaries"
PREFER_TUPLE = "prefer-tuple"
UNUSED_PUBLIC_SYMBOL = "unused-public-symbol"
NO_IMPURE_FUNCTIONS = "no-impure-functions"

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
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag internal imports that cross a forbidden package boundary.

    Enforces declared layering. Each file's package is the first segment under
    the project root of its module path. For each ``IMPORT`` symbol the imported
    package is the package that *actually defines* the imported name — resolved
    through any barrel (``__init__``) re-export chain by the shared
    :class:`CrossModuleResolver`, not the literal text of ``imported_from``.
    Charging against the origin closes re-export laundering (``query`` reaching
    ``refactor`` through a ``storage`` barrel) and symbol-vs-package
    misattribution (``from pkg import Symbol`` names a symbol, not a package).
    When resolution can't leave the importing file (external / unindexed
    target) the literal package is used as a fallback. An import is flagged when
    the importing package is listed in ``allow`` and the resolved package is
    neither the same package nor in that package's allow-list.

    Dynamic imports recovered by the binder (``importlib.import_module`` /
    ``__import__`` with a string-literal path) are enforced too, but their
    findings carry ``confidence=HEURISTIC`` (see
    :attr:`Symbol.import_confidence`) since the binding is best-effort.

    Options (``[tool.pypeeker.import-boundaries]``):
        ``allow``   — mapping of package -> list of packages it may import.
                      Packages absent from the mapping are unconstrained.
        ``root``    — project root package (dotted prefix). Defaults to the
                      single top-level segment shared by the indexed modules.
        ``strict``  — when true, every top-level unit under ``root`` present in
                      the index must appear in ``allow`` or ``unconstrained``;
                      an undeclared unit is flagged so new packages can't slip
                      enforcement silently. Default false (backward compatible).
        ``unconstrained`` — units deliberately exempt from ``allow`` under
                      ``strict`` (e.g. a ``cli`` composition root).
        ``report-unused-allowances`` — when true, flag ``allow`` entries never
                      exercised by any real import across the project. Default
                      false.

    External imports (under a different root) and same-package imports are
    never flagged.
    """
    allow_raw = options.get("allow")
    allow: dict[str, set[str]] = (
        {pkg: set(deps) for pkg, deps in allow_raw.items()}
        if isinstance(allow_raw, Mapping)
        else {}
    )
    strict = bool(options.get("strict"))
    unconstrained = set(_as_str_list(options.get("unconstrained")))
    report_unused = bool(options.get("report-unused-allowances"))
    if not allow and not strict:
        return []  # no configuration → no-op (backward compatible)

    resolver = context.resolver
    module_ids: dict[str, str] = {}  # file_path -> MODULE symbol id
    for index in context.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
            None,
        )
        if module_id is not None:
            module_ids[index.file_path] = module_id

    root = options.get("root") or _infer_root(module_ids.values())

    violations: list[Violation] = []
    exercised: set[tuple[str, str]] = set()  # (importer, dep) pairs actually used
    for index in context.indexes:
        module_id = module_ids.get(index.file_path)
        if module_id is None:
            continue
        importer_pkg = _package_under(module_id, root)
        if importer_pkg is None or importer_pkg not in allow:
            continue
        allowed = allow[importer_pkg]
        for symbol in index.symbols:
            if symbol.kind != SymbolKind.IMPORT or not symbol.imported_from:
                continue
            dep_pkg, via_reexport = _import_origin_package(symbol, resolver, root)
            if dep_pkg is None or dep_pkg == importer_pkg:
                continue
            if dep_pkg in allowed:
                exercised.add((importer_pkg, dep_pkg))
                continue
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=IMPORT_BOUNDARIES,
                    message=_boundary_message(
                        importer_pkg, dep_pkg, symbol.imported_from, via_reexport
                    ),
                    confidence=symbol.import_confidence or Confidence.DECLARED,
                )
            )

    if strict:
        violations.extend(
            _strict_undeclared_violations(module_ids, allow, unconstrained, root)
        )
    if report_unused:
        violations.extend(
            _unused_allowance_violations(allow, exercised, module_ids, root)
        )
    return violations


def _import_origin_package(
    symbol: Symbol, resolver: CrossModuleResolver, root: str
) -> tuple[str | None, bool]:
    """The package that defines an import's target, and whether via a re-export.

    Resolves the import symbol to its canonical definition through the barrel
    re-export chain and maps that definition's module to a package under
    ``root``. Returns ``(package, via_reexport)`` where ``via_reexport`` is true
    when the resolved package differs from the literal ``imported_from``
    package (i.e. the name was re-exported from elsewhere). Falls back to the
    literal package when resolution can't leave the importing file (external /
    unindexed / dynamic-unresolved target).
    """
    literal_pkg = _package_under(symbol.imported_from or "", root)
    canonical = resolver.resolve_definition(symbol.symbol_id)
    if canonical == symbol.symbol_id:
        return literal_pkg, False  # resolution didn't follow the import anywhere
    origin_pkg = _package_under(module_of(canonical), root)
    if origin_pkg is None:
        return literal_pkg, False
    return origin_pkg, origin_pkg != literal_pkg


def _boundary_message(
    importer_pkg: str, dep_pkg: str, imported_from: str, via_reexport: bool
) -> str:
    """Render an import-boundary violation, noting re-export laundering."""
    detail = "via re-export" if via_reexport else "via"
    return (
        f"package '{importer_pkg}' may not import '{dep_pkg}' "
        f"({detail} '{imported_from}')"
    )


def _infer_root(module_ids: Any) -> str:
    """Pick the project root package from the indexed modules' first segments.

    The dominant top-level segment (ties broken alphabetically) — the common
    case is a single top-level package, for which this returns that package.
    """
    from collections import Counter

    counts = Counter(mid.split(".")[0] for mid in module_ids)
    if not counts:
        return ""
    return max(counts, key=lambda seg: (counts[seg], seg))


def _representative_file(entries: list[tuple[str, str]]) -> str:
    """A stable file to anchor a package-level finding on.

    Prefers the package ``__init__.py``; otherwise the alphabetically-first
    module file, so findings are deterministic across runs.
    """
    for file_path, _ in sorted(entries):
        if file_path.endswith("__init__.py"):
            return file_path
    return sorted(entries)[0][0]


def _units_under_root(
    module_ids: dict[str, str], root: str
) -> dict[str, list[tuple[str, str]]]:
    """Map each top-level unit under ``root`` to its (file_path, module_id) list.

    The root package itself and dunder modules (``__main__``) are not layered
    units and are skipped.
    """
    units: dict[str, list[tuple[str, str]]] = {}
    for file_path, module_id in module_ids.items():
        unit = _package_under(module_id, root)
        if unit is None or unit.startswith("__"):
            continue
        units.setdefault(unit, []).append((file_path, module_id))
    return units


def _strict_undeclared_violations(
    module_ids: dict[str, str],
    allow: dict[str, set[str]],
    unconstrained: set[str],
    root: str,
) -> list[Violation]:
    """Flag indexed top-level units missing from both ``allow`` and ``unconstrained``."""
    violations: list[Violation] = []
    for unit, entries in sorted(_units_under_root(module_ids, root).items()):
        if unit in allow or unit in unconstrained:
            continue
        violations.append(
            Violation(
                file_path=_representative_file(entries),
                line=1,
                rule=IMPORT_BOUNDARIES,
                message=(
                    f"package '{unit}' is not declared in import-boundaries "
                    f"(add it to [tool.pypeeker.import-boundaries.allow] or to "
                    f"the 'unconstrained' list)"
                ),
            )
        )
    return violations


def _unused_allowance_violations(
    allow: dict[str, set[str]],
    exercised: set[tuple[str, str]],
    module_ids: dict[str, str],
    root: str,
) -> list[Violation]:
    """Flag ``allow`` entries that no real import in the project exercises."""
    units = _units_under_root(module_ids, root)
    violations: list[Violation] = []
    for importer_pkg in sorted(allow):
        entries = units.get(importer_pkg)
        for dep_pkg in sorted(allow[importer_pkg]):
            if (importer_pkg, dep_pkg) in exercised:
                continue
            violations.append(
                Violation(
                    file_path=(
                        _representative_file(entries)
                        if entries
                        else "pyproject.toml"
                    ),
                    line=1,
                    rule=IMPORT_BOUNDARIES,
                    message=(
                        f"unused import-boundaries allowance: '{importer_pkg}' "
                        f"is permitted to import '{dep_pkg}' but never does"
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

    Each violation carries a :class:`~pypeeker.check.fixes.PreferTupleFix`
    that rewrites the literal's brackets (``[...]`` -> ``(...)``), declining
    conservatively when the literal cannot be re-scanned safely.
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
            with_fix(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=PREFER_TUPLE,
                    message=(
                        f"list '{symbol.name}' is never mutated — consider a tuple"
                    ),
                ),
                PreferTupleFix(
                    file_path=symbol.location.file_path,
                    symbol_id=sid,
                    name=symbol.name,
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
      deliberate public API surface even when nothing in-repo consumes them;
    * symbols carrying a decorator matching ``allow-decorators`` (the rule's
      own option merged with the global ``[tool.pypeeker.visibility]`` list);
    * in library mode, symbols re-exported by a barrel under a public root
      (today subsumed by the unconditional barrel exemption above, but kept
      explicit: it is the documented library contract and survives if the
      blanket barrel skip ever becomes conditional).

    Findings for symbols defined in a module that references
    ``getattr``/``globals``/``vars``/``locals`` are still emitted but carry
    ``confidence=HEURISTIC`` — dynamic access can consume symbols invisibly.

    Each finding embeds the full symbol id, so the batch demotion planner
    can consume it: extract the ``(symbol_id, confidence)`` pair with
    :func:`pypeeker.check.demotion.demote_entry`.

    Options (``[tool.pypeeker.unused-public-symbol]``):
        ``allow-decorators`` — fnmatch patterns matched against decorator
                               source text or its leading callable name.
        ``also-private``     — when true, also report unreferenced PROTECTED
                               (``_name``) and PRIVATE (``__name``)
                               module-level symbols. Those findings — and
                               ONLY those — carry a
                               :class:`~pypeeker.check.fixes.DeleteUnusedSymbolFix`
                               that deletes the definition: dead private code
                               is safe to auto-remove, while pruning public
                               API stays a human decision. Default false.
        ``visibility``       — reserved key injected by ``check.config``
                               with the ``[tool.pypeeker.visibility]`` table.

    Best-effort and **opt-in** (not enabled by default): static references are
    the only signal, so this rule over-flags symbols reached dynamically —
    decorator-registered handlers/entry points discovered by framework name,
    ``getattr``/``globals()`` access, CLI entry points declared in
    ``pyproject.toml``, and anything consumed only outside the indexed tree.
    """
    also_private = bool(options.get("also-private"))
    visibilities = (
        (Visibility.PUBLIC, Visibility.PROTECTED, Visibility.PRIVATE)
        if also_private
        else (Visibility.PUBLIC,)
    )
    vis = coerce_visibility(options.get("visibility"))
    allow_decorators = _merged_allow_decorators(options, vis)
    protected = _public_root_protected(context, vis)
    dynamic_modules = _dynamic_access_modules(context)
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
            if symbol.visibility not in visibilities:
                continue
            if symbol.parent_scope_id != module_id:
                continue
            if symbol.name == "main" or (
                symbol.name.startswith("__") and symbol.name.endswith("__")
            ):
                continue
            if _has_allowed_decorator(symbol, allow_decorators):
                continue
            canonical = resolver.resolve_definition(symbol.symbol_id)
            if canonical in referenced or canonical in barrel_exported:
                continue
            if canonical in protected:
                continue  # library-mode public API (see docstring)
            violation = Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=UNUSED_PUBLIC_SYMBOL,
                message=(
                    f"{symbol.visibility.value} {symbol.kind.value} "
                    f"'{symbol.symbol_id}' has no references in the project"
                ),
                confidence=_dynamic_access_confidence(
                    module_id, dynamic_modules
                ),
            )
            if symbol.visibility is not Visibility.PUBLIC:
                # Deleting dead PRIVATE code is auto-fixable; deleting
                # public API is not (see the also-private option docs).
                violation = with_fix(
                    violation,
                    DeleteUnusedSymbolFix(
                        file_path=symbol.location.file_path,
                        symbol_id=symbol.symbol_id,
                        name=symbol.name,
                    ),
                )
            violations.append(violation)
    return violations


def no_impure_functions(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag functions in scope that have impurity observations.

    Runs the purity analysis (:func:`pypeeker.analysis.purity.impurities`,
    including its transitive cross-function call walk) over every FUNCTION /
    METHOD symbol whose ``symbol_id`` or module path matches an ``include``
    pattern, and emits one violation per impure function.

    Options (``[tool.pypeeker.no-impure-functions]``):
        ``include``      — fnmatch patterns matched against the symbol_id
                           (``"pkg.mod:func"``) or the module path
                           (``"pkg.mod"``). **Required**: with no ``include``
                           patterns the rule flags nothing, so enabling it
                           without scoping is deliberately a no-op.
        ``exclude``      — patterns subtracted from ``include`` (exclude wins).
        ``extra-impure`` — extra names treated as impure: dotted names join
                           the module denylist (``"mypkg.db.commit"``), bare
                           names join the builtin denylist (``"log"``).
        ``allow``        — names removed from every purity denylist.

    Opt-in (not enabled by default): purity analysis is heuristic, so apply
    it where purity is an actual contract (e.g. a ``*.pure`` module
    convention) rather than project-wide.
    """
    include = _as_str_list(options.get("include"))
    if not include:
        return []
    exclude = _as_str_list(options.get("exclude"))
    policy = _configured_policy(options)
    engine = SemanticQueryEngine(context.store)

    violations: list[Violation] = []
    for index in context.indexes:
        for symbol in index.symbols:
            if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
                continue
            if not _matches_any(symbol.symbol_id, include):
                continue
            if _matches_any(symbol.symbol_id, exclude):
                continue
            found = impurities(
                context.store, symbol.symbol_id, engine=engine, policy=policy
            )
            if not found:  # None (unanalyzable) or empty (pure)
                continue
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=NO_IMPURE_FUNCTIONS,
                    message=(
                        f"{symbol.kind.value} '{symbol.symbol_id}' is impure: "
                        f"{_summarize_observations(found)}"
                    ),
                    confidence=_impurity_confidence(found),
                )
            )
    return violations


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


# ── visibility-shared helpers ───────────────────────────────────────────────
# Used by unused-public-symbol here and imported by the builtin visibility /
# test-only-production-code rules, so the [tool.pypeeker.visibility] contract
# (library mode, public roots, decorator allowlists, dynamic-access proximity)
# is implemented exactly once.

def _dynamic_access_confidence(
    module_id: str | None, dynamic_modules: set[str]
) -> Confidence:
    """HEURISTIC when the subject's module uses dynamic access, else DECLARED.

    Findings about a symbol defined in a module that references
    ``getattr``/``globals``/``vars``/``locals`` are still emitted, but
    labeled ``HEURISTIC`` — dynamic access can consume (or serve) the symbol
    invisibly, so reference-based evidence is weaker there. This supersedes
    the message-suffix mechanism that previously decorated such findings.
    """
    if module_id is not None and module_id in dynamic_modules:
        return Confidence.HEURISTIC
    return Confidence.DECLARED


def _impurity_confidence(found: Observations) -> Confidence:
    """Confidence tier for an impurity verdict from its observations.

    ``HEURISTIC`` when every observation rests on an UNKNOWN receiver —
    name matching against a receiver the binder could not classify is
    guesswork. Any structurally-grounded observation (builtin/module call,
    parameter/import receiver, transitive impure call) keeps the verdict
    ``DECLARED``: the impurity holds regardless of the weak observations.
    Observation kinds without a ``receiver_kind`` (BareCall, ModuleCall,
    TransitiveImpureCall, outer-scope writes) count as structural; a bare
    call matched via an *unresolved* name is not distinguishable from a
    builtin at this layer, so it stays DECLARED (accepted imprecision).
    """
    weak = [
        obs
        for obs in found
        if getattr(obs, "receiver_kind", None) is ReceiverKind.UNKNOWN
    ]
    if found and len(weak) == len(found):
        return Confidence.HEURISTIC
    return Confidence.DECLARED


_DYNAMIC_ACCESS_BUILTIN_IDS: frozenset[str] = frozenset(
    builtin_id(name) for name in ("getattr", "globals", "vars", "locals")
)
"""Resolved builtin reference ids that signal dynamic symbol access."""


def _dynamic_access_modules(context: CheckContext) -> set[str]:
    """Dotted module paths containing getattr/globals/vars/locals references.

    Reference-only static analysis cannot see through dynamic access, so a
    module using these builtins may consume (or serve) symbols invisibly.
    Findings about symbols defined in such a module are still emitted but
    carry ``confidence=HEURISTIC`` (see :func:`_dynamic_access_confidence`).
    """
    out: set[str] = set()
    for index in context.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
            None,
        )
        if module_id is None:
            continue
        if any(
            ref.symbol_id in _DYNAMIC_ACCESS_BUILTIN_IDS
            for ref in index.references
        ):
            out.add(module_id)
    return out


def _has_allowed_decorator(symbol: Symbol, patterns: list[str]) -> bool:
    """True when any decorator on ``symbol`` matches an fnmatch pattern.

    Decorators are stored as source text without the ``@``
    (``register_rule("name", scope="project")``); patterns are matched
    against both the full text and the leading callable name, so plain
    names (``register_rule``) work without trailing wildcards.
    """
    if not patterns:
        return False
    for decorator in symbol.decorators:
        head = decorator.split("(", 1)[0].strip()
        if any(
            fnmatch.fnmatchcase(decorator, pattern)
            or fnmatch.fnmatchcase(head, pattern)
            for pattern in patterns
        ):
            return True
    return False


def _merged_allow_decorators(
    options: Mapping[str, Any], vis: VisibilityConfig
) -> list[str]:
    """A rule's own ``allow-decorators`` merged with the global visibility list."""
    return _as_str_list(options.get("allow-decorators")) + list(vis.allow_decorators)


def _public_root_protected(
    context: CheckContext, vis: VisibilityConfig
) -> set[str]:
    """Canonical ids re-exported by a barrel under an effective public root.

    Library mode only (app mode protects nothing). A barrel qualifies when
    its package equals, or is nested beneath, one of the effective public
    roots (explicit ``public-roots``, defaulting to every top-level package —
    see :meth:`VisibilityConfig.effective_public_roots`). Such exports are the
    library's published API: external consumers are invisible to the index,
    so in-repo reference counts say nothing about them.
    """
    if not vis.is_library:
        return set()
    module_ids: dict[str, str] = {}
    for index in context.indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind == SymbolKind.MODULE),
            None,
        )
        if module_id is not None:
            module_ids[index.file_path] = module_id
    roots = vis.effective_public_roots(
        module_id.split(".")[0] for module_id in module_ids.values()
    )
    resolver = context.resolver
    protected: set[str] = set()
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        package = module_ids.get(index.file_path)
        if package is None:
            continue
        if not any(
            package == root or package.startswith(root + ".") for root in roots
        ):
            continue
        for symbol in index.symbols:
            if symbol.kind == SymbolKind.IMPORT:
                protected.add(resolver.resolve_definition(symbol.symbol_id))
    return protected


def _configured_policy(options: Mapping[str, Any]) -> PurityPolicy:
    """Build the purity policy from ``extra-impure`` / ``allow`` options.

    Dotted ``extra-impure`` names extend the module denylist; bare names
    extend the builtin denylist. ``allow`` names are removed from every
    denylist. Without either option the shared default policy is used.
    """
    extra = _as_str_list(options.get("extra-impure"))
    allow = _as_str_list(options.get("allow"))
    if not extra and not allow:
        return DEFAULT_POLICY
    return DEFAULT_POLICY.extended(
        extra_impure_builtins=[name for name in extra if "." not in name],
        extra_module_impure=[name for name in extra if "." in name],
        allow=allow,
    )


_MAX_OBSERVATIONS_IN_MESSAGE = 3


def _summarize_observations(found: Observations) -> str:
    """One-line summary: first few observation kinds/names with 1-indexed lines."""
    shown = list(found)[:_MAX_OBSERVATIONS_IN_MESSAGE]
    parts = [_describe_observation(obs) for obs in shown]
    remaining = len(found) - len(shown)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return "; ".join(parts)


def _describe_observation(obs: Any) -> str:
    """Render one observation as ``Kind 'name' (line N)`` (line 1-indexed)."""
    name = None
    for attr in ("name", "qualified_name", "method", "target", "attribute", "callee"):
        value = getattr(obs, attr, None)
        if value is not None:
            name = value
            break
    label = type(obs).__name__
    if name is not None:
        label = f"{label} '{name}'"
    line = getattr(obs, "line", None)
    if line is not None:
        label = f"{label} (line {line + 1})"
    return label


REGISTRY: dict[str, Rule] = {
    REQUIRE_DOCSTRINGS: require_docstrings,
    NO_UNRESOLVED_REFS: no_unresolved_refs,
    PREFER_TUPLE: prefer_tuple,
}

PROJECT_REGISTRY: dict[str, ProjectRule] = {
    IMPORT_BOUNDARIES: import_boundaries,
    UNUSED_PUBLIC_SYMBOL: unused_public_symbol,
    NO_IMPURE_FUNCTIONS: no_impure_functions,
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
