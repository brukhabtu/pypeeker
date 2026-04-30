"""Pure-function fact extractors over an AnalysisContext.

Facts are typed observations on the indexed semantic data. They report what
the code does; checks decide what that means.
"""

from pypeeker.analysis.facts.calls import (
    find_attribute_method_calls,
    find_impure_builtin_calls,
)
from pypeeker.analysis.facts.models import (
    AttributeMethodCall,
    AttributeWrite,
    ImpureBuiltinCall,
    OuterScopeWrite,
)
from pypeeker.analysis.facts.writes import (
    find_attribute_writes,
    find_outer_scope_writes,
)

__all__ = [
    "AttributeMethodCall",
    "AttributeWrite",
    "ImpureBuiltinCall",
    "OuterScopeWrite",
    "find_attribute_method_calls",
    "find_attribute_writes",
    "find_impure_builtin_calls",
    "find_outer_scope_writes",
]
