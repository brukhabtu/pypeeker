"""R: the import's own scope is a class body, but a function encloses it.

The deferred-import law quantifies over EVERY enclosing scope, not just the
immediate one. Here the chain is class -> class -> function -> module, so the
import is deferred and cyc.r/cyc.s are not a cycle.
"""

R = "r"


def outer():
    """Outer."""

    class Outer:
        """Outer class."""

        class Inner:
            """Inner class."""

            from cyc.s import S

    return Outer
