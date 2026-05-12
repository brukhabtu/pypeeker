"""Parse ``[tool.pypeeker]`` from ``pyproject.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SRC: tuple[str, ...] = ("src",)


@dataclass(frozen=True)
class CheckConfig:
    """Resolved configuration for ``pypeeker check``.

    Empty rules list = no violations, by design — matches the ruff/mypy
    default-off behaviour rather than enabling everything on first run.
    """

    src: tuple[str, ...] = DEFAULT_SRC
    rules: tuple[str, ...] = ()
    rule_options: dict[str, dict] = field(default_factory=dict)


def load_config(project_root: Path) -> CheckConfig:
    """Load ``[tool.pypeeker]`` from ``project_root/pyproject.toml``.

    Returns defaults if the file is missing or has no ``[tool.pypeeker]``
    section. Subsections (``[tool.pypeeker.<rule>]``) become entries in
    ``rule_options`` keyed by rule name.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return CheckConfig()

    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)

    section = data.get("tool", {}).get("pypeeker")
    if not isinstance(section, dict):
        return CheckConfig()

    src_raw = section.get("src", list(DEFAULT_SRC))
    rules_raw = section.get("rules", [])
    rule_options: dict[str, dict] = {
        key: value
        for key, value in section.items()
        if key not in ("src", "rules") and isinstance(value, dict)
    }

    return CheckConfig(
        src=tuple(src_raw),
        rules=tuple(rules_raw),
        rule_options=rule_options,
    )
