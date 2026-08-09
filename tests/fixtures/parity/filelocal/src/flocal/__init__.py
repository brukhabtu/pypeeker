"""Corpus for the file-local rule family (see the manifest's `filelocal` target).

The unused `json` binding below is not an oversight: a package `__init__` is a
barrel that re-exports by design, so `unused-imports` skips such files outright
and this binding must never be reported.
"""

import json
