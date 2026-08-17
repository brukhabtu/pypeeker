"""A whole module exempted through the MODULE-PATH branch of `allow`.

Both import-time calls below are ordinary findings — one builtin, one module
call — matching no call-name pattern at all. They are silent only because
`impure.settings` is in this rule's `allow` list and the frozen `_allowed`
tests every pattern against the calling module's path as well as against the
call name.
"""

import time

STARTED = time.time()
"""The module-call shape, suppressed by the module path."""

print("settings loaded")
