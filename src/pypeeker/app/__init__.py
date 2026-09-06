"""pypeeker app: application services between the CLI and the domain packages.

Each module here composes two domain packages that may not import each
other directly — the ``dsl`` rule engine and ``refactor`` — into one workflow
the CLI can call as a single function. The composition is what makes that pair
a layering boundary rather than an implementation detail: this package is the
one place allowed to import both.

**Naming scheme.** A planning entry point is ``plan_<what feeds it>_fixes`` and
its result is ``<What>FixOutcome``: :func:`~pypeeker.app.fix_run.plan_check_fixes`
returns a :class:`~pypeeker.app.fix_run.FixOutcome` for the repairs a ``check``
run earned, and :func:`~pypeeker.app.intent_fixes.plan_intent_fixes` returns an
:class:`~pypeeker.app.intent_fixes.IntentFixOutcome` for a caller-supplied list
of intents. ``plan_check_fixes`` is deliberately not the frozen engine's
``apply_check_fixes``: ``plan_only=True`` makes it plan-only, so the old verb
contradicted its own parameter.
"""

from pypeeker.app.batch_intents import build_batch_intents
from pypeeker.app.batch_run import dropped_intent_report, run_intent_batch
from pypeeker.app.boundary_config import (
    BoundaryConfigError,
    dotted_boundary_units,
    validate_boundary_config,
)
from pypeeker.app.check_run import (
    BaselineDelta,
    BaselineUpdate,
    CheckConfigError,
    CheckRun,
    check_baseline_delta,
    run_check,
    update_check_baseline,
)
from pypeeker.app.fix_run import (
    CheckFixApplyError,
    CheckFixSimulationError,
    FixOutcome,
    plan_check_fixes,
)
from pypeeker.app.intent_fixes import (
    DuplicateIntentIdError,
    IntentFixOutcome,
    plan_intent_fixes,
)
from pypeeker.app.privatize import PrivatizeReport, run_privatize
from pypeeker.app.scratch import scratch_transactions
from pypeeker.app.submit import SubmitError, submit_intent, submit_intents

__all__ = [
    "BaselineDelta",
    "BaselineUpdate",
    "BoundaryConfigError",
    "CheckConfigError",
    "CheckFixApplyError",
    "CheckFixSimulationError",
    "CheckRun",
    "DuplicateIntentIdError",
    "FixOutcome",
    "IntentFixOutcome",
    "PrivatizeReport",
    "SubmitError",
    "build_batch_intents",
    "check_baseline_delta",
    "dotted_boundary_units",
    "dropped_intent_report",
    "plan_check_fixes",
    "plan_intent_fixes",
    "run_check",
    "run_intent_batch",
    "run_privatize",
    "scratch_transactions",
    "submit_intent",
    "submit_intents",
    "update_check_baseline",
    "validate_boundary_config",
]
