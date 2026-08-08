"""I: lambda-scoped dynamic import is deferred."""
import importlib

I = "i"

_get_j = lambda: importlib.import_module("cyc.j")
