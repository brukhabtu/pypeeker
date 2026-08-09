"""Supplies public module-level names to a star import."""

ALPHA = "alpha"


def beta() -> str:
    """Return beta's label."""
    return "beta"


def _hidden() -> str:
    """Underscore-prefixed: a star never supplies this, so it is not attributed."""
    return "hidden"
