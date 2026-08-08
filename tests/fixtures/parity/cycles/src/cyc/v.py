"""V: a comprehension inside a method inside a class.

Chain: comprehension -> function -> class -> module. Every scope kind the law
names appears on one chain, and the comprehension alone is enough to defer.
"""
import importlib


class Holder:
    """Holder."""

    def load(self):
        """Load."""
        return [importlib.import_module("cyc.w") for _ in range(1)]


V = "v"
