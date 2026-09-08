"""The new engine's configuration reader: ``[tool.pypeeker]`` as a ported rule sees it.

Built on :func:`pypeeker.project.load_pypeeker_section`, the single owner of
``[tool.pypeeker]`` access — the same function ``check.config.load_config``
builds its typed config on. This module is the *new engine's* view of that
table: which files are in scope, which rules and plugins the project declares,
and what each rule's option table contains. It stays a module of its own
because every runnable surface reads configuration —
:mod:`pypeeker.dsl.engine`, :mod:`pypeeker.dsl.repairs`,
:func:`pypeeker.app.run_check` and ``pypeeker.app.batch_intents`` — and
none of them is the *owner* of how the engine reads it.

Option *coercion* is not here — it is :mod:`pypeeker.project`'s, the one
module every consumer can legally reach (TASK-163). What lives here is the
reserved-key vocabulary of ``[tool.pypeeker]`` and the injection contract:
which keys are not rule-option subsections, and under what key the
project-wide visibility table reaches a rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypeeker.project import (
    DEFAULT_SRC_ROOTS,
    ConfigOptionError,
    coerce_str_list,
    coerce_visibility_table,
    load_pypeeker_section,
)

DEFAULT_SRC: tuple[str, ...] = DEFAULT_SRC_ROOTS
"""The source roots assumed when ``[tool.pypeeker]`` declares no ``src`` key."""

RESERVED_KEYS: tuple[str, ...] = ("src", "rules", "plugins", "visibility")
"""``[tool.pypeeker]`` keys that are not rule-option subsections."""

PROJECT_VISIBILITY_KEY: str = "project-visibility"
"""The rule-option key :func:`read_config` injects the project-wide table under.

Reserved: :func:`read_config` refuses a rule table that declares it, so it can
never be shadowed by user config. The name was chosen so it cannot collide with
a rule's *own* ``visibility`` option — that collision is what silently emptied
``require-docstrings``' visibility set on every project declaring
``[tool.pypeeker.visibility]``, turning three findings into zero (TASK-163).

It lives in ``dsl``, not ``project``, because ``pypeeker.app`` writes it too
and ``app`` may not import ``project``. ``import-boundaries`` resolves
re-export chains, so re-exporting a ``project`` symbol through the ``dsl``
barrel would still charge ``app`` with importing ``project``. Defining it here
is also the honest home: the reserved key is the *DSL injection contract*,
while ``project`` owns the TOML table's own vocabulary.
"""


def read_config(
    target: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], dict[str, dict]]:
    """Read ``target``'s ``[tool.pypeeker]`` into (src roots, rules, plugins, rule options).

    The injection at the end is not decoration — the project-wide
    ``[tool.pypeeker.visibility]`` table is copied into *every enabled rule's*
    options under :data:`PROJECT_VISIBILITY_KEY`, so the visibility family can
    read project-wide policy without a second config read. It goes under that
    reserved key rather than ``visibility`` because the latter is also the name
    of ``require-docstrings``' own enum option, and the collision emptied that
    option's set in silence (TASK-163). A rule table declaring the reserved key
    itself refuses. The injected value is the **raw** table, not a parsed
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
    src = coerce_str_list("src", section.get("src", DEFAULT_SRC), allow_scalar=False)
    rules = coerce_str_list("rules", section.get("rules", ()), allow_scalar=False)
    plugins = coerce_str_list("plugins", section.get("plugins", ()), allow_scalar=False)
    options: dict[str, dict] = {
        key: dict(value)
        for key, value in section.items()
        if key not in RESERVED_KEYS and isinstance(value, dict)
    }
    for rule_name, table in options.items():
        if PROJECT_VISIBILITY_KEY in table:
            raise ConfigOptionError(
                f"{rule_name}.{PROJECT_VISIBILITY_KEY}",
                table[PROJECT_VISIBILITY_KEY],
                "nothing — this key is reserved for the injected project-wide "
                "[tool.pypeeker.visibility] table",
            )
    visibility = coerce_visibility_table(section.get("visibility"))
    if visibility:
        for rule_name in rules:
            options.setdefault(rule_name, {})[PROJECT_VISIBILITY_KEY] = dict(visibility)
    return src, rules, plugins, options


def read_visibility_table(target: Path) -> dict[str, Any]:
    """Read ``target``'s project-wide ``[tool.pypeeker.visibility]`` table, raw.

    :func:`read_config` injects this table into every *enabled* rule's options,
    which is the right shape for running the configured rule set. It is the
    wrong shape for a service that runs a rule the project has not enabled —
    ``privatize`` nominates through three demotion rules whether or not
    ``[tool.pypeeker].rules`` lists them — so the table is also reachable on
    its own, from the module that already owns reading it. One owner of
    ``[tool.pypeeker]`` access, two views of the same key.

    The value is the **raw** mapping, deliberately not a parsed
    :class:`~pypeeker.project.VisibilityConfig`:
    :func:`pypeeker.dsl.visibility._visibility_table` accepts only the raw
    table, so handing it a parsed config — which is what the frozen
    ``app/privatize.py`` injected — refuses on every project that declares the
    section, this repo included.

    Returns ``{}`` when the file, the section or the key is absent or empty,
    and refuses through :func:`~pypeeker.project.coerce_visibility_table` when
    the key holds something that is not a well-shaped table.
    """
    section = load_pypeeker_section(target)
    visibility = coerce_visibility_table(section.get("visibility") if section else None)
    return dict(visibility)
