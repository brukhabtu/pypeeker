"""unused-imports: flag import bindings that nothing in their file uses.

A consumer module's references bind to its local IMPORT symbol (not to the
imported definition), so "is this import used?" is a purely file-local
question: an IMPORT symbol with zero references binding to it in its own
file is dead weight. Each finding carries a
:class:`~pypeeker.check.fixes.RemoveUnusedImportFix` that deletes the
binding (the whole line for a single-name import, just the name entry on a
multi-name line).

Conservative exclusions, by design:

* ``__init__.py`` files are skipped entirely — barrels re-export by design,
  and an "unused" import there is usually the package's public API surface;
* files that bind ``__all__`` are skipped — string re-exports are invisible
  to reference analysis, so any import in such a file may be consumed via
  ``from pkg import *`` or documented API listings;
* ``__future__`` imports are skipped — they act by existing;
* underscore-prefixed bindings (``import x as _x``) are skipped — the
  underscore is a deliberate "imported for side effects / re-export" signal.

Findings in a file referencing ``getattr``/``globals``/``vars``/``locals``
carry ``confidence=HEURISTIC`` (``globals()["os"]`` can consume an import
invisibly), so they are hidden by default and never auto-fixed.

Opt-in (not enabled by default), like the other advisory builtin rules.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses into
the engine import and creates a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pypeeker.check.fixes import RemoveUnusedImportFix, with_fix
from pypeeker.check.models import Violation
from pypeeker.check.rules import _DYNAMIC_ACCESS_BUILTIN_IDS, register_rule
from pypeeker.models.capabilities import Confidence
from pypeeker.models.index import FileIndex
from pypeeker.models.symbols import SymbolKind

UNUSED_IMPORTS = "unused-imports"


@register_rule(UNUSED_IMPORTS, scope="file")
def unused_imports(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag IMPORT symbols with no references binding to them in their file.

    See the module docstring for the exclusion list (``__init__.py``,
    ``__all__`` files, ``__future__`` imports, underscore-prefixed bindings)
    and the dynamic-access confidence downgrade. Takes no options.
    """
    if file_index.file_path.endswith("__init__.py"):
        return []  # barrels re-export by design
    if any(s.name == "__all__" for s in file_index.symbols):
        return []  # string re-exports are invisible to reference analysis

    used = {ref.symbol_id for ref in file_index.references}
    confidence = (
        Confidence.HEURISTIC
        if any(ref.symbol_id in _DYNAMIC_ACCESS_BUILTIN_IDS for ref in file_index.references)
        else Confidence.DECLARED
    )

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind is not SymbolKind.IMPORT:
            continue
        if symbol.name.startswith("_"):
            continue
        if symbol.imported_from and symbol.imported_from.split(".", 1)[0] == "__future__":
            continue
        if symbol.symbol_id in used:
            continue
        violations.append(
            with_fix(
                Violation(
                    file_path=symbol.location.file_path,
                    line=symbol.location.span.start.line + 1,
                    rule=UNUSED_IMPORTS,
                    message=f"import '{symbol.name}' is unused in this module",
                    confidence=confidence,
                ),
                RemoveUnusedImportFix(
                    file_path=symbol.location.file_path,
                    symbol_id=symbol.symbol_id,
                    name=symbol.name,
                ),
            )
        )
    return violations
