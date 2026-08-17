"""One ghost and one missing parameter on one function: the RENAMEABLE case.

`docstring-drift`'s `drifted` module holds every *unrepairable* drift shape —
one ghost and nothing missing (`google_ghost`), nothing ghosted and one missing
(`undocumented_second`), and the two-ghost fan-out. None of those earn a repair:
the frozen rule attaches a `RenameDocstringParamIntent` only when a function has
exactly one ghost and exactly one missing parameter, because anything else is
ambiguous about which name replaced which.

This module supplies the one shape that does, and lives beside `drifted` rather
than inside it so the corpus that grades the read half stays byte-identical to
what phase 3 measured. Without it the fix half of the oracle would grade
`docstring-drift:rename-param` on no target at all.

Both drift directions still fire as ordinary findings here, so this extends the
two message shapes' coverage rather than replacing it.

Nothing here is imported anywhere: the module exists to be lint material.
"""


def renamed_param(kept, renamed):
    """Return both parameters.

    Args:
        kept: the surviving parameter.
        oldname: the ghost — renamed to `renamed`, and the docstring did not
            follow.

    Returns:
        A pair of both parameters.
    """
    return kept, renamed
