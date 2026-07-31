"""docstring-drift: documented parameters vs the actual signature (TASK-93).

A docstring that documents a parameter the function no longer has (or has
under a different name) is actively misleading — worse than no docstring.
darglint is unmaintained and ruff's coverage of this is shallow, so this rule
closes the gap from the index: every FUNCTION/METHOD symbol carries its
``docstring`` and its parameters are PARAMETER symbols whose
``parent_scope_id`` is the function's scope, which is all the rule needs.

Scope of ambition (deliberate): **parameter-name drift only**, in the three
common docstring styles. Type drift, return/raises sections, multi-name numpy
entries (``x, y : int``) and exotic markup are out of scope.

The params-section parsers (the three recognized styles, autodetection, and
stars normalization) live in :mod:`pypeeker.analysis.docstrings`, not here:
the planner that repairs what this rule flags has to re-derive the drift with
the identical parser, and it lives in ``refactor``, which may not import
``check``. See that module for the recognized shapes and their limits.

Two violation kinds:

* documented-but-absent — the docstring documents a parameter the signature
  does not have. Always reported when a params section is recognized.
* present-but-undocumented — a signature parameter missing from a recognized
  params section. Gated by ``require-complete`` (default false), and ONLY
  emitted when a params section exists: demanding sections where none exist
  is ``require-docstrings``' turf, not drift.

``self``/``cls`` as the leading parameter is never expected in a docstring
and is skipped on the signature side.

The remedy (conservative): when exactly ONE documented name is absent from
the signature and exactly ONE signature parameter is undocumented — the shape
of "the parameter was renamed and the docstring did not follow" — the
documented-but-absent violation carries a
:class:`~pypeeker.intents.RenameDocstringParamIntent`. Zero or several
undocumented parameters, or several stale names, means "which parameter was
renamed" is not answerable at all, so no remedy is attached and the finding
is report-only.

Everything else is settled by the planner, not here.
:class:`~pypeeker.refactor.docstring_ops.DocstringParamRenamePlanner` is
index-anchored: it re-reads the file, refuses on a stale index, re-derives
the drift from the CURRENT docstring, and rewrites only the name token — so
a drift that changed shape, or a stale name occurring more than once in the
docstring, surfaces as a *reported decline* (``"stale-index"``,
``"text-mismatch"``, ``"ambiguous"``) rather than as a silently missing
repair. That is deliberate: ``check --fix``'s ``declined`` list is how a
consumer learns why a finding was not auto-fixed, and an ambiguity the rule
could also see at detection time still belongs there.

Options (``[tool.pypeeker.docstring-drift]``):
    ``style``            — force one of ``google`` / ``numpy`` / ``sphinx``
                           instead of autodetecting (unknown values fall back
                           to autodetect).
    ``require-complete`` — also flag signature parameters missing from an
                           existing params section. Default false.
    ``allow``            — fnmatch patterns matched against the function's
                           ``symbol_id`` (``"pkg.mod:func"``) or its module
                           path; matching functions are never flagged.

Advisory and **opt-in** (not enabled by default): docstring conventions vary
per project, and the parsers cover the common shapes, not every dialect.

Import discipline: imports only concrete ``pypeeker.check.*`` modules (plus
the ``analysis`` and ``intents`` barrels) — importing ``pypeeker.check``
itself from a builtin rule module recurses into the engine import and creates
a cycle.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any

from pypeeker.analysis import (
    DOCSTRING_STYLES,
    param_drift,
    parse_documented_params,
    signature_params,
)
from pypeeker.check.models import Violation, with_remedy
from pypeeker.check.rules import register_rule
from pypeeker.intents import RenameDocstringParamIntent
from pypeeker.models import FileIndex, SymbolKind

DOCSTRING_DRIFT = "docstring-drift"


@register_rule(DOCSTRING_DRIFT, scope="file")
def _docstring_drift(
    file_index: FileIndex, options: Mapping[str, Any]
) -> list[Violation]:
    """Flag docstring params sections that drifted from the signature.

    See the module docstring for the recognized styles, the two violation
    kinds, the repair conditions, and the options.
    """
    style_opt = options.get("style")
    style = style_opt if style_opt in DOCSTRING_STYLES else None
    require_complete = bool(options.get("require-complete"))
    allow = _as_str_list(options.get("allow"))

    violations: list[Violation] = []
    for symbol in file_index.symbols:
        if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            continue
        if not symbol.docstring:
            continue
        if _matches_any(symbol.symbol_id, allow):
            continue
        section = parse_documented_params(symbol.docstring, style)
        if section is None:
            continue  # no params section: require-docstrings' turf, not drift
        signature = signature_params(file_index, symbol)
        ghosts, missing = param_drift(section, signature)

        # The repair applies only to the unambiguous rename shape: one stale
        # documented name, one undocumented signature parameter. Everything
        # finer-grained (does the name occur once? does the drift still hold
        # against current bytes?) is the planner's call, so that a refusal is
        # reported rather than silently withheld.
        renameable = len(ghosts) == 1 and len(missing) == 1

        for ghost in ghosts:
            violation = Violation(
                file_path=symbol.location.file_path,
                line=symbol.location.span.start.line + 1,
                rule=DOCSTRING_DRIFT,
                message=(
                    f"docstring of {symbol.kind.value} '{symbol.name}' "
                    f"documents parameter '{ghost}' which does not exist"
                ),
            )
            if renameable:
                violation = with_remedy(
                    violation,
                    RenameDocstringParamIntent(
                        f"{DOCSTRING_DRIFT}:rename-param:{symbol.symbol_id}:{ghost}",
                        symbol_id=symbol.symbol_id,
                        old_param=ghost,
                        new_param=missing[0],
                        style=section.style,
                    ),
                )
            violations.append(violation)

        if require_complete:
            for name in missing:
                violations.append(
                    Violation(
                        file_path=symbol.location.file_path,
                        line=symbol.location.span.start.line + 1,
                        rule=DOCSTRING_DRIFT,
                        message=(
                            f"docstring of {symbol.kind.value} "
                            f"'{symbol.name}' does not document parameter "
                            f"'{name}'"
                        ),
                    )
                )
    return sorted(violations)


# ── option coercion ─────────────────────────────────────────────────────────


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
