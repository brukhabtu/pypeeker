"""The new engine's runnable surface, for the differential oracle to grade.

``scripts/differential-check.py`` materializes a target, runs the frozen old
engine over it, then runs this module over the *same* directory and compares
findings per rule. The contract is one JSON object on stdout::

    {"schema": 1, "findings": [{"rule": "...", "path": "src/x.py",
                                "line": 12, "message": "...",
                                "confidence": "declared"}]}

Exactly those five keys per finding — the harness rejects extras by name — and
``path`` must stay relative to the target root, which it is, because the binder
records indexed paths that way.

Two deliberate shapes here:

* **The ``__main__`` guard is not in this file.** It lives in
  ``scripts/dsl-engine.py``. A guard under ``src/`` fails the zero-baseline
  self-lint twice over: ``no-unresolved-refs`` on ``'__name__'`` and
  ``import-time-side-effects`` on the guarded call, both at DECLARED tier.
  Reading ``__doc__`` (for an argparse description, say) fails the first of
  those too, which is why the parser below carries a literal string.
* **Configuration is read here, not imported.** ``dsl`` may not import
  ``check``, and ``project`` is not in its layering allow-list, so
  :func:`_read_config` re-implements the slice of
  ``pypeeker.check.config.load_config`` the ported rules actually observe.
  That duplication is sanctioned: the differential runner must never execute
  old-engine code on the new side, or the oracle would be grading a thing
  against itself.
* **An unreadable target is refused, not reported as zero findings.** See
  :exc:`_NoIndexError`. The old side is already protected — the harness runs
  ``pypeeker index`` over the target and fails unless it exits 0 — so without
  the same guard here a misdirected ``--target`` would silently zero only the
  new side, and every rule whose old-side count is also 0 would grade PASS
  against an engine that read nothing.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.library import install_expressions
from pypeeker.dsl.rules import dsl_rule
from pypeeker.storage import IndexStore

SCHEMA = 1
"""Version of the JSON payload this module prints; the harness requires 1."""

_DEFAULT_SRC: tuple[str, ...] = ("src",)

_RESERVED_KEYS: tuple[str, ...] = ("src", "rules", "plugins", "visibility")
"""``[tool.pypeeker]`` keys that are not rule-option subsections."""


class _NoIndexError(RuntimeError):
    """Raised when ``--target`` names something this engine cannot read at all.

    "I read the corpus and found nothing" and "I read nothing" are the same
    JSON payload — an empty ``findings`` list — and the harness grades them the
    same way: PASS against an old side that also found nothing. Only the second
    of those is a bug, so it is made loud here instead. A target that is not a
    directory, or that holds no index, raises; a target that *is* indexed but
    whose configured source roots select no file does not, because that is a
    real (if empty) corpus and the old engine reports zero findings over it too.
    """


def _read_config(target: Path) -> tuple[tuple[str, ...], dict[str, dict]]:
    """Read ``target/pyproject.toml``'s ``[tool.pypeeker]`` into (src roots, rule options).

    Mirrors ``pypeeker.check.config.load_config`` on the two things a ported
    rule can observe: which files are in scope, and what each rule's option
    table contains. The ``visibility`` injection at the end is not
    decoration — the old engine copies the whole project-wide
    ``[tool.pypeeker.visibility]`` table into *every enabled rule's* options
    under that reserved key, and a rule that reads its own ``visibility``
    option therefore sees a different value on a project that declares the
    section. Omitting the injection here would make the two engines disagree
    on exactly those projects.

    Returns defaults (``("src",)``, no options) when the file or the section is
    missing, matching the old loader. The default applies when the ``src`` key
    is *absent*, not when it is falsy: the old loader's
    ``section.get("src", list(DEFAULT_SRC))`` leaves an explicit ``src = []``
    empty, and its engine then applies no prefix filter at all, so every
    indexed file is checked. Coercing ``[]`` to ``("src",)`` here would filter
    the corpus to ``src/`` and under-report on exactly those projects — a
    divergence the oracle cannot catch, since a materialized target has every
    file under ``src/`` anyway.
    """
    pyproject = target / "pyproject.toml"
    if not pyproject.is_file():
        return _DEFAULT_SRC, {}
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    section = (data.get("tool") or {}).get("pypeeker") or {}
    src = tuple(section.get("src", _DEFAULT_SRC))
    options: dict[str, dict] = {
        key: dict(value)
        for key, value in section.items()
        if key not in _RESERVED_KEYS and isinstance(value, dict)
    }
    visibility = section.get("visibility")
    if isinstance(visibility, dict) and visibility:
        for rule_name in section.get("rules") or ():
            options.setdefault(rule_name, {}).setdefault("visibility", dict(visibility))
    return src, options


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


def _run(target: Path, rules: tuple[str, ...]) -> dict:
    """Evaluate ``rules`` over the index already sitting in ``target``.

    The harness hands over the same directory the old engine just indexed and
    checked, so this reads ``.pypeeker/`` rather than re-binding — sanctioned
    by the manifest's own comment, and it halves the oracle's runtime.

    Returns:
        The payload to print: ``{"schema": 1, "findings": [...]}``, with the
        findings in rule order and, within a rule, in index order.

    Raises:
        _NoIndexError: if ``target`` is not a directory, or holds no index.
    """
    install_expressions()
    src_roots, options = _read_config(target)
    store = IndexStore(target)
    _require_index(target, store)
    corpus = Corpus(store, src_roots)
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
        ``0``. A failure here is an exception, not an exit code: the harness
        reports a non-zero exit with the last 40 lines of output, and a
        traceback is far more useful to whoever is porting a rule than a
        swallowed error would be.
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
