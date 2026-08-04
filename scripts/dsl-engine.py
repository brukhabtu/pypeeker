"""The differential harness's new-engine entry point.

The three lines below cannot live under ``src/``: a ``__name__`` guard there
fails pypeeker's own zero-baseline self-lint twice — ``no-unresolved-refs`` on
``'__name__'`` and ``import-time-side-effects`` on the guarded call, both at
DECLARED tier. ``scripts/`` is not listed in ``[tool.pypeeker].src``, so it is
not self-linted, and the real surface stays a plain importable module in
:mod:`pypeeker.dsl.differential`.
"""

import sys

from pypeeker.dsl.differential import main

if __name__ == "__main__":
    sys.exit(main())
