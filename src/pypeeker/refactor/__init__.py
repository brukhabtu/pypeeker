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
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.refactor.privatize import CandidateEntry, PrivatizeOutcome, plan_privatize
from pypeeker.refactor.visibility_ops import VisibilityOpError, VisibilityPlanner

__all__ = [
    "ApplyError",
    "BatchAborted",
    "BatchPolicy",
    "CandidateEntry",
    "ExtractMethodError",
    "ExtractMethodPlanner",
    "ExtractVariableError",
    "ExtractVariablePlanner",
    "FlattenError",
    "InlineVariableError",
    "InlineVariablePlanner",
    "PrivatizeOutcome",
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
