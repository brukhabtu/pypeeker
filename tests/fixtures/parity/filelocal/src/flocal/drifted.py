"""Docstring params sections that drifted from their signatures.

`docstring-drift` material for the parity oracle: ghosts in all three
recognized styles, a two-ghost function (one symbol, two findings, one line),
an undocumented parameter that only fires under `require-complete`, a method
whose `self` must not read as undocumented, and a docstring with no params
section at all, which must not fire in either direction.

Every function here is documented, so `require-docstrings` stays quiet and the
only rule this module feeds is `docstring-drift`.
"""


def google_ghost(kept):
    """A google section documenting one parameter that no longer exists.

    Args:
        kept: the surviving parameter.
        removed: deleted from the signature; the docstring did not follow.

    Returns:
        The surviving parameter.
    """
    return kept


def two_ghosts(kept):
    """Two ghosts on one symbol: the fan-out case, both findings on this line.

    Args:
        kept: the surviving parameter.
        gone_first: deleted from the signature.
        gone_second: also deleted from the signature.

    Returns:
        The surviving parameter.
    """
    return kept


def undocumented_second(first, second):
    """Only the first parameter is documented.

    Args:
        first: documented.

    Returns:
        A pair of both parameters.
    """
    return first, second


def numpy_ghost(kept):
    """A numpy section with a ghost.

    Parameters
    ----------
    kept
        the surviving parameter.
    stale
        deleted from the signature.

    Returns
    -------
    object
        The surviving parameter.
    """
    return kept


def sphinx_ghost(kept):
    """A sphinx field list with a ghost.

    :param kept: the surviving parameter.
    :param dropped: deleted from the signature.
    :returns: the surviving parameter.
    """
    return kept


def no_params_section(anything):
    """Prose only, with no recognized params section anywhere in it.

    Demanding a section where none exists is `require-docstrings`' turf, so
    neither drift direction may fire here — not even under
    `require-complete`.
    """
    return anything


class Holder:
    """A class whose method's docstring drifted."""

    def method_ghost(self, kept):
        """`self` is never expected in a params section and is not missing.

        Args:
            kept: the surviving parameter.
            vanished: deleted from the signature.

        Returns:
            The surviving parameter.
        """
        return kept
