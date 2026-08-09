"""Two star imports in one file: both findings are HEURISTIC, and attribution
is first-star-wins.

`ALPHA` is defined by `names` and `gamma` by `other`, so the first star claims
`ALPHA` and the second claims what is left. Each supplies exactly one name,
which is the singular wording ("1 name actually used").
"""

from xf.stars.names import *
from xf.stars.other import *


def use_both() -> str:
    """Use one name from each starred module."""
    return ALPHA + gamma()
