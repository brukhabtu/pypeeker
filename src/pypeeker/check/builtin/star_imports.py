"""star-imports: flag ``from m import *`` and rewrite it to explicit names.

A star import leaves no name bindings the binder can see: every name the
star supplies shows up in the importing module as an *unresolved* bare
reference. The binder records the star itself as an IMPORT symbol with the
local name ``"*"`` and ``imported_from`` naming the (relative-resolved)
target module; this project-scoped rule joins that fact with cross-module
data — the unresolved bare names of the importing module matched against
the public module-level surface of the target — to report which names the
star actually supplies, and to rewrite the star into an explicit sorted
import list.

Attribution model (deliberate v1 simplifications, each making the rule
*more* conservative, never less):

* **First-star-wins.** With multiple star imports in one file, each used
  name is attributed to the first star-imported module (in file order) that
  defines it. Python's runtime semantics are *last*-wins shadowing, so the
  attribution can differ from runtime when two targets export the same
  name; multi-star findings therefore carry ``confidence=HEURISTIC`` and
  never get a remedy. Single-star files are ``DECLARED``.
* **``__all__`` filtering is unsupported.** When the target binds
  ``__all__``, the index records it only as a VARIABLE — its string
  contents are not available — so the rule matches the target's public
  (non-underscore) module-level symbols instead. Over-attribution is
  harmless for the rewrite: ``from m import name`` is valid even for names
  ``__all__`` omits.
* **Underscore names are out of scope.** A star never supplies ``_name``
  (absent ``__all__``, which v1 ignores), so underscore-prefixed unresolved
  references are excluded from both attribution and the fully-attributed
  proof below.

The remedy is a :class:`~pypeeker.intents.RewriteStarImportIntent`, executed
by :class:`~pypeeker.refactor.imports_ops.RewriteStarImportPlanner`: it
replaces the ``*`` token with the sorted used-name list, re-deriving those
names from the indexes *at apply time* and declining conservatively:

* ``stale-index`` / ``file-missing`` — the standard index-anchored
  discipline (the file's index hash must match the bytes on disk);
* ``ambiguous`` — zero used names (the rewrite would empty the import; the
  message suggests deleting it instead — no auto-delete), any unresolved
  bare name in the file that no star-imported module's surface accounts
  for (the star might supply it, e.g. through the target's own transitive
  star imports, so removing the star is unprovable), the target module not
  being indexed, or the file having grown a second star import since
  detection;
* ``text-mismatch`` — the ``*`` token is no longer where the index says,
  or its line no longer looks like ``from <module> import *``.

Opt-in (not enabled by default), like the other advisory builtin rules.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses
into the engine import and creates a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.models import Violation, with_remedy
from pypeeker.check.rules import register_rule
from pypeeker.intents import RewriteStarImportIntent
from pypeeker.models import (
    Confidence,
    FileIndex,
    Symbol,
    SymbolKind,
    is_unresolved_attr,
)

STAR_IMPORTS = "star-imports"


@register_rule(STAR_IMPORTS, scope="project")
def _star_imports(
    context: CheckContext, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag every star import, reporting which names it actually supplies.

    For each ``"*"`` IMPORT symbol the binder recorded, the used names are
    the importing module's unresolved bare references that match the target
    module's public module-level surface (first-star-wins across multiple
    stars — see the module docstring for the attribution model and its
    confidence consequences). Findings whose target module is not indexed
    report unknown names and are ``HEURISTIC``. Single-star ``DECLARED``
    findings with at least one used name carry a
    :class:`~pypeeker.intents.RewriteStarImportIntent` remedy. Takes no
    options.
    """
    modules = _module_indexes(context.indexes)
    violations: list[Violation] = []
    for index in context.indexes:
        stars = _star_symbols(index)
        if not stars:
            continue
        used_by, _unattributed = _attribute_names(
            stars, _unresolved_bare_names(index), modules
        )
        # First-star-wins attribution differs from Python's last-wins
        # shadowing, so multi-star findings are heuristic by construction.
        file_confidence = (
            Confidence.DECLARED if len(stars) == 1 else Confidence.HEURISTIC
        )
        for star in stars:
            line = star.location.span.start.line + 1
            if star.imported_from not in modules:
                violations.append(
                    Violation(
                        file_path=star.location.file_path,
                        line=line,
                        rule=STAR_IMPORTS,
                        message=(
                            f"star import from '{star.imported_from}' — "
                            "target module is not indexed; used names unknown"
                        ),
                        confidence=Confidence.HEURISTIC,
                    )
                )
                continue
            names = used_by.get(star.symbol_id, [])
            violation = Violation(
                file_path=star.location.file_path,
                line=line,
                rule=STAR_IMPORTS,
                message=_message(star.imported_from, names),
                confidence=file_confidence,
            )
            if names and file_confidence is Confidence.DECLARED:
                violation = with_remedy(
                    violation,
                    RewriteStarImportIntent(
                        f"{STAR_IMPORTS}:rewrite:{star.symbol_id}",
                        symbol_id=star.symbol_id,
                        module=star.imported_from,
                    ),
                )
            violations.append(violation)
    return violations


def _message(module: str, names: Sequence[str]) -> str:
    """Finding message: used-name count and list, or a deletion suggestion."""
    if not names:
        return (
            f"star import from '{module}' — 0 names actually used; "
            "consider deleting the import"
        )
    plural = "s" if len(names) != 1 else ""
    return (
        f"star import from '{module}' — {len(names)} name{plural} "
        f"actually used: {', '.join(names)}"
    )


# ── shared derivation helpers (attribution model) ───────────────────────────


def _star_symbols(index: FileIndex) -> list[Symbol]:
    """The file's ``"*"`` IMPORT symbols, in file order."""
    stars = [
        s
        for s in index.symbols
        if s.kind is SymbolKind.IMPORT and s.name == "*"
    ]
    stars.sort(
        key=lambda s: (s.location.span.start.line, s.location.span.start.column)
    )
    return stars


def _module_indexes(indexes: Sequence[FileIndex]) -> dict[str, FileIndex]:
    """Map each index's dotted module path to its :class:`FileIndex`."""
    out: dict[str, FileIndex] = {}
    for index in indexes:
        module_id = next(
            (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE),
            None,
        )
        if module_id is not None:
            out[module_id] = index
    return out


def _public_surface(index: FileIndex) -> frozenset[str]:
    """Public module-level names of ``index`` — what ``import *`` can supply.

    Includes every symbol kind bound at module level (imports re-export
    under star semantics), excluding underscore-prefixed names, the module
    symbol itself, and ``"*"`` star-import facts. ``__all__`` contents are
    not consulted (unsupported in v1 — see the module docstring).
    """
    module_id = next(
        (s.symbol_id for s in index.symbols if s.kind is SymbolKind.MODULE),
        None,
    )
    if module_id is None:
        return frozenset()
    return frozenset(
        s.name
        for s in index.symbols
        if s.parent_scope_id == module_id
        and s.kind is not SymbolKind.MODULE
        and s.name != "*"
        and not s.name.startswith("_")
    )


def _unresolved_bare_names(index: FileIndex) -> set[str]:
    """Bare unresolved reference names in ``index`` — star-supply candidates.

    A name the star supplies binds to nothing the binder can see, so it
    surfaces as an unresolved reference whose ``symbol_id`` is the bare
    name itself. ``<unresolved>.attr`` sentinels (attribute chains on an
    unresolved receiver) and underscore-prefixed names (never supplied by a
    star — see the module docstring) are excluded.
    """
    return {
        ref.symbol_id
        for ref in index.references
        if not ref.resolved
        and not is_unresolved_attr(ref.symbol_id)
        and ref.symbol_id.isidentifier()
        and not ref.symbol_id.startswith("_")
    }


def _attribute_names(
    stars: Sequence[Symbol],
    unresolved: set[str],
    modules: Mapping[str, FileIndex],
) -> tuple[dict[str, list[str]], list[str]]:
    """Attribute unresolved names to star imports, first-star-wins.

    Walks ``stars`` in file order; each remaining unresolved name is
    attributed to the first star whose (indexed) target module publicly
    defines it. Returns ``(used_by, unattributed)``: ``used_by`` maps each
    star's symbol_id to its sorted attributed names (stars with an
    unindexed target get no entry), ``unattributed`` is the sorted residue
    no star accounts for — the signal that removing a star is unprovable.
    """
    remaining = set(unresolved)
    used_by: dict[str, list[str]] = {}
    for star in stars:
        target = modules.get(star.imported_from)
        if target is None:
            continue
        supplied = sorted(remaining & _public_surface(target))
        used_by[star.symbol_id] = supplied
        remaining.difference_update(supplied)
    return used_by, sorted(remaining)
