"""Parse [tool.pypeeker] config from pyproject.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SRC: tuple[str, ...] = ("src",)


@dataclass(frozen=True)
class CheckConfig:
    src: tuple[str, ...] = DEFAULT_SRC
    rules: tuple[str, ...] = ()
    rule_options: dict[str, dict] = field(default_factory=dict)


def load_config(project_root: Path) -> CheckConfig:
    """Load [tool.pypeeker] from pyproject.toml at project_root.

    Returns defaults (no rules enabled) if pyproject.toml is missing or has
    no [tool.pypeeker] section.
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
    rule_options: dict[str, dict] = {}
    for key, value in section.items():
        if key in ("src", "rules"):
            continue
        if isinstance(value, dict):
            rule_options[key] = value

    return CheckConfig(
        src=tuple(src_raw),
        rules=tuple(rules_raw),
        rule_options=rule_options,
    )
