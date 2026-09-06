"""The new engine's configuration reader: ``[tool.pypeeker]`` as a ported rule sees it.

Built on :func:`pypeeker.project.load_pypeeker_section`, the single owner of
``[tool.pypeeker]`` access — the same function ``check.config.load_config``
builds its typed config on. This module is the *new engine's* view of that
table: which files are in scope, which rules and plugins the project declares,
and what each rule's option table contains. It stays a module of its own,
rather than living in the harness that first needed it, because
:mod:`pypeeker.dsl.differential` and :mod:`pypeeker.dsl.differential_fix` both
read configuration and neither is the *owner* of how the new engine reads it.

The option coercion the frozen ``check.rules._as_str_list`` performs lives
beside it. Every family that reads an option table needs it, and the two
families that used to carry their own copy (:mod:`pypeeker.dsl.sweeps`,
:mod:`pypeeker.dsl.visibility`) import each other in one direction already, so
this leaf is the one place both can reach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypeeker.project import DEFAULT_SRC_ROOTS, load_pypeeker_section

DEFAULT_SRC: tuple[str, ...] = DEFAULT_SRC_ROOTS
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


def read_config(
    target: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], dict[str, dict]]:
    """Read ``target``'s ``[tool.pypeeker]`` into (src roots, rules, plugins, rule options).

    The ``visibility`` injection at the end is not decoration — the project-wide
    ``[tool.pypeeker.visibility]`` table is copied into *every enabled rule's*
    options under that reserved key, so a rule that reads its own
    ``visibility`` option sees a different value on a project that declares the
    section. The injected value is the **raw** table, not a parsed
    :class:`~pypeeker.project.VisibilityConfig`: rules coerce it themselves.

    Returns defaults (``("src",)``, no rules, no plugins, no options) when the
    file or the section is missing. The default applies when the ``src`` key is
    *absent*, not when it is falsy: ``section.get("src", DEFAULT_SRC)`` leaves
    an explicit ``src = []`` empty, and the engine then applies no prefix filter
    at all, so every indexed file is checked. Coercing ``[]`` to ``("src",)``
    here — which is what :func:`pypeeker.project.load_src_roots` does — would
    filter the corpus to ``src/`` and under-report on exactly those projects.
    """
    section = load_pypeeker_section(target)
    if not section:
        return DEFAULT_SRC, (), (), {}
    src = tuple(section.get("src", DEFAULT_SRC))
    rules = tuple(section.get("rules", ()))
    plugins = tuple(section.get("plugins", ()))
    options: dict[str, dict] = {
        key: dict(value)
        for key, value in section.items()
        if key not in RESERVED_KEYS and isinstance(value, dict)
    }
    visibility = section.get("visibility")
    if isinstance(visibility, dict) and visibility:
        for rule_name in rules:
            options.setdefault(rule_name, {}).setdefault("visibility", dict(visibility))
    return src, rules, plugins, options
