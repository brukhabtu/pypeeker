"""born-private: a prospective "private until needed" ratchet (TASK-99).

``over-exposed-module-symbol`` reports every public module-level symbol whose
observed usage is module-local — on a legacy codebase that is a wall of
findings nobody will fix at once. This rule keeps the same signal but applies
it only PROSPECTIVELY: symbols already public when the rule was first enabled
are recorded in a baseline and never relitigated; only symbols that became
public *afterwards* must justify their visibility with a cross-module
reference (or an underscore, or an explicit baseline update).

Self-seeding (read this before enabling)
----------------------------------------
The recorded symbol ids live in the shared baseline file
(``.semantic-tool/check-baseline.json``) under the ``"symbols"`` namespace —
see :mod:`pypeeker.check.baseline`. The rule seeds that namespace ITSELF: on
the first run against a project whose baseline has no ``"symbols"`` namespace
it writes every current public symbol id and reports nothing, so first
enablement is silent by design. This is the only time the rule writes the
baseline; subsequent runs never auto-extend it (a new symbol stays flagged
until it gains a cross-module consumer, an underscore, or an explicit
re-record). Re-recording accepted symbols belongs to ``check
--update-baseline``: when this rule is enabled, that flow clears the symbol
namespace before running the rules (see
:func:`pypeeker.check.baseline.clear_symbol_baseline`), so the run re-seeds
it with the current public surface.

Like its visibility siblings the rule is best-effort and **opt-in**: static
references are the only signal, so dynamically-reached symbols over-flag.
The same conservative exemptions as ``over-exposed-module-symbol`` apply
(dunders, ``main``, ``__main__.py``, barrel re-exports, allowed decorators,
library-mode public roots), and findings whose defining module uses
``getattr``/``globals``/``vars``/``locals`` carry ``confidence=HEURISTIC``.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses into
the engine import and creates a cycle. The usage-scope core and option
helpers are reused from :mod:`pypeeker.check.builtin.visibility` and
:mod:`pypeeker.check.rules` so the ``[tool.pypeeker.visibility]`` contract
stays implemented once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pypeeker.check.baseline import (
    baseline_path,
    has_symbol_baseline,
    load_symbol_baseline,
    write_symbol_baseline,
)
from pypeeker.check.builtin.visibility import (
    _allowed,
    _as_str_list,
    _is_dunder,
    _module_id_of_index,
    _selected_kinds,
    _usage_origins,
)
from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import (
    _dynamic_access_confidence,
    _dynamic_access_modules,
    _has_allowed_decorator,
    _merged_allow_decorators,
    _public_root_protected,
    register_rule,
)
from pypeeker.models.symbols import Symbol, SymbolKind, Visibility
from pypeeker.project import coerce_visibility

BORN_PRIVATE = "born-private"


def _barrel_exported(context: CheckContext) -> set[str]:
    """Canonical ids re-exported by a package ``__init__`` barrel.

    Replicates the small inline computation in
    ``over-exposed-module-symbol`` (not a shared helper there, and
    ``check/builtin/visibility.py`` is owned by another work stream this
    wave, so it cannot be extracted yet): barrel re-exports are deliberate
    public API surface and exempt from the ratchet.
    """
    resolver = context.resolver
    exported: set[str] = set()
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        for symbol in index.symbols:
            if symbol.kind == SymbolKind.IMPORT:
                exported.add(resolver.resolve_definition(symbol.symbol_id))
    return exported


@register_rule(BORN_PRIVATE, scope="project")
def born_private(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag NEWLY public module-local symbols absent from the symbol baseline.

    A current public module-level symbol is flagged when it is (a) not in the
    recorded symbol baseline — i.e. it became public after the baseline was
    seeded — and (b) never referenced outside its own module (the same
    observed-usage-scope computation as ``over-exposed-module-symbol``; zero
    references also count as module-local). Symbols recorded in the baseline
    are legacy and never flagged, whatever their usage scope.

    Self-seeding: when the project baseline has no ``"symbols"`` namespace
    yet, this run records every current public symbol id (post-exemptions)
    and returns no violations — first enablement is silent. That seed is the
    ONLY baseline write this rule ever performs; later runs never auto-extend
    the recorded set. Accepting a flagged symbol as deliberately public means
    re-recording the baseline (``check --update-baseline`` clears the symbol
    namespace so this rule re-seeds it — see the module docstring) or
    carving it out via the options below.

    Exemptions (identical to ``over-exposed-module-symbol``): dunder names
    and ``main``, anything in a ``__main__.py``, barrel re-exported symbols,
    symbols carrying an allowed decorator, and — in library mode — symbols
    re-exported by a barrel under a public root. Findings for symbols defined
    in a module referencing ``getattr``/``globals``/``vars``/``locals`` carry
    ``confidence=HEURISTIC``.

    Options:
        ``kinds``            — symbol kinds to check, from function / class /
                               variable (default function + class).
        ``allow``            — fnmatch patterns (symbol_id or module path)
                               exempting symbols.
        ``allow-decorators`` — fnmatch patterns matched against decorator
                               source text or its leading callable name.
        ``visibility``       — reserved key injected by ``check.config`` with
                               the ``[tool.pypeeker.visibility]`` table.

    Opt-in (not enabled by default): same dynamic-access caveats as the
    visibility-detection rules.
    """
    kinds = _selected_kinds(options.get("kinds"))
    allow = _as_str_list(options.get("allow"))
    vis = coerce_visibility(options.get("visibility"))
    allow_decorators = _merged_allow_decorators(options, vis)
    protected = _public_root_protected(context, vis)
    barrel_exported = _barrel_exported(context)
    resolver = context.resolver

    def _eligible(symbol: Symbol, module_id: str) -> str | None:
        """The canonical id when ``symbol`` is a tracked public module-level
        symbol (post-exemptions), else None."""
        if symbol.kind not in kinds:
            return None
        if symbol.visibility is not Visibility.PUBLIC:
            return None
        if symbol.parent_scope_id != module_id:
            return None
        if symbol.name == "main" or _is_dunder(symbol.name):
            return None
        if _allowed(symbol.symbol_id, allow):
            return None
        if _has_allowed_decorator(symbol, allow_decorators):
            return None
        canonical = resolver.resolve_definition(symbol.symbol_id)
        if canonical in barrel_exported:
            return None
        if canonical in protected:
            return None  # library-mode public API
        return canonical

    # Current public surface: canonical id -> (defining symbol, its module).
    current: dict[str, tuple[Symbol, str]] = {}
    for index in context.indexes:
        if index.file_path.endswith("__main__.py"):
            continue
        module_id = _module_id_of_index(index)
        if module_id is None:
            continue
        for symbol in index.symbols:
            canonical = _eligible(symbol, module_id)
            if canonical is not None:
                current[canonical] = (symbol, module_id)

    path = baseline_path(context.store.project_root)
    if not has_symbol_baseline(path):
        # First enablement: seed silently (see module docstring). The only
        # baseline write this rule performs.
        write_symbol_baseline(path, set(current))
        return []

    recorded = load_symbol_baseline(path)
    dynamic_modules = _dynamic_access_modules(context)
    origins = _usage_origins(context)

    violations: list[Violation] = []
    for canonical, (symbol, module_id) in current.items():
        if canonical in recorded:
            continue  # legacy public symbol: never relitigated
        if origins.get(canonical, set()) - {module_id}:
            continue  # cross-module use justifies the visibility
        violations.append(
            Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=BORN_PRIVATE,
                message=(
                    f"newly public '{symbol.name}' is only used within its "
                    f"module — make it _{symbol.name} or record it "
                    f"(`check --update-baseline`)"
                ),
                confidence=_dynamic_access_confidence(
                    module_id, dynamic_modules
                ),
            )
        )
    return violations
