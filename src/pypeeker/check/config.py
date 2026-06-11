"""Typed ``check`` configuration built on :mod:`pypeeker.project`.

``project.load_pypeeker_section`` is the single owner of ``[tool.pypeeker]``
parsing; this module only shapes that raw dict into a :class:`CheckConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pypeeker.project import DEFAULT_SRC_ROOTS, load_pypeeker_section

DEFAULT_SRC: tuple[str, ...] = DEFAULT_SRC_ROOTS


@dataclass(frozen=True)
class CheckConfig:
    """Resolved configuration for ``pypeeker check``.

    Empty rules list = no violations, by design — matches the ruff/mypy
    default-off behaviour rather than enabling everything on first run.
    """

    src: tuple[str, ...] = DEFAULT_SRC
    rules: tuple[str, ...] = ()
    rule_options: dict[str, dict] = field(default_factory=dict)
    plugins: tuple[str, ...] = ()
    """Importable module paths that register custom rules via ``register_rule``."""


def load_config(project_root: Path) -> CheckConfig:
    """Load ``[tool.pypeeker]`` from ``project_root/pyproject.toml``.

    Returns defaults if the file is missing or has no ``[tool.pypeeker]``
    section. Subsections (``[tool.pypeeker.<rule>]``) become entries in
    ``rule_options`` keyed by rule name.
    """
    section = load_pypeeker_section(project_root)
    if not section:
        return CheckConfig()

    src_raw = section.get("src", list(DEFAULT_SRC))
    rules_raw = section.get("rules", [])
    plugins_raw = section.get("plugins", [])
    rule_options: dict[str, dict] = {
        key: value
        for key, value in section.items()
        if key not in ("src", "rules", "plugins") and isinstance(value, dict)
    }

    return CheckConfig(
        src=tuple(src_raw),
        rules=tuple(rules_raw),
        rule_options=rule_options,
        plugins=tuple(plugins_raw),
    )
