"""One star import supplying two used names: the DECLARED plural wording."""

from xf.stars.names import *


def use() -> str:
    """Use two of the starred names, and none of the underscore ones."""
    return ALPHA + beta()
