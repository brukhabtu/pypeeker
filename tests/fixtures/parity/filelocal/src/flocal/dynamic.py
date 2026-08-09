"""A file calling `getattr`, so every unused-import finding here is HEURISTIC.

`globals()["json"]` and friends can consume an import invisibly, so the frozen
rule downgrades the whole file's findings rather than dropping them. `json` is
unused and IS reported — at HEURISTIC, which is the half a count-only
comparison cannot see.
"""

import json


def fetch(obj, name):
    """Read an attribute dynamically, which is what arms the downgrade."""
    return getattr(obj, name)
