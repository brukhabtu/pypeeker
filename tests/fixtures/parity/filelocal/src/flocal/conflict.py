"""Two unused bindings on ONE import line: the byte-range conflict case.

`unused-imports` finds both `dumps` and `loads` and attaches a
`RemoveImportIntent` to each. Both repairs rewrite the same `from json import
...` statement, so their planned edits overlap: the first to be considered
wins and the second is reported under `skipped_conflicts` rather than applied.

That bucket fires on no other target in the manifest, and a fix-level oracle
that never exercises it would grade the de-conflict itself at zero — the
comparison is over an ordered fix list precisely so that "which repair won"
is graded, not merely "how many landed". Splitting the two names across two
import statements would produce two clean fixes and lose the case.

Nothing here is imported anywhere: the module exists to be lint material.
"""

from json import dumps, loads
