"""Application service: JSON batch-intents parsing and check-rule expansion.

Turns a ``batch`` intents file's parsed JSON into
:class:`~pypeeker.intents.intents.Intent` objects. Depends on both
:mod:`pypeeker.dsl` (to expand a ``"fix"`` entry into the repairs a rule
currently proposes) and :mod:`pypeeker.intents` (the intent types
themselves), which is why it lives in ``app`` rather than in ``refactor``
(which may not import ``dsl``).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from pypeeker.app.check_run2 import finding_order, load_plugins
from pypeeker.dsl import (
    Corpus,
    UnknownExpressionError,
    dsl_rule,
    install_expressions,
    read_config,
)
from pypeeker.intents import (
    ExtractMethodIntent,
    ExtractVariableIntent,
    InlineVariableIntent,
    Intent,
    MoveSymbolIntent,
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
    rule_name: str, base_id: str, store: IndexStore, root: Path, where: str
) -> list[Intent]:
    """The repair intents ``rule_name`` currently proposes, in report order.

    Runs only ``rule_name`` (the project's configured options for it still
    apply) and takes the :attr:`~pypeeker.dsl.Remediation.intent` off every
    repair it yields, re-identified as ``{base_id}-{n}`` so ``deps`` naming
    the *entry* resolve to every intent it expanded into. The repair's own
    ``intent_id`` (the derived ``<rule>:<mutation>:<anchor>``, e.g.
    ``unused-imports:remove:mod:os``) is deliberately replaced: within a
    batch, intent ids are the dependency namespace, and the entry that
    produced them is what a plan file can name.

    There is no eligibility test here. A repair exists only for a row that
    cleared its mutation's confidence floor and every precondition, so the
    gate the frozen path spelled as ``auto_fixable`` is an attribute of the
    mutation value and is already applied by the time these rows arrive.

    The repairs are sorted by :func:`~pypeeker.app.check_run2.finding_order`
    before numbering: the frozen expansion consumed the check engine's
    ``(path, line, rule, message)`` order, so ``-1``, ``-2``, ... follow report
    order rather than per-rule row order.

    Unlike ``check --fix`` this path plans nothing here — the intents travel
    into :func:`~pypeeker.refactor.batch.run_batch` and are re-planned by
    their registered planners at their turn in the schedule, against the
    simulated state the intents before them produced.

    A ``rule`` naming no ported rule is an ordinary malformed-entry error, so
    it is re-raised as the :class:`ValueError` this module's contract promises
    for any bad input, prefixed with ``where``. :func:`dsl_rule`'s own
    :class:`~pypeeker.dsl.UnknownExpressionError` is not a ``ValueError`` and
    would escape ``cli.batch``'s handler as a traceback; a traceback is not one
    of this CLI's output shapes, and the entry-naming ``ValueError`` lands in
    the frozen ``intents-invalid`` envelope with the known rule ids listed.
    """
    install_expressions()
    src, _rules, plugins, options = read_config(root)
    load_plugins(plugins, store.project_root)
    try:
        rule = dsl_rule(rule_name)
    except UnknownExpressionError as exc:
        raise ValueError(f"{where}: {exc}") from exc
    repairs = sorted(
        rule.remediations(options.get(rule_name, {}), Corpus(store, src)),
        key=lambda repair: finding_order(repair.finding),
    )
    return [
        dataclasses.replace(repair.intent, intent_id=f"{base_id}-{n}")
        for n, repair in enumerate(repairs, start=1)
    ]


def build_batch_intents(entries: object, store: IndexStore, root: Path) -> list[Intent]:
    """Intent objects from a batch intents file's parsed JSON.

    ``entries`` must be a list of objects, each with a ``kind`` of
    ``"rename"``, ``"inline-variable"``, ``"extract-variable"``,
    ``"extract-method"``, ``"move-symbol"`` or ``"fix"`` plus that kind's
    parameters (mirroring
    the corresponding single-op CLI command's arguments; ``fix`` takes ``rule`` and
    expands into one intent per certain-confidence repair the rule proposes,
    via :func:`_expand_fix_rule`, which refuses a ``rule`` naming no ported
    rule rather than expanding to nothing). Optional ``id`` names the intent (default
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
        elif kind == "move-symbol":
            intents = [
                MoveSymbolIntent(
                    entry_id,
                    _required_str(entry, "symbol_id", where),
                    _required_str(entry, "dest_module", where),
                )
            ]
        elif kind == "fix":
            intents = list(
                _expand_fix_rule(
                    _required_str(entry, "rule", where), entry_id, store, root, where
                )
            )
        else:
            raise ValueError(
                f"{where}: unknown kind (expected rename, inline-variable, "
                "extract-variable, extract-method, move-symbol, or fix)"
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
