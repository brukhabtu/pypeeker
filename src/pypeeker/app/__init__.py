"""pypeeker app: application services between the CLI and the domain packages.

Each module here composes two domain packages that may not import each
other directly (``check`` and ``refactor``) into one workflow the CLI can
call as a single function — the composition is what makes ``check`` /
``refactor`` a layering boundary rather than an implementation detail: this
package is the one place allowed to import both.
"""

from pypeeker.app.batch_intents import build_batch_intents
from pypeeker.app.check_fixes import (
    CheckFixApplyError,
    CheckFixSimulationError,
    apply_check_fixes,
)
from pypeeker.app.intent_fixes import (
    DuplicateIntentIdError,
    IntentFixOutcome,
    plan_intent_fixes,
)
from pypeeker.app.privatize import run_privatize
from pypeeker.app.submit import SubmitError, submit_intent, submit_intents

__all__ = [
    "CheckFixApplyError",
    "CheckFixSimulationError",
    "DuplicateIntentIdError",
    "IntentFixOutcome",
    "SubmitError",
    "apply_check_fixes",
    "build_batch_intents",
    "plan_intent_fixes",
    "run_privatize",
    "submit_intent",
    "submit_intents",
]
