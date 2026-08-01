"""Refactoring operations: extract, inline, rename, and visibility management."""

from pypeeker.refactor.applier import ApplyError, RollbackError, TransactionApplier
from pypeeker.refactor.batch import (
    BatchAborted,
    BatchPolicy,
    BatchResult,
    FlattenError,
    OverlayApplyError,
    ScheduleCycleError,
    ScheduleError,
    StalePreimageError,
    apply_to_overlay,
    flatten_batch,
    flatten_store,
    run_batch,
    schedule,
)
from pypeeker.refactor.delete import DeleteSymbolError, DeleteSymbolPlanner
from pypeeker.refactor.docstring_ops import (
    DocstringParamRenameError,
    DocstringParamRenamePlanner,
)
from pypeeker.refactor.extract import (
    ExtractMethodError,
    ExtractMethodPlanner,
    ExtractVariableError,
    ExtractVariablePlanner,
)
from pypeeker.refactor.imports_ops import (
    RemoveImportError,
    RemoveImportPlanner,
    RewriteStarImportError,
    RewriteStarImportPlanner,
)
from pypeeker.refactor.inline import InlineVariableError, InlineVariablePlanner
from pypeeker.refactor.literals import TuplifyError, TuplifyPlanner
from pypeeker.refactor.move import MoveSymbolError, MoveSymbolPlanner
from pypeeker.refactor.planner import RenamePlanError, RenamePlanner
from pypeeker.refactor.privatize import CandidateEntry, PrivatizeOutcome, plan_privatize
from pypeeker.refactor.registry import Materialized, get_materializer
from pypeeker.refactor.text_ops import ReplaceTextError, ReplaceTextPlanner
from pypeeker.refactor.visibility_ops import VisibilityOpError, VisibilityPlanner

__all__ = [
    "ApplyError",
    "BatchAborted",
    "BatchPolicy",
    "BatchResult",
    "CandidateEntry",
    "DeleteSymbolError",
    "DeleteSymbolPlanner",
    "DocstringParamRenameError",
    "DocstringParamRenamePlanner",
    "ExtractMethodError",
    "ExtractMethodPlanner",
    "ExtractVariableError",
    "ExtractVariablePlanner",
    "FlattenError",
    "InlineVariableError",
    "InlineVariablePlanner",
    "Materialized",
    "MoveSymbolError",
    "MoveSymbolPlanner",
    "OverlayApplyError",
    "PrivatizeOutcome",
    "RemoveImportError",
    "RemoveImportPlanner",
    "RenamePlanError",
    "RenamePlanner",
    "ReplaceTextError",
    "ReplaceTextPlanner",
    "RewriteStarImportError",
    "RewriteStarImportPlanner",
    "RollbackError",
    "ScheduleCycleError",
    "ScheduleError",
    "StalePreimageError",
    "TransactionApplier",
    "TuplifyError",
    "TuplifyPlanner",
    "VisibilityOpError",
    "VisibilityPlanner",
    "apply_to_overlay",
    "flatten_batch",
    "flatten_store",
    "get_materializer",
    "plan_privatize",
    "run_batch",
    "schedule",
]
