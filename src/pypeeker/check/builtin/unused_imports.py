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
  underscore is a deliberate "imported for side effects / re-export" signal;
* dotted-name imports (``import a.b.c``) are skipped — they bind a namespace
  whose uses do not bind back to the dotted symbol, and are commonly imported
  for a submodule's side effects (registration, etc.), so "unused" is unreliable
  and removing one is risky;
* imports used only inside a quoted forward-reference annotation
  (``x: "Node | None"``, ``x: list["Foo"]``) are skipped — the binder does not
  descend into string literals, so those uses are otherwise invisible.

Findings in a file referencing ``getattr``/``globals``/``vars``/``locals``
carry ``confidence=HEURISTIC`` (``globals()["os"]`` can consume an import
invisibly), so they are hidden by default and never auto-fixed.

Opt-in (not enabled by default), like the other advisory builtin rules.

Import discipline: imports only concrete ``pypeeker.check.*`` modules —
importing ``pypeeker.check`` itself from a builtin rule module recurses into
the engine import and creates a cycle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pypeeker.check.fixes import RemoveUnusedImportFix, with_fix
from pypeeker.check.models import Violation
from pypeeker.check.rules import _DYNAMIC_ACCESS_BUILTIN_IDS, register_rule
from pypeeker.models import Confidence, FileIndex, SymbolKind

UNUSED_IMPORTS = "unused-imports"

_QUOTED = re.compile(r"""(['"])(.*?)\1""")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _forward_ref_names(file_index: FileIndex) -> set[str]:
    """Names that appear inside quoted (forward-reference) type annotations.

    A name used only in a string annotation — ``x: "Node | None"`` or the
    nested ``x: list["Foo"]`` — is invisible to reference analysis (the binder
    does not descend into string literals), so its import would otherwise read
    as unused. Collect every identifier inside a quoted substring of any
    annotation so such imports are not flagged. Over-collecting a plain string
    value (``Literal["x"]``) is harmless: it can only *spare* an import.
    """
    names: set[str] = set()
    for symbol in file_index.symbols:
        ann = symbol.type_annotation
        if ann is None or not ann.raw:
            continue
        for _quote, inner in _QUOTED.findall(ann.raw):
            names.update(_IDENTIFIER.findall(inner))
    return names


@register_rule(UNUSED_IMPORTS, scope="file")
def _unused_imports(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag IMPORT symbols with no references binding to them in their file.

    See the module docstring for the exclusion list (``__init__.py``,
    ``__all__`` files, ``__future__`` imports, underscore-prefixed bindings,
    dotted-name/side-effect imports, and forward-reference-only imports) and
    the dynamic-access confidence downgrade. Takes no options.
    """
    if file_index.file_path.endswith("__init__.py"):
        return []  # barrels re-export by design
    if any(s.name == "__all__" for s in file_index.symbols):
        return []  # string re-exports are invisible to reference analysis

    used = {ref.symbol_id for ref in file_index.references}
    forward_ref_names = _forward_ref_names(file_index)
    confidence = (
        Confidence.HEURISTIC
        if any(ref.symbol_id in _DYNAMIC_ACCESS_BUILTIN_IDS for ref in file_index.references)
        else Confidence.DECLARED
    )

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind is not SymbolKind.IMPORT:
            continue
        if symbol.name == "*":
            # Star imports never bind by name; the star-imports rule owns them.
            continue
        if symbol.import_confidence is not None:
            # Dynamic imports (importlib.import_module/__import__) bind no
            # name, so "unused" is meaningless — and there is no import
            # statement a fix could remove.
            continue
        if symbol.name.startswith("_"):
            continue
        if "." in symbol.name:
            # ``import a.b.c`` binds a namespace whose uses (``a.b.c.x``) do not
            # bind back to this dotted symbol, and such imports are commonly for
            # a submodule's side effects (e.g. registration). Removing one is
            # risky and "unused" cannot be told reliably — leave it alone.
            continue
        if symbol.imported_from and symbol.imported_from.split(".", 1)[0] == "__future__":
            continue
        if symbol.name in forward_ref_names:
            # Used only inside a string/forward-ref annotation (invisible to
            # reference analysis) — not actually unused.
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
