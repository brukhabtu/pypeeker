"""T: two class bodies and nothing else, so the import DOES run at load.

The other half of the law: a long chain is not automatically deferred. The
chain here is class -> class -> module, all load-time, so cyc.t/cyc.u is a real
cycle.
"""

T = "t"


class Outer:
    """Outer class."""

    class Inner:
        """Inner class."""

        from cyc.u import U
