"""Confidence enum."""

from enum import Enum


class Confidence(str, Enum):
    """How reliable a piece of semantic info is."""

    DECLARED = "declared"
    INFERRED = "inferred"
    HEURISTIC = "heuristic"
    UNKNOWN = "unknown"
