"""Typed ``check`` configuration built on :mod:`pypeeker.project`.

``project.load_pypeeker_section`` is the single owner of ``[tool.pypeeker]``
parsing; this module only shapes that raw dict into a :class:`CheckConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pypeeker.project import (
    DEFAULT_SRC_ROOTS,
    VisibilityConfig,
    load_pypeeker_section,
    parse_visibility_config,
)

DEFAULT_SRC: tuple[str, ...] = DEFAULT_SRC_ROOTS

_RESERVED_KEYS: tuple[str, ...] = ("src", "rules", "plugins", "visibility")
"""Top-level ``[tool.pypeeker]`` keys that are not rule-option subsections."""


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
    visibility: VisibilityConfig = VisibilityConfig()
    """Parsed ``[tool.pypeeker.visibility]`` section (defaults when absent)."""


def load_config(project_root: Path) -> CheckConfig:
    """Load ``[tool.pypeeker]`` from ``project_root/pyproject.toml``.

    Returns defaults if the file is missing or has no ``[tool.pypeeker]``
    section. Subsections (``[tool.pypeeker.<rule>]``) become entries in
    ``rule_options`` keyed by rule name.

    ``[tool.pypeeker.visibility]`` is project-wide, not a rule: it is parsed
    into :attr:`CheckConfig.visibility` and — because the engine hands each
    rule only ``rule_options[name]`` — its raw table is also injected into
    every enabled rule's options under the reserved key ``"visibility"``.
    Rules read it back via :func:`pypeeker.project.coerce_visibility`. The
    injection only happens when the section is present, so projects without
    it see byte-identical rule options.
    """
    section = load_pypeeker_section(project_root)
    if not section:
        return CheckConfig()

    src_raw = section.get("src", list(DEFAULT_SRC))
    rules_raw = section.get("rules", [])
    plugins_raw = section.get("plugins", [])
    visibility_raw = section.get("visibility")
    if not isinstance(visibility_raw, dict):
        visibility_raw = None
    rule_options: dict[str, dict] = {
        key: value
        for key, value in section.items()
        if key not in _RESERVED_KEYS and isinstance(value, dict)
    }
    if visibility_raw:
        for rule_name in rules_raw:
            options = rule_options.setdefault(rule_name, {})
            options.setdefault("visibility", dict(visibility_raw))

    return CheckConfig(
        src=tuple(src_raw),
        rules=tuple(rules_raw),
        rule_options=rule_options,
        plugins=tuple(plugins_raw),
        visibility=parse_visibility_config(visibility_raw),
    )
