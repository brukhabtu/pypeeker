"""Functions that answer questions about indexed Python code.

Each module groups related questions by topic. Probes (single-purpose
queries) and compositions (recipes that combine probes) are siblings — no
architectural distinction between them. Compositions live in
:mod:`pypeeker.analysis.purity` and friends; probes live in
:mod:`pypeeker.analysis.writes`, :mod:`pypeeker.analysis.calls`, and
:mod:`pypeeker.analysis.graph`.
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
from pypeeker.analysis.graph import (
    TransitiveImpureCall,
    call_graph,
    functions_reachable_from,
)
from pypeeker.analysis.observations import Observations
from pypeeker.analysis.purity import (
    is_pure,
    purity,
    purity_with_call_graph,
)
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
    # purity (composition)
    "is_pure",
    "purity",
    "purity_with_call_graph",
]
