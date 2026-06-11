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
  never get the fix. Single-star files are ``DECLARED``.
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

The fix (:class:`RewriteStarImportFix`) replaces the ``*`` token with the
sorted used-name list, declining conservatively:

* ``STALE_INDEX`` / ``FILE_MISSING`` — the standard index-anchored
  discipline (the file's index hash must match the bytes on disk);
* ``AMBIGUOUS`` — zero used names (the rewrite would empty the import; the
  message suggests deleting it instead — no auto-delete), any unresolved
  bare name in the file that no star-imported module's surface accounts
  for (the star might supply it, e.g. through the target's own transitive
  star imports, so removing the star is unprovable), the target module not
  being indexed, or the file having grown a second star import since
  detection;
* ``TEXT_MISMATCH`` — the ``*`` token is no longer where the index says,
  or its line no longer looks like ``from <module> import *``.

Opt-in (not enabled by default), like the other advisory builtin rules.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses
into the engine import and creates a cycle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pypeeker.check.context import CheckContext
from pypeeker.check.fixes import (
    DeclineReason,
    FixDeclined,
    FixPlan,
    _current_state,
    _position_to_byte_offset,
    with_fix,
)
from pypeeker.check.models import Violation
from pypeeker.check.rules import register_rule
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.symbol_id import is_unresolved_attr
from pypeeker.models.symbols import Symbol, SymbolKind
from pypeeker.models.transaction import EditEntry, EditOp
from pypeeker.storage.index_store import IndexStore

STAR_IMPORTS = "star-imports"

# The bytes that must precede the ``*`` token on its line for the fix to
# rewrite it: a plain single-line ``from <module> import *`` statement
# (relative dots allowed). Anything else — continuations, parentheses, a
# semicolon-joined statement — fails verification and declines.
_STAR_LINE_PREFIX = re.compile(rb"\s*from\s+[.\w]+\s+import\s+$")


@register_rule(STAR_IMPORTS, scope="project")
def star_imports(
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
    :class:`RewriteStarImportFix`. Takes no options.
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
                violation = with_fix(
                    violation,
                    RewriteStarImportFix(
                        file_path=star.location.file_path,
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


@dataclass(frozen=True)
class RewriteStarImportFix:
    """Rewrite ``from m import *`` to ``from m import a, b, c`` (sorted).

    Anchored on the ``"*"`` IMPORT symbol id: ``plan()`` re-reads the file
    through the hash-verified current index (the same discipline as the
    index-anchored fixes in :mod:`pypeeker.check.fixes`), re-derives the
    used names from the *current* indexes in the store, text-verifies that
    the ``*`` token still sits on a plain ``from <module> import *`` line,
    and emits one REPLACE edit swapping the ``*`` for the sorted name list
    — indentation, the module text as written (relative imports stay
    relative), and any trailing comment are untouched. Decline conditions
    are listed in the module docstring.
    """

    file_path: str  # project-root-relative, like Violation.file_path
    symbol_id: str  # the "*" IMPORT symbol recorded by the binder
    module: str  # the resolved target module (for messages)

    @property
    def fix_id(self) -> str:
        """Stable id: ``star-imports:rewrite:<symbol_id>``."""
        return f"star-imports:rewrite:{self.symbol_id}"

    @property
    def description(self) -> str:
        """One-line summary of the rewrite."""
        return (
            f"rewrite the star import from '{self.module}' as an explicit "
            "sorted import list"
        )

    def plan(self, store: IndexStore) -> FixPlan | FixDeclined:
        """Re-derive used names from the current index and rewrite the star."""
        state = _current_state(store, self.file_path, self.fix_id)
        if isinstance(state, FixDeclined):
            return state
        content, index = state

        star = next(
            (
                s
                for s in index.symbols
                if s.symbol_id == self.symbol_id
                and s.kind is SymbolKind.IMPORT
                and s.name == "*"
            ),
            None,
        )
        if star is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                f"star import '{self.symbol_id}' is no longer in the index",
            )
        stars = _star_symbols(index)
        if len(stars) > 1:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"{self.file_path} now has {len(stars)} star imports; "
                "first-star-wins attribution is heuristic there",
            )

        modules = _module_indexes(_load_indexes(store, index))
        if star.imported_from not in modules:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"target module '{star.imported_from}' is not indexed; "
                "the names the star supplies cannot be derived",
            )
        used_by, unattributed = _attribute_names(
            stars, _unresolved_bare_names(index), modules
        )
        if unattributed:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                "unresolved name(s) "
                + ", ".join(f"'{name}'" for name in unattributed)
                + " match no star-imported module's public surface — the "
                "star import may still supply them (e.g. via a transitive "
                "star import), so the rewrite cannot be proven complete",
            )
        names = used_by.get(star.symbol_id, [])
        if not names:
            return FixDeclined(
                self.fix_id,
                DeclineReason.AMBIGUOUS,
                f"no names from '{star.imported_from}' are used; delete the "
                "star import instead of rewriting it",
            )

        offset = _position_to_byte_offset(
            content, star.location.span.start.line, star.location.span.start.column
        )
        if offset is None or content[offset : offset + 1] != b"*":
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "the '*' token is not at its indexed location",
            )
        line_start = content.rfind(b"\n", 0, offset) + 1
        if _STAR_LINE_PREFIX.fullmatch(content[line_start:offset]) is None:
            return FixDeclined(
                self.fix_id,
                DeclineReason.TEXT_MISMATCH,
                "the indexed line is not a plain 'from <module> import *' "
                "statement",
            )

        edit = EditEntry(
            op=EditOp.REPLACE,
            file=self.file_path,
            start=offset,
            end=offset + 1,
            old="*",
            new=", ".join(names),
            file_hash=IndexStore.compute_file_hash(
                store.project_root / self.file_path
            ),
        )
        return FixPlan(self.fix_id, self.description, [edit])


# ── shared derivation helpers (rule detection and fix planning) ─────────────


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


def _load_indexes(store: IndexStore, current: FileIndex) -> list[FileIndex]:
    """Every index in the store, with ``current`` standing in for its own file.

    ``plan()`` already hash-verified ``current``; the other indexes are read
    as stored — they only provide target-module surfaces, never byte
    offsets, so staleness there degrades name derivation, not edit safety.
    """
    indexes: list[FileIndex] = []
    for path in store.list_indexed_files():
        index = current if path == current.file_path else store.load(path)
        if index is not None:
            indexes.append(index)
    return indexes


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
