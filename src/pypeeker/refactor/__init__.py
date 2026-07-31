"""Refactoring operations: extract, inline, rename, and visibility management."""

from pypeeker.refactor.applier import ApplyError, RollbackError, TransactionApplier
from pypeeker.refactor.batch import (
    BatchAborted,
    BatchPolicy,
    BatchResult,
    FlattenError,
    ScheduleCycleError,
    ScheduleError,
    flatten_batch,
    run_batch,
    schedule,
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
from pypeeker.refactor.registry import Materialized, get_materializer
from pypeeker.refactor.visibility_ops import VisibilityOpError, VisibilityPlanner

__all__ = [
    "ApplyError",
    "BatchAborted",
    "BatchPolicy",
    "BatchResult",
    "CandidateEntry",
    "ExtractMethodError",
    "ExtractMethodPlanner",
    "ExtractVariableError",
    "ExtractVariablePlanner",
    "FlattenError",
    "InlineVariableError",
    "InlineVariablePlanner",
    "Materialized",
    "PrivatizeOutcome",
    "RenamePlanError",
    "RenamePlanner",
    "RollbackError",
    "ScheduleCycleError",
    "ScheduleError",
    "TransactionApplier",
    "VisibilityOpError",
    "VisibilityPlanner",
    "flatten_batch",
    "get_materializer",
    "plan_privatize",
    "run_batch",
    "schedule",
]
