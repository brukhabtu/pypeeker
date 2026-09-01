"""pypeeker app: application services between the CLI and the domain packages.

Each module here composes two domain packages that may not import each
other directly (``check`` and ``refactor``) into one workflow the CLI can
call as a single function — the composition is what makes ``check`` /
``refactor`` a layering boundary rather than an implementation detail: this
package is the one place allowed to import both.
"""

from pypeeker.app.batch_intents import build_batch_intents
from pypeeker.app.batch_run import dropped_intent_report, run_intent_batch
from pypeeker.app.boundary_config import (
    BoundaryConfigError,
    dotted_boundary_units,
    validate_boundary_config,
)
from pypeeker.app.check_fixes import (
    CheckFixApplyError,
    CheckFixSimulationError,
    apply_check_fixes,
)
from pypeeker.app.check_run import (
    BaselineDelta,
    BaselineUpdate,
    CheckRun,
    check_baseline_delta,
    run_check,
    update_check_baseline,
)
from pypeeker.app.intent_fixes import (
    DuplicateIntentIdError,
    IntentFixOutcome,
    plan_intent_fixes,
)
from pypeeker.app.privatize import run_privatize
from pypeeker.app.scratch import scratch_transactions
from pypeeker.app.submit import SubmitError, submit_intent, submit_intents

__all__ = [
    "BaselineDelta",
    "BaselineUpdate",
    "BoundaryConfigError",
    "CheckFixApplyError",
    "CheckFixSimulationError",
    "CheckRun",
    "DuplicateIntentIdError",
    "IntentFixOutcome",
    "SubmitError",
    "apply_check_fixes",
    "build_batch_intents",
    "check_baseline_delta",
    "dropped_intent_report",
    "plan_intent_fixes",
    "dotted_boundary_units",
    "run_check",
    "run_intent_batch",
    "run_privatize",
    "scratch_transactions",
    "submit_intent",
    "submit_intents",
    "update_check_baseline",
    "validate_boundary_config",
]
