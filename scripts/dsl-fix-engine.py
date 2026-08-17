#!/usr/bin/env python3
"""The differential harness's new-engine entry point for ``check --fix``.

The write-half twin of ``scripts/dsl-engine.py``. That launcher prints what the
new engine *reports*; this one prints what it proposes to *repair*, in a shape
``scripts/differential-check.py`` compares against the frozen
``pypeeker check --fix --plan`` report plus its ``transactions show``.

**The composition lives here, deliberately.** ``pypeeker.dsl`` produces the
intents (:func:`pypeeker.dsl.collect_repairs`, imported through the barrel) and
``pypeeker.app`` turns a flat list of intents into one ``check-fix``
transaction (:func:`pypeeker.app.plan_intent_fixes`); the two meet in this
file, which sits outside ``src/`` and therefore outside
``[tool.pypeeker.import-boundaries]``. That is what lets phase 4 restore
``check --fix`` through the new engine while ``dsl`` still imports neither
``check`` nor ``refactor``, and ``app`` still does not import ``dsl``.

Two invariants this launcher must not relax:

* **Nothing is applied.** ``plan_only=True``: the pass plans, refuses and
  de-conflicts exactly as a real run would, and writes the transaction PENDING.
  The old side is run with ``--plan`` for the same reason, so neither engine
  mutates the materialized target and the two are graded on the same state.
* **The transaction goes to a throwaway store**, never the target's own
  ``.pypeeker/``. By the time this runs, the old side has already left its own
  PENDING ``check-fix`` transaction in there; writing a second one into the
  same store invites a later reader to pick up the wrong id.

Output contract, one JSON object on stdout::

    {"schema": 1,
     "fixes": [{"fix_id": ..., "description": ..., "violation": ...}],
     "skipped_conflicts": [{"fix_id": ..., "description": ..., "violation": ...}],
     "declined": [{"fix_id": ..., "reason": ..., "detail": ...}],
     "edits": [{"file": ..., "start": ..., "end": ..., "replacement": ...}]}

``fixes`` is ordered — it is the conflict-resolution order, so comparing the
list rather than the set grades the de-conflict itself — and ``edits`` is the
transaction's edit list in the order it was written, which is the order the
repairs landed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from pypeeker.app import plan_intent_fixes
from pypeeker.dsl import collect_repairs
from pypeeker.storage import IndexStore, TransactionStore

SCHEMA = 1


def run(target: Path, rules: tuple[str, ...]) -> dict:
    """Collect every proposed repair over ``target`` and plan it into one transaction."""
    repairs = collect_repairs(target, rules)
    store = IndexStore(target)
    with tempfile.TemporaryDirectory(prefix="dsl-fix-engine-") as tmp:
        tx_store = TransactionStore(Path(tmp))
        outcome = plan_intent_fixes(
            store, tx_store, repairs.intents, plan_only=True
        )
        edits: list[dict] = []
        if outcome.tx_id is not None:
            loaded = tx_store.load(outcome.tx_id)
            if loaded is None:  # pragma: no cover - the store just wrote it
                raise RuntimeError(f"transaction {outcome.tx_id} vanished from {tmp}")
            edits = [
                {
                    "file": edit.file,
                    "start": edit.start,
                    "end": edit.end,
                    "replacement": edit.new,
                }
                for edit in loaded.edits
            ]

    def _entry(item: dict) -> dict:
        # Indexed, not `.get(..., "")`: every planned repair came from a
        # finding, so a miss is a bug in the collection and must not be
        # laundered into an empty violation line that the oracle would then
        # compare against the frozen engine's real one.
        return {**item, "violation": repairs.violations[item["fix_id"]]}

    return {
        "schema": SCHEMA,
        "fixes": [_entry(item) for item in outcome.fixes],
        "skipped_conflicts": [_entry(item) for item in outcome.skipped_conflicts],
        "declined": list(outcome.declined),
        "edits": edits,
    }


def main(argv: list[str] | None = None) -> int:
    """Parse ``--target``/``--rules``, print one JSON payload, exit 0."""
    parser = argparse.ArgumentParser(
        description="Plan the DSL-ported rules' repairs over an indexed target and print JSON."
    )
    parser.add_argument("--target", required=True, help="project root holding a .pypeeker/ index")
    parser.add_argument("--rules", default="", help="comma-separated rule ids to evaluate")
    args = parser.parse_args(argv)
    rules = tuple(name for name in args.rules.split(",") if name)
    print(json.dumps(run(Path(args.target), rules)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
