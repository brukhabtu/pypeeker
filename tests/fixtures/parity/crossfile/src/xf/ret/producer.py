"""Functions that do and do not promise a value."""


def discarded() -> str:
    """Every call site throws the result away."""
    return "discarded"


def used() -> str:
    """One call site keeps the result, so this is never flagged."""
    return "used"


def exempt_producer() -> str:
    """Every call site discards the result, but the `allow` option exempts it."""
    return "exempt"


def procedure() -> None:
    """Annotated `-> None`: never a candidate."""


def unannotated():
    """No return annotation: never a candidate."""
    return "unannotated"
