"""Every call shape `import-time-side-effects` reports, and every silence it keeps.

The three shapes the frozen `_describe_call` partitions calls into are all
here, in its own order: bare/builtin names, module-qualified names, and calls
that resolve to an impure project function or method.
"""

import logging
import os
import shelve
import subprocess
import time
from os import path

from impure.support import Maker, blessed_setup, now

LOGGER = logging.getLogger(__name__)
"""Named in `extra-impure`, so only DEFAULT_ALLOW can keep this silent.

`logging` is in no denylist, so a bare `logging.getLogger(__name__)` would be
silent whether or not the rule had a default allowance — grading nothing. The
corpus therefore configures it as impure, leaving the frozen precedence
(DEFAULT_ALLOW is prepended to the configured `allow`, and `_allowed` is
consulted before a finding is emitted) as the sole reason for the silence.
"""

print("booting")

COMPLETED = subprocess.run(["true"], check=False)
"""Shape 2 through a plain `import subprocess` root."""

HAS_CONF = os.path.exists("conf.ini")
"""Shape 2 through a TWO-segment receiver chain: `os` + `path` + `exists`."""

HAS_ALT = path.exists("alt.ini")
"""The same dotted name assembled from a FROM-import root: `imported_from` wins."""

STAMP = now()
"""Shape 3 onto a project FUNCTION."""

STORE = shelve.open("cache.db")
"""Shape 2 only because `extra-impure` carries the dotted name."""

STARTED_AT = Maker.build()
"""Shape 3 onto a project METHOD.

Its qualified name assembles to `impure.support.build`, which is NOT in the
module denylist — so this also grades the frozen `elif`'s fall-through: a
non-None qualified name that misses the table must reach shape 3 rather than
end the classification.
"""

BANNER = [print(part) for part in ("a", "b")]
"""A module-level COMPREHENSION scope still runs during the import."""

log("boot")  # noqa: F821 — deliberately unresolved

quit()

blessed_setup()


class Boot:
    """A class body executes during the import, so the call in it is import-time."""

    STARTED = now()


def deferred() -> float:
    """A function body runs only when called, so nothing in here is reported."""
    return time.time()


def outer() -> type:
    """A class nested INSIDE a function: one scope too deep to run at import."""

    class Inner:
        """Its body waits for `outer()` to be called, so it stays silent."""

        STARTED = now()

    return Inner
