"""G: comprehension-scoped dynamic import is deferred."""
import importlib

G = "g"

_mods = [importlib.import_module("cyc.h") for _ in range(1)]
