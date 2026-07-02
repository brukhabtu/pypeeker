"""Application service: JSON batch-intents parsing and check-rule expansion.

Turns a ``plan-batch`` intents file's parsed JSON into
:class:`~pypeeker.refactor.intents.Intent` objects. Depends on both
:mod:`pypeeker.check` (to expand a ``"fix"`` entry into the autofixes a rule
currently reports) and :mod:`pypeeker.refactor` (the intent types
themselves), which is why it lives in ``app`` rather than in ``refactor``
(which may not import ``check``).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from pypeeker.check import CheckEngine, load_config
from pypeeker.models.capabilities import Confidence
from pypeeker.refactor.intents import (
    ExtractMethodIntent,
    ExtractVariableIntent,
    FixIntent,
    InlineVariableIntent,
    Intent,
    RenameIntent,
)
from pypeeker.storage import IndexStore

__all__ = ["build_batch_intents"]


def _required_str(entry: dict, key: str, where: str) -> str:
    """A non-empty string value for ``key``, or a ValueError naming the entry."""
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: missing or invalid '{key}' (expected a string)")
    return value


def _required_int(entry: dict, key: str, where: str) -> int:
    """An integer value for ``key``, or a ValueError naming the entry."""
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{where}: missing or invalid '{key}' (expected an integer)")
    return value


def _position(entry: dict, key: str, where: str) -> tuple[int, int]:
    """A 0-indexed ``(line, col)`` from a ``"line:col"`` string or ``[line, col]``."""
    value = entry.get(key)
    if isinstance(value, str):
        line, sep, col = value.partition(":")
        if sep:
            try:
                return int(line), int(col)
            except ValueError:
                pass
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        return value[0], value[1]
    raise ValueError(f"{where}: '{key}' must be a 'line:col' string or [line, col]")


def _expand_fix_rule(
    rule_name: str, base_id: str, store: IndexStore, root: Path
) -> list[FixIntent]:
    """FixIntents for every certain-confidence autofix ``rule_name`` reports now.

    Runs the check engine with only ``rule_name`` enabled (the project's
    configured options for it still apply) and wraps the fix attached to each
    DECLARED-confidence violation as a deferred
    :class:`~pypeeker.refactor.intents.FixIntent` named ``{base_id}-{n}``.
    The eligibility filter (fix present + DECLARED confidence) deliberately
    mirrors :func:`~pypeeker.app.check_fixes.apply_check_fixes` — kept as
    light duplication because that path plans and applies immediately while
    this one defers planning to batch materialization, so the fix objects
    (not their edits) are what travel.
    """
    config = dataclasses.replace(load_config(root), rules=(rule_name,))
    violations = CheckEngine(store, config).run()
    fixes = [
        v.fix
        for v in violations
        if v.fix is not None and v.confidence is Confidence.DECLARED
    ]
    return [
        FixIntent(f"{base_id}-{n}", fix=fix) for n, fix in enumerate(fixes, start=1)
    ]


def build_batch_intents(entries: object, store: IndexStore, root: Path) -> list[Intent]:
    """Intent objects from a plan-batch intents file's parsed JSON.

    ``entries`` must be a list of objects, each with a ``kind`` of
    ``"rename"``, ``"inline-variable"``, ``"extract-variable"``,
    ``"extract-method"`` or ``"fix"`` plus that kind's parameters (mirroring
    the corresponding plan-* CLI arguments; ``fix`` takes ``rule`` and
    expands into one intent per certain-confidence autofix the rule reports,
    via :func:`_expand_fix_rule`). Optional ``id`` names the intent (default
    ``{kind}-{position}``); optional ``deps`` lists ids that must execute
    first — a dep naming a fix entry resolves to every intent the entry
    expanded into. Raises :class:`ValueError` with an entry-naming message on
    any malformed input.
    """
    if not isinstance(entries, list):
        raise ValueError("intents file must contain a JSON list of intent objects")

    built: list[tuple[dict, list[Intent]]] = []
    expansion: dict[str, list[str]] = {}
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"intent #{number} must be a JSON object")
        kind = entry.get("kind")
        where = f"intent #{number} ({kind!r})"
        entry_id = entry.get("id") or f"{kind}-{number}"
        if not isinstance(entry_id, str):
            raise ValueError(f"{where}: 'id' must be a string")
        if entry_id in expansion:
            raise ValueError(f"{where}: duplicate intent id '{entry_id}'")
        deps = entry.get("deps", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError(f"{where}: 'deps' must be a list of intent ids")

        if kind == "rename":
            intents: list[Intent] = [
                RenameIntent(
                    entry_id,
                    _required_str(entry, "symbol_id", where),
                    _required_str(entry, "new_name", where),
                    include_file=bool(entry.get("include_file", False)),
                    include_exports=bool(entry.get("include_exports", False)),
                    include_receivers=bool(entry.get("include_receivers", False)),
                    keep_export=bool(entry.get("keep_export", False)),
                    allow_override_rename=bool(
                        entry.get("allow_override_rename", False)
                    ),
                )
            ]
        elif kind == "inline-variable":
            intents = [
                InlineVariableIntent(entry_id, _required_str(entry, "symbol_id", where))
            ]
        elif kind == "extract-variable":
            intents = [
                ExtractVariableIntent(
                    entry_id,
                    _required_str(entry, "file_path", where),
                    _position(entry, "start", where),
                    _position(entry, "end", where),
                    _required_str(entry, "new_name", where),
                )
            ]
        elif kind == "extract-method":
            intents = [
                ExtractMethodIntent(
                    entry_id,
                    _required_str(entry, "file_path", where),
                    _required_int(entry, "start_line", where),
                    _required_int(entry, "end_line", where),
                    _required_str(entry, "new_name", where),
                )
            ]
        elif kind == "fix":
            intents = list(
                _expand_fix_rule(
                    _required_str(entry, "rule", where), entry_id, store, root
                )
            )
        else:
            raise ValueError(
                f"{where}: unknown kind (expected rename, inline-variable, "
                "extract-variable, extract-method, or fix)"
            )
        expansion[entry_id] = [intent.intent_id for intent in intents]
        built.append((entry, intents))

    result: list[Intent] = []
    for entry, intents in built:
        resolved: set[str] = set()
        for dep in entry.get("deps", []):
            resolved.update(expansion.get(dep, [dep]))
        for intent in intents:
            if resolved:
                intent = dataclasses.replace(intent, deps=frozenset(resolved))
            result.append(intent)
    return result
