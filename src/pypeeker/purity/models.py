"""Models for purity analysis results."""

from enum import Enum

from pydantic import BaseModel

from pypeeker.models.capabilities import Confidence


class PurityVerdict(str, Enum):
    """Overall purity verdict for a function.

    IMPURE: Concrete evidence of side effects was found.
    PROBABLY_PURE: No evidence of impurity found, but absence of evidence is
        not proof of purity (e.g., unresolved external calls cannot be analyzed).
    UNKNOWN: The symbol could not be analyzed (not found, wrong kind, etc.).
    """

    IMPURE = "impure"
    PROBABLY_PURE = "probably_pure"
    UNKNOWN = "unknown"


class EvidenceKind(str, Enum):
    """Categories of evidence the checker can produce."""

    WRITES_OUTER_SCOPE = "writes_outer_scope"
    GLOBAL_DECLARATION = "global_declaration"
    NONLOCAL_DECLARATION = "nonlocal_declaration"
    ATTRIBUTE_WRITE = "attribute_write"
    CALLS_IMPURE_BUILTIN = "calls_impure_builtin"
    CALLS_IMPURE_STDLIB = "calls_impure_stdlib"
    UNRESOLVED_EXTERNAL_CALL = "unresolved_external_call"
    NOT_FOUND = "not_found"
    NOT_A_FUNCTION = "not_a_function"


class Evidence(BaseModel):
    """A single piece of evidence supporting a purity verdict."""

    kind: EvidenceKind
    line: int | None = None
    target: str | None = None
    detail: str | None = None


class PurityResult(BaseModel):
    """Result of analyzing a function for purity."""

    symbol_id: str
    verdict: PurityVerdict
    confidence: Confidence
    evidence: list[Evidence] = []
