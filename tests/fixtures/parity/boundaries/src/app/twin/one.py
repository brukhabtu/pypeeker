"""Module half of a colliding module id, in a package the allow table polices.

`app/twin/one.py` and `app/twin/one/__init__.py` both bind the module id
`app.twin.one`, so both bind the import symbol id `app.twin.one:go` — one id,
two occurrences, in two different files, violating for two different reasons.
A verdict table keyed on that id holds one entry and the second occurrence
reads the first's answer, so the finding COUNT stays right while both messages
name whichever package was judged last. That is the shape no count comparison
catches, which is why it is a fixture and not a unit test alone.

The target is unresolvable on purpose: `_import_origin_package` then falls back
to the literal `imported_from`, which is per-occurrence, so the two halves
genuinely disagree about the package they must be charged to.
"""

from app.ghostpkg.z import go
