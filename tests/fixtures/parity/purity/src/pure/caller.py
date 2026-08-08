"""Caller."""
from pure.io import writes


def caller(path):
    """Transitively impure."""
    return writes(path)
