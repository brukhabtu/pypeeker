"""Capability and confidence enums."""

from enum import Enum


class _Capability(str, Enum):
    """What semantic info a language adapter can provide."""

    VISIBILITY = "visibility"
    STATIC_TYPES = "static_types"
    TYPE_INFERENCE = "type_inference"
    INTERFACES = "interfaces"
    GENERICS = "generics"
    MUTABILITY = "mutability"
    NULLABILITY = "nullability"
    IMPORT_RESOLUTION = "import_resolution"
    CALL_GRAPH = "call_graph"


class Confidence(str, Enum):
    """How reliable a piece of semantic info is."""

    DECLARED = "declared"
    INFERRED = "inferred"
    HEURISTIC = "heuristic"
    UNKNOWN = "unknown"
