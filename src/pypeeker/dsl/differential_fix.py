"""The new engine's *repair* surface, for the differential oracle to grade.

The write-half twin of :mod:`pypeeker.dsl.differential`. That module answers
"what does the new engine report?"; this one answers "what does it propose to
repair?" — the second question ``check --fix`` asks, and the one phase 4's
mutation terminals exist to make answerable.

The split of labour is deliberate and is what keeps the layering honest:

* **this module is ``dsl``-only.** It runs the ported rules over one corpus and
  hands back the :class:`~pypeeker.intents.Intent` each remedied finding names,
  plus the finding's rendered text. It never plans, never de-conflicts and
  never writes a byte — ``dsl`` may not import ``refactor``, and a planner is
  the only code in the system that writes bytes;
* **the composition lives in ``scripts/dsl-fix-engine.py``**, outside ``src/``
  and therefore outside the import-boundaries table: that script is the one
  place that holds both these intents and
  :func:`pypeeker.app.plan_intent_fixes`. Keeping it there means phase 4 adds
  no allowance to ``app`` and no ``dsl``→``refactor`` edge anywhere.

The ``__main__`` guard is likewise not here, for the reason
:mod:`pypeeker.dsl.differential` records: a guard under ``src/`` fails the
zero-baseline self-lint twice over.

One :class:`~pypeeker.dsl.corpus.Corpus` is threaded through every rule in a
run, and each rule is asked only for its :meth:`~pypeeker.dsl.DslRule.remediations`
— never for its findings, which this pass does not report. The corpus memos its
sweeps, so a rule graded on both halves in one process re-reads warm rows;
building a corpus per rule would make that memo cold and double the run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.differential import _read_config, _require_index
from pypeeker.dsl.library import install_expressions
from pypeeker.dsl.rules import dsl_rule
from pypeeker.intents import Intent
from pypeeker.storage import IndexStore

__all__ = ["RepairSet", "collect_repairs"]


@dataclass(frozen=True)
class RepairSet:
    """Every repair the ported rules propose over one target, and its finding.

    ``intents`` is flat and unordered — the contract
    :meth:`pypeeker.dsl.Application.intents` publishes, and what the existing
    batch machinery consumes. Rule order is preserved only because iterating a
    rule table is ordered; nothing downstream may rely on it, and
    :func:`pypeeker.app.plan_intent_fixes` re-orders by ``(file, start,
    intent_id)`` before it de-conflicts.

    ``violations`` maps each intent's derived id to the rendered text of the
    finding that earned it — ``Finding.__str__``, which is byte-identical to
    the frozen ``Violation.__str__``. The oracle compares that string per fix,
    which re-grades the finding's message *and* its confidence tier at the fix
    layer: a repair attached to the wrong row is caught even when the two
    engines' fix id lists agree.
    """

    intents: tuple[Intent, ...]
    violations: Mapping[str, str]


def collect_repairs(target: Path, rules: tuple[str, ...]) -> RepairSet:
    """Run ``rules`` over the index in ``target`` and collect their remedies.

    Args:
        target: the project root holding a ``.pypeeker/`` index — the same
            directory the old engine was just run over.
        rules: the rule ids to evaluate, in the order the caller wants them
            attempted. A rule that declares no mutation contributes nothing.

    Returns:
        The :class:`RepairSet`: every :class:`~pypeeker.dsl.Remediation` the
        rules produced, with the finding text each came from.

    Raises:
        pypeeker.dsl.differential._NoIndexError: ``target`` is not a directory,
            or holds no index. Refused rather than reported as "no repairs" for
            the same reason the findings side refuses it — an empty result must
            not be able to mean "read nothing".
    """
    install_expressions()
    src_roots, options = _read_config(target)
    store = IndexStore(target)
    _require_index(target, store)
    corpus = Corpus(store, src_roots)
    intents: list[Intent] = []
    violations: dict[str, str] = {}
    for name in rules:
        rule = dsl_rule(name)
        for repair in rule.remediations(options.get(name, {}), corpus):
            intents.append(repair.intent)
            violations[repair.fix_id] = str(repair.finding)
    return RepairSet(
        intents=tuple(intents), violations=MappingProxyType(dict(violations))
    )
