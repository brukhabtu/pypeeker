"""A module binding `__all__`, which `unused-imports` skips entirely.

String re-exports are invisible to reference analysis, so any import here may
be consumed through `from flocal.exported import *` or through documented API
listings. `json` is unused by reference and still must not be flagged.
"""

import json

__all__ = ["json"]
