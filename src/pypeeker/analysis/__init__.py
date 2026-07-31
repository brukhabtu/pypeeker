"""Functions that answer questions about indexed Python code.

Each module groups related questions by topic. Probes (single-purpose
queries) and compositions (recipes that combine probes) are siblings — no
architectural distinction between them. Compositions live in
:mod:`pypeeker.analysis.purity` and friends; probes live in
:mod:`pypeeker.analysis.writes`, :mod:`pypeeker.analysis.calls`,
:mod:`pypeeker.analysis.graph`, and :mod:`pypeeker.analysis.docstrings`.

Some probes are shared across the ``check`` / ``refactor`` divide on purpose:
:mod:`pypeeker.analysis.docstrings` is what the ``docstring-drift`` rule and
the planner that repairs its findings both parse with, since neither of those
packages may import the other.
"""

from pypeeker.analysis.calls import (
    AttributeMethodCall,
    BareCall,
    ModuleCall,
    ReceiverKind,
    attribute_method_calls,
    bare_calls,
    module_calls,
)
from pypeeker.analysis.context import AnalysisContext, ContextError
from pypeeker.analysis.docstrings import (
    DOCSTRING_STYLES,
    ParamsSection,
    param_drift,
    parse_documented_params,
    signature_params,
)
from pypeeker.analysis.graph import (
    TransitiveImpureCall,
    call_graph,
    functions_reachable_from,
)
from pypeeker.analysis.hierarchy import BaseRef, Hierarchy
from pypeeker.analysis.observations import Observations
from pypeeker.analysis.purity import impurities
from pypeeker.analysis.traits import Trait, get_trait_provider, register_trait
from pypeeker.analysis.type_annotation import TYPE_ANNOTATION, is_inferred_list
from pypeeker.analysis.variable_mutation import VARIABLE_MUTATION, VariableMutation
from pypeeker.analysis.writes import (
    AttributeWrite,
    OuterScopeWrite,
    attribute_writes,
    outer_scope_writes,
)

__all__ = [
    # context
    "AnalysisContext",
    "ContextError",
    # docstrings
    "DOCSTRING_STYLES",
    "ParamsSection",
    "param_drift",
    "parse_documented_params",
    "signature_params",
    # container
    "Observations",
    # writes
    "AttributeWrite",
    "OuterScopeWrite",
    "attribute_writes",
    "outer_scope_writes",
    # calls
    "AttributeMethodCall",
    "BareCall",
    "ModuleCall",
    "ReceiverKind",
    "attribute_method_calls",
    "bare_calls",
    "module_calls",
    # graph
    "TransitiveImpureCall",
    "call_graph",
    "functions_reachable_from",
    # hierarchy
    "BaseRef",
    "Hierarchy",
    # purity (composition)
    "impurities",
    # traits
    "Trait",
    "get_trait_provider",
    "register_trait",
    # variable-mutation trait (proof migration, TASK-127)
    "VARIABLE_MUTATION",
    "VariableMutation",
    # type-annotation trait (second proven pair, TASK-128)
    "TYPE_ANNOTATION",
    "is_inferred_list",
]
