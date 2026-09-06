"""The engine's runnable surface over an on-disk target: run rules, print JSON.

Point this at a project root that has been indexed and it reads that project's
``[tool.pypeeker]`` config, opens its store, and runs the named rules over the
corpus, printing one JSON object on stdout::

    {"schema": 1, "findings": [{"rule": "...", "path": "src/x.py",
                                "line": 12, "message": "...",
                                "confidence": "declared"}]}

``path`` stays relative to the target root, because the binder records indexed
paths that way.

This is the *bare* surface — a target directory in, findings out. The surface
the CLI drives is :func:`pypeeker.app.run_dsl_check`, which additionally
resolves plugins, validates the boundary table, seeds the born-private ratchet
and orders findings for reporting.

Two deliberate shapes here:

* **There is no ``__main__`` guard in this file.** A guard under ``src/`` fails
  the zero-baseline self-lint twice over: ``no-unresolved-refs`` on
  ``'__name__'`` and ``import-time-side-effects`` on the guarded call, both at
  DECLARED tier. Reading ``__doc__`` (for an argparse description, say) fails
  the first of those too, which is why the parser below carries a literal
  string. :func:`main` is the entry point a launcher would call.
* **An unreadable target is refused, not reported as zero findings.** See
  :exc:`_NoIndexError`. :func:`open_corpus` is the one prologue both this
  module and :mod:`pypeeker.dsl.repairs` run — read the config, open the store,
  refuse an unindexed target, build the corpus — so the refusal and the reading
  of ``[tool.pypeeker]`` cannot drift between the read half and the write half.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypeeker.dsl.config import read_config
from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.library import install_expressions
from pypeeker.dsl.rules import dsl_rule
from pypeeker.storage import IndexStore

SCHEMA = 1
"""Version of the JSON payload this module prints."""

class _NoIndexError(RuntimeError):
    """Raised when ``--target`` names something this engine cannot read at all.

    "I read the corpus and found nothing" and "I read nothing" are the same
    JSON payload — an empty ``findings`` list — and a caller reading that
    payload has no way to tell them apart. Only the second is a bug, so it is
    made loud here instead of being answered with a clean bill of health. A
    target that is not a directory, or that holds no index, raises; a target
    that *is* indexed but whose configured source roots select no file does
    not, because that is a real (if empty) corpus.
    """


def _require_index(target: Path, store: IndexStore) -> None:
    """Refuse a target this engine would otherwise "check" without reading anything.

    Args:
        target: the project root passed as ``--target``.
        store: the store opened over it.

    Raises:
        _NoIndexError: if ``target`` is not a directory, or if the store lists
            no indexed files — the second covers a missing storage directory,
            since :meth:`IndexStore.list_indexed_files` returns an empty list
            rather than raising when its index root does not exist.
    """
    if not target.is_dir():
        raise _NoIndexError(f"--target is not a directory: {target}")
    if not store.list_indexed_files():
        raise _NoIndexError(
            f"--target {target} holds no index — nothing under it has been indexed, "
            "so an empty result would mean 'read nothing', not 'found nothing'; "
            "run 'pypeeker index' over that directory first"
        )


def open_corpus(target: Path) -> tuple[dict[str, dict], Corpus]:
    """Read ``target``'s config, open its index, and build the corpus over it.

    The prologue every runnable surface over an on-disk target shares — the
    findings side here and the repair side in :mod:`pypeeker.dsl.repairs` — in
    one place, so the refusal of an unindexed target and the reading of
    ``[tool.pypeeker]`` cannot drift between the read half and the write half.

    Args:
        target: the project root holding a ``.pypeeker/`` index.

    Returns:
        The per-rule option tables, keyed by rule id, and the
        :class:`~pypeeker.dsl.Corpus` over the configured source roots.

    Raises:
        _NoIndexError: if ``target`` is not a directory, or holds no index.
    """
    src_roots, _rules, _plugins, options = read_config(target)
    store = IndexStore(target)
    _require_index(target, store)
    return options, Corpus(store, src_roots)


def _run(target: Path, rules: tuple[str, ...]) -> dict:
    """Evaluate ``rules`` over the index already sitting in ``target``.

    This reads the index already under ``target/.pypeeker/`` rather than
    re-binding the tree, so the caller owns indexing (``pypeeker index``) and
    a repeated run over an unchanged tree costs only the rule evaluation.

    Returns:
        The payload to print: ``{"schema": 1, "findings": [...]}``, with the
        findings in rule order and, within a rule, in index order.

    Raises:
        _NoIndexError: if ``target`` is not a directory, or holds no index.
    """
    install_expressions()
    options, corpus = open_corpus(target)
    findings: list[dict] = []
    for name in rules:
        rule = dsl_rule(name)
        for finding in rule.findings(options.get(name, {}), corpus):
            findings.append({
                "rule": finding.rule,
                "path": finding.path,
                "line": finding.line,
                "message": finding.message,
                "confidence": finding.confidence.value,
            })
    return {"schema": SCHEMA, "findings": findings}


def main(argv: list[str] | None = None) -> int:
    """Parse ``--target``/``--rules``, print one JSON payload, exit 0.

    Args:
        argv: argument list, defaulting to ``sys.argv[1:]`` via argparse.

    Returns:
        ``0``. A failure here is an exception, not an exit code: a traceback
        naming the rule that broke is far more useful to whoever is debugging
        an expression than a swallowed error and a bare non-zero status.
    """
    parser = argparse.ArgumentParser(
        description="Evaluate DSL-ported rules over an indexed target and print JSON findings."
    )
    parser.add_argument("--target", required=True, help="project root holding a .pypeeker/ index")
    parser.add_argument("--rules", default="", help="comma-separated rule ids to evaluate")
    args = parser.parse_args(argv)
    rules = tuple(name for name in args.rules.split(",") if name)
    print(json.dumps(_run(Path(args.target), rules)))
    return 0
