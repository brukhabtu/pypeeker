"""Refactoring operations: extract, inline, rename, and visibility management."""

from pypeeker.refactor.applier import ApplyError, RollbackError, TransactionApplier
from pypeeker.refactor.batch import (
    BatchAborted,
    BatchPolicy,
    FlattenError,
    ScheduleError,
    flatten_batch,
    run_batch,
)
from pypeeker.refactor.extract import (
    ExtractMethodError,
    ExtractMethodPlanner,
    ExtractVariableError,
    ExtractVariablePlanner,
)
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.refactor.intents import (
    ExtractMethodIntent,
    ExtractVariableIntent,
    FixIntent,
    InlineVariableIntent,
    Intent,
    RenameIntent,
)
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.refactor.privatize import CandidateEntry, PrivatizeOutcome, plan_privatize
from pypeeker.refactor.visibility_ops import VisibilityOpError, VisibilityPlanner

__all__ = [
    "ApplyError",
    "BatchAborted",
    "BatchPolicy",
    "CandidateEntry",
    "ExtractMethodError",
    "ExtractMethodIntent",
    "ExtractMethodPlanner",
    "ExtractVariableError",
    "ExtractVariableIntent",
    "ExtractVariablePlanner",
    "FixIntent",
    "FlattenError",
    "InlineVariableError",
    "InlineVariableIntent",
    "InlineVariablePlanner",
    "Intent",
    "PrivatizeOutcome",
    "RenameIntent",
    "RenamePlanError",
    "RenamePlanner",
    "RollbackError",
    "ScheduleError",
    "TransactionApplier",
    "VisibilityOpError",
    "VisibilityPlanner",
    "flatten_batch",
    "plan_privatize",
    "run_batch",
]
