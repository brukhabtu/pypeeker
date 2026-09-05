"""The new engine's configuration reader: ``[tool.pypeeker]`` as a ported rule sees it.

``dsl`` may not import ``check``, and ``project`` is not in its layering
allow-list, so this module re-implements the slice of
``pypeeker.check.config.load_config`` the ported rules actually observe: which
files are in scope, and what each rule's option table contains. That
duplication is sanctioned by ``dsl-rewrite.md`` — the differential runner must
never execute old-engine code on the new side, or the oracle would be grading a
thing against itself — and it lives here, in its own module, rather than in
the harness that first needed it: :mod:`pypeeker.dsl.differential` and
:mod:`pypeeker.dsl.differential_fix` both read configuration, and neither is
the *owner* of how the new engine reads it.

The option coercion the frozen ``check.rules._as_str_list`` performs lives
beside it for the same reason. Every family that reads an option table needs
it, and the two families that used to carry their own copy
(:mod:`pypeeker.dsl.sweeps`, :mod:`pypeeker.dsl.visibility`) import each
other in one direction already, so this leaf is the one place both can reach.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DEFAULT_SRC: tuple[str, ...] = ("src",)
"""The source roots assumed when ``[tool.pypeeker]`` declares no ``src`` key."""

RESERVED_KEYS: tuple[str, ...] = ("src", "rules", "plugins", "visibility")
"""``[tool.pypeeker]`` keys that are not rule-option subsections."""


def as_str_list(raw: Any) -> list[str]:
    """Coerce an option value to a list of strings (``''`` / ``None`` / ``[]`` -> ``[]``).

    A faithful copy of ``check.rules._as_str_list``, silent drops included.
    Copied rather than imported: ``dsl`` may not import ``check`` at all, and
    ``check`` is frozen.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(value) for value in raw]


def read_config(target: Path) -> tuple[tuple[str, ...], dict[str, dict]]:
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
        return DEFAULT_SRC, {}
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    section = (data.get("tool") or {}).get("pypeeker") or {}
    src = tuple(section.get("src", DEFAULT_SRC))
    options: dict[str, dict] = {
        key: dict(value)
        for key, value in section.items()
        if key not in RESERVED_KEYS and isinstance(value, dict)
    }
    visibility = section.get("visibility")
    if isinstance(visibility, dict) and visibility:
        for rule_name in section.get("rules") or ():
            options.setdefault(rule_name, {}).setdefault("visibility", dict(visibility))
    return src, options
