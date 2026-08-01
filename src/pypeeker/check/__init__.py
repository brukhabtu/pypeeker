"""pypeeker check: semantic linter driven by ``[tool.pypeeker]`` in pyproject.toml."""

from pypeeker.check.baseline import (
    baseline_path,
    clear_symbol_baseline,
    delta,
    load_baseline,
    write_baseline,
)
from pypeeker.check.config import CheckConfig, load_config
from pypeeker.check.context import CheckContext
from pypeeker.check.engine import CheckConfigError, CheckEngine
from pypeeker.check.models import Violation, with_remedy
from pypeeker.check.rules import ProjectRule, Rule, register_rule
from pypeeker.check.simulation import SIMULATION_UNSAFE_RULES

__all__ = [
    "CheckConfig",
    "CheckConfigError",
    "CheckContext",
    "CheckEngine",
    "SIMULATION_UNSAFE_RULES",
    "ProjectRule",
    "Rule",
    "Violation",
    "baseline_path",
    "clear_symbol_baseline",
    "delta",
    "load_baseline",
    "load_config",
    "register_rule",
    "with_remedy",
    "write_baseline",
]
