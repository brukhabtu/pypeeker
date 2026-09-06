"""pypeeker app: application services between the CLI and the domain packages.

Each module here composes two domain packages that may not import each
other directly — a rule engine and ``refactor`` — into one workflow the CLI
can call as a single function. The composition is what makes that pair a
layering boundary rather than an implementation detail: this package is the
one place allowed to import both.

For the duration of the DSL flip there are *two* rule engines, and this layer
composes either with ``refactor``: ``check_run``/``check_fixes``/``privatize``
over the frozen ``check`` package (still what the CLI calls), and
``check_run2``/``fix_run``/``privatize_run`` over ``dsl``. Phase B deletes the
first set, drops ``check`` from this package's allow-list, and renames the
second set into the vacated names.
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
from pypeeker.app.check_run2 import (
    CheckConfigError,
    DslBaselineDelta,
    DslBaselineUpdate,
    DslCheckRun,
    dsl_baseline_delta,
    run_dsl_check,
    update_dsl_baseline,
)
from pypeeker.app.fix_run import DslFixOutcome, plan_dsl_fixes
from pypeeker.app.intent_fixes import (
    DuplicateIntentIdError,
    IntentFixOutcome,
    plan_intent_fixes,
)
from pypeeker.app.privatize import run_privatize
from pypeeker.app.privatize_run import PrivatizeReport, run_dsl_privatize
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
    "DslBaselineDelta",
    "DslBaselineUpdate",
    "DslCheckRun",
    "DslFixOutcome",
    "DuplicateIntentIdError",
    "IntentFixOutcome",
    "PrivatizeReport",
    "SubmitError",
    "apply_check_fixes",
    "build_batch_intents",
    "check_baseline_delta",
    "dropped_intent_report",
    "dsl_baseline_delta",
    "plan_dsl_fixes",
    "plan_intent_fixes",
    "dotted_boundary_units",
    "run_check",
    "run_dsl_check",
    "run_dsl_privatize",
    "run_intent_batch",
    "run_privatize",
    "scratch_transactions",
    "submit_intent",
    "submit_intents",
    "update_check_baseline",
    "update_dsl_baseline",
    "validate_boundary_config",
]
