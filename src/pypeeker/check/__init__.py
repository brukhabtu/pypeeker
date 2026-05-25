"""pypeeker check: semantic linter driven by ``[tool.pypeeker]`` in pyproject.toml."""

from pypeeker.check.config import CheckConfig, load_config
from pypeeker.check.engine import CheckConfigError, CheckEngine
from pypeeker.check.models import Violation
from pypeeker.check.rules import Rule, register_rule

__all__ = [
    "CheckConfig",
    "CheckConfigError",
    "CheckEngine",
    "Rule",
    "Violation",
    "load_config",
    "register_rule",
]
