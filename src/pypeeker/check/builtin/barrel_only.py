"""barrel-only: require cross-package imports to go through the public barrel.

``import-boundaries`` governs WHICH packages may depend on which. This rule
governs THAT a permitted cross-package dependency goes through the target
package's curated public surface (its ``__init__`` barrel) instead of reaching
past it into an internal submodule.

A consumer can bypass a package's public API by deep-importing an internal
module (``from pypeeker.refactor.planner import RenamePlanner``) rather than
going through the barrel (``from pypeeker.refactor import RenamePlanner``). Both
bind the same object, but the deep import couples the consumer to the internal
layout: renaming or splitting ``refactor.planner`` silently breaks it, while
the barrel is the stable contract. This rule flags the deep form whenever the
same canonical definition is reachable through the target package's barrel.

Detection (see the acceptance semantics in TASK-104):

* The import must be a ``from a.b.c import Name`` deep import — its module path
  (``imported_from`` minus the trailing name) must sit strictly below
  ``root.P_t`` for some package ``P_t``. A bare ``root.P_t`` import already
  goes through the barrel level and is clean; a bare ``import a.b.c`` module
  import binds no name through a barrel and never matches.
* The target package ``P_t`` must have a *curated* barrel: a ``P_t/__init__.py``
  that declares ``__all__`` (the intentional-public-surface signal) and whose
  re-export IMPORT symbols resolve to the same canonical definition as this
  import's target. If the package has no ``__all__`` barrel, or the barrel does
  not re-export this name, no valid barrel path exists — demanding one is not
  this rule's job, so the import is left alone.

Never flagged:

* same-package imports (``P_t == P_f``) — a module may freely reach its own
  siblings, and this also exempts a barrel's own ``__init__.py`` deep imports
  (that is how barrels are built);
* imports whose ``import_confidence`` is set — dynamic/synthetic imports the
  binder recovered best-effort are out of scope.

Determining the root: ``root`` is read from this rule's own options
(``[tool.pypeeker.barrel-only]``); when omitted each file falls back to its own
top-level segment, mirroring ``import-boundaries``. The rule's options are the
only thing the engine hands it, so wiring ``root`` here (rather than reaching
into the ``import-boundaries`` table) keeps the plumbing local and explicit.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses into
the engine import and creates a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models import FileIndex, SymbolKind

BARREL_ONLY = "barrel-only"


@register_rule(BARREL_ONLY, scope="project")
def _barrel_only(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag cross-package deep imports of a name the target's barrel re-exports.

    For each ``from a.b.c import Name`` IMPORT symbol whose target package
    differs from the importer's and has a curated ``__all__`` barrel that
    re-exports the same canonical definition, emit one finding telling the
    consumer to import through the barrel instead of the internal module. See
    the module docstring for the full detection contract and exemptions.

    Options (``[tool.pypeeker.barrel-only]``):
        ``root`` — project root package (dotted prefix). When omitted each file
                   falls back to its own top-level segment.
    """
    resolver = context.resolver
    configured_root = options.get("root")

    # Curated barrels: barrel module id -> canonical defs it re-exports. A
    # barrel is curated only when its __init__ declares __all__ (recorded by
    # the index as a VARIABLE named "__all__"; its contents are unavailable, so
    # the re-export set is taken from the __init__'s IMPORT symbols — the same
    # approximation used by unused-public-symbol / born-private).
    barrels: dict[str, set[str]] = {}
    for index in context.indexes:
        if not index.file_path.endswith("__init__.py"):
            continue
        module_id = _module_id_of(index)
        if module_id is None:
            continue
        if not any(
            s.kind is SymbolKind.VARIABLE and s.name == "__all__"
            for s in index.symbols
        ):
            continue
        barrels[module_id] = {
            resolver.resolve_definition(s.symbol_id)
            for s in index.symbols
            if s.kind is SymbolKind.IMPORT
        }

    violations: list[Violation] = []
    for index in context.indexes:
        if index.file_path.endswith("__init__.py"):
            continue  # a barrel deep-importing its own submodules is how
            # barrels are built (also covered by the P_t == P_f skip below)
        module_id = _module_id_of(index)
        if module_id is None:
            continue
        root = configured_root or module_id.split(".")[0]
        importer_pkg = _package_under(module_id, root)
        if importer_pkg is None:
            continue
        for symbol in index.symbols:
            if symbol.kind is not SymbolKind.IMPORT or not symbol.imported_from:
                continue
            if symbol.import_confidence is not None:
                continue  # dynamic / synthetic import — out of scope
            # imported_from for `from a.b import C` is "a.b.C"; the module part
            # is everything before the final dotted segment.
            target_module, _, name = symbol.imported_from.rpartition(".")
            if not target_module:
                continue
            target_pkg = _package_under(target_module, root)
            if target_pkg is None or target_pkg == importer_pkg:
                continue  # external / root-level, or same-package sibling
            barrel_module = f"{root}.{target_pkg}"
            if target_module == barrel_module:
                continue  # already imports at the barrel level, not deeper
            exported = barrels.get(barrel_module)
            if not exported:
                continue  # target package has no curated barrel
            if resolver.resolve_definition(symbol.symbol_id) not in exported:
                continue  # the barrel does not re-export this name
            violations.append(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=BARREL_ONLY,
                    message=(
                        f"import '{name}' via the '{barrel_module}' barrel, "
                        f"not its internal module '{target_module}'"
                    ),
                )
            )
    return violations


def _module_id_of(index: FileIndex) -> str | None:
    """The index's MODULE symbol id (its dotted module path), or None."""
    return next(
        (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE),
        None,
    )


def _package_under(module_path: str, root: str) -> str | None:
    """First package segment of ``module_path`` beneath ``root``.

    ``None`` when ``module_path`` is outside ``root`` (external) or is the root
    package itself (no segment beneath it). Mirrors the helper of the same name
    in :mod:`pypeeker.check.rules` used by ``import-boundaries``.
    """
    parts = module_path.split(".")
    root_parts = root.split(".")
    if parts[: len(root_parts)] != root_parts:
        return None
    rest = parts[len(root_parts):]
    return rest[0] if rest else None
