"""First-class planner preconditions.

Each refactor planner (rename, extract-variable, extract-method,
inline-variable) historically validated its prerequisites with inline
``raise`` statements. This module lifts those checks into small, named
:class:`Precondition` objects so they can be evaluated independently of the
planners — the composite batch planner re-validates "guarded" intents at
materialization time by re-running a planner's precondition set (TASK-88).

Contract
--------
- A precondition has a stable :attr:`Precondition.name` and an
  ``evaluate() -> PreconditionResult`` method returning a pass/fail flag
  plus, on failure, a reason string identical to the planner's historical
  error message. ``evaluate()`` never raises for an ordinary failed check.
- Inputs are passed explicitly at construction — usually the
  :class:`~pypeeker.storage.IndexStore` /
  :class:`~pypeeker.query.engine.SemanticQueryEngine` and the plan
  parameters. Preconditions that need values computed mid-plan (the resolved
  symbol for rename's conflict check, the parsed CST for extract-variable,
  the range dataflow for extract-method) take those values as constructor
  arguments. Each planner's ``preconditions(...)`` method recomputes those
  inputs, so a caller that wants a check against *current* state should
  rebuild the set through that method rather than re-evaluating cached
  instances whose constructor inputs may have gone stale.
- Resolution-style preconditions cache what they resolved (e.g.
  :attr:`SymbolResolvesUniquely.symbol`) on a successful ``evaluate()`` so
  the planner and dependent preconditions can reuse it without re-querying.
- :func:`evaluate_in_order` drives an ordered set, stopping at the first
  failure. It accepts generators that construct later preconditions from the
  cached results of earlier ones; the generator is never advanced past a
  failing precondition.
- A precondition whose failure the ``check --fix`` report must map onto a
  legacy refusal slug (TASK-125: the six phase-4 remedy planners —
  delete-symbol, remove-import, rewrite-star-import, tuplify, replace-text,
  rename-docstring-param — ported from the superseded fix protocol) carries
  that slug as a
  :attr:`Precondition.slug` class attribute (``"file-missing"`` /
  ``"stale-index"`` / ``"text-mismatch"`` / ``"ambiguous"``). One precondition
  class always has exactly one fixed slug; two checks that happen to share a
  slug are still two classes if their failure wording differs. A planner
  never hardcodes the slug at its raise site — it reads
  ``evaluated[-1].slug`` off the failing precondition and passes it through
  as the error's ``code``, so the mapping lives in exactly one place (this
  module) and the error's ``.precondition`` carries the failing
  precondition's :attr:`~Precondition.name` alongside it. Preconditions with
  no legacy slug (every rename/extract/inline check, all pre-TASK-125) leave
  :attr:`slug` at its default ``None``.
"""

from __future__ import annotations

from pypeeker.refactor.preconditions.base import (
    FileExists,
    FileFresh,
    Precondition,
    PreconditionResult,
    SourceIsUtf8,
    ValidIdentifier,
    evaluate_in_order,
)
from pypeeker.refactor.preconditions.rename import (
    AffectedFilesFresh,
    NewNameDiffers,
    NoScopeNameConflict,
    RenameFlagsCompatible,
    SymbolResolvesUniquely,
)
from pypeeker.refactor.preconditions.extract import (
    ExpressionFound,
    InsideStatement,
    NoControlFlowEscape,
    RangeInsideFunction,
    TopLevelFunctionOnly,
)
from pypeeker.refactor.preconditions.inline import (
    AssignmentLocatable,
    LoadedIndexFresh,
    LocalVariableResolves,
    MultiUseValuePure,
    NotReassigned,
)
from pypeeker.refactor.preconditions.anchored import (
    AnchorFileExists,
    AnchorIndexFresh,
    AnchorTextMatches,
    SymbolMatchFound,
    SymbolMatchUnambiguous,
)
from pypeeker.refactor.preconditions.delete import (
    DeletableScope,
    ScopeSpanClean,
    UndecoratedDefinition,
)
from pypeeker.refactor.preconditions.imports_ops import (
    ImportLineInRange,
    ImportLineSurgerySafe,
    ImportNameUnambiguousOnLine,
    ImportSegmentsLocatable,
    ImportStatementLine,
    SingleStarImportInFile,
    StarAttributionUnambiguous,
    StarLinePlainForm,
    StarSupplyNonEmpty,
    StarTargetModuleIndexed,
    StarTokenMatches,
)
from pypeeker.refactor.preconditions.literals import (
    AssignmentBindsList,
    InferredListBinding,
    ScannableLiteral,
)
from pypeeker.refactor.preconditions.text_ops import (
    OccurrenceExists,
    UniqueOccurrence,
)
from pypeeker.refactor.preconditions.docstring_ops import (
    DocstringScopeLocated,
    DocstringStillPresent,
    DocstringTextFound,
    DocstringTextUnique,
    DocstringTokenFound,
    DocstringTokenUnique,
    DocumentedParamDriftMatches,
    DocumentedParamDriftSingle,
    ParamsSectionPresent,
)
from pypeeker.refactor.preconditions.move import (
    CarriedImportsUnconditional,
    DestinationImportsCompatible,
    DestinationModuleResolvable,
    DestinationPathUnobstructed,
    GUARDED_BINDING,
    ImportBindingReproducible,
    ImportEdgeRewritable,
    MoveIsNotSelf,
    MoveQualifiedUseUnsupported,
    MovedBodyClosed,
    NoDestinationNameCollision,
    SourceExportListClean,
    SourceModuleFree,
    SourceStarImportOpaque,
    TOP_LEVEL_BINDING,
    TopLevelDefinition,
    UNPROVEN_BINDING,
    UnconditionalDefinition,
    ValidModulePath,
)

__all__ = [
    "AffectedFilesFresh",
    "AnchorFileExists",
    "AnchorIndexFresh",
    "AnchorTextMatches",
    "AssignmentBindsList",
    "AssignmentLocatable",
    "CarriedImportsUnconditional",
    "DeletableScope",
    "DestinationImportsCompatible",
    "DestinationModuleResolvable",
    "DestinationPathUnobstructed",
    "DocstringScopeLocated",
    "DocstringStillPresent",
    "DocstringTextFound",
    "DocstringTextUnique",
    "DocstringTokenFound",
    "DocstringTokenUnique",
    "DocumentedParamDriftMatches",
    "DocumentedParamDriftSingle",
    "ExpressionFound",
    "FileExists",
    "FileFresh",
    "GUARDED_BINDING",
    "ImportBindingReproducible",
    "ImportEdgeRewritable",
    "ImportLineInRange",
    "ImportLineSurgerySafe",
    "ImportNameUnambiguousOnLine",
    "ImportSegmentsLocatable",
    "ImportStatementLine",
    "InferredListBinding",
    "InsideStatement",
    "LoadedIndexFresh",
    "LocalVariableResolves",
    "MoveIsNotSelf",
    "MoveQualifiedUseUnsupported",
    "MovedBodyClosed",
    "MultiUseValuePure",
    "NewNameDiffers",
    "NoControlFlowEscape",
    "NoDestinationNameCollision",
    "NoScopeNameConflict",
    "NotReassigned",
    "OccurrenceExists",
    "ParamsSectionPresent",
    "Precondition",
    "PreconditionResult",
    "RangeInsideFunction",
    "RenameFlagsCompatible",
    "ScannableLiteral",
    "ScopeSpanClean",
    "SingleStarImportInFile",
    "SourceExportListClean",
    "SourceIsUtf8",
    "SourceModuleFree",
    "SourceStarImportOpaque",
    "StarAttributionUnambiguous",
    "StarLinePlainForm",
    "StarSupplyNonEmpty",
    "StarTargetModuleIndexed",
    "StarTokenMatches",
    "SymbolMatchFound",
    "SymbolMatchUnambiguous",
    "SymbolResolvesUniquely",
    "TOP_LEVEL_BINDING",
    "TopLevelDefinition",
    "TopLevelFunctionOnly",
    "UNPROVEN_BINDING",
    "UnconditionalDefinition",
    "UndecoratedDefinition",
    "UniqueOccurrence",
    "ValidIdentifier",
    "ValidModulePath",
    "evaluate_in_order",
]
