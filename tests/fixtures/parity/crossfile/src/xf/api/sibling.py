"""A same-package sibling, reached by a deep import that is always exempt."""


def sibling_value() -> str:
    """Return the sibling's label."""
    return "sibling"
