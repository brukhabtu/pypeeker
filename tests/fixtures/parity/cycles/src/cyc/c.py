"""C: deferred import breaks the cycle."""

C = "c"


def use():
    """Use."""
    from cyc.d import D
    return D
