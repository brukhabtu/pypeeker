"""Application service: the ``check`` command's run, baseline update and delta.

Everything ``check`` does between parsing its flags and printing — load the
project's ``[tool.pypeeker]`` config, refuse an import-boundaries table that
would enforce nothing, build the engine, re-seed the born-private symbol
namespace on ``--update-baseline``, run the rules — lives here so the CLI
keeps only options, output and exit codes. The two baseline flows are
separate functions returning plain data for the same reason: the CLI prints
them, it does not compute them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypeeker.app.boundary_config import validate_boundary_config
from pypeeker.check import (
    CheckEngine,
    Violation,
    baseline_path,
    clear_symbol_baseline,
    delta,
    load_baseline,
    load_config,
    write_baseline,
)
from pypeeker.check.builtin.born_private import BORN_PRIVATE
from pypeeker.storage import IndexStore

__all__ = [
    "BaselineDelta",
    "BaselineUpdate",
    "CheckRun",
    "check_baseline_delta",
    "run_check",
    "update_check_baseline",
]


@dataclass(frozen=True)
class CheckRun:
    """One run of the configured rules: the engine that ran and what it found.

    ``engine`` is handed on to ``check --fix``, which re-runs it against
    simulated state; ``violations`` is the FULL set, never filtered by
    confidence — display filtering is the CLI's, baselines always see all.
    """

    engine: CheckEngine
    violations: list[Violation]


@dataclass(frozen=True)
class BaselineUpdate:
    """What ``--update-baseline`` recorded: how many, and where."""

    recorded: int
    path: Path


@dataclass(frozen=True)
class BaselineDelta:
    """``--baseline``'s comparison: the recorded total, and what moved."""

    baselined: int
    new: list[Violation]
    fixed: list


def run_check(
    store: IndexStore, root: Path, *, reseed_symbol_baseline: bool = False
) -> CheckRun:
    """Load the project's check config, build the engine and run every rule.

    Raises :class:`~pypeeker.app.boundary_config.BoundaryConfigError` when the
    import-boundaries table names a nested unit: such a table would run clean
    while enforcing nothing, so the run is refused rather than reported as a
    pass the project cannot trust.

    ``reseed_symbol_baseline`` is ``--update-baseline``'s half of the
    born-private contract: when that rule is enabled, the accepted-public
    symbol namespace is cleared first so the rule's own run self-seeds it
    (``write_symbol_baseline``) from the current public surface.
    """
    config = load_config(root)
    validate_boundary_config(config.rule_options.get("import-boundaries", {}))
    engine = CheckEngine(store, config)
    if reseed_symbol_baseline and BORN_PRIVATE in config.rules:
        clear_symbol_baseline(baseline_path(root))
    return CheckRun(engine=engine, violations=engine.run())


def update_check_baseline(root: Path, violations: list[Violation]) -> BaselineUpdate:
    """Record ``violations`` as the project's new baseline.

    Takes the full set, never a confidence-filtered one: a baseline must not
    churn with ``--strict``.
    """
    path = baseline_path(root)
    counts = write_baseline(path, violations)
    return BaselineUpdate(recorded=sum(counts.values()), path=path)


def check_baseline_delta(root: Path, violations: list[Violation]) -> BaselineDelta:
    """Compare ``violations`` against the stored baseline.

    The delta is over the full set — identities must match what was
    recorded — and a missing baseline file counts as empty, so every
    violation is new.
    """
    baseline = load_baseline(baseline_path(root))
    new, fixed = delta(violations, baseline)
    return BaselineDelta(baselined=sum(baseline.values()), new=new, fixed=fixed)
