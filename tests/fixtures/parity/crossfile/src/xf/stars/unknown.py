"""A star import whose target is outside the corpus: HEURISTIC, names unknown.

The target is not indexed, so the rule reports before it computes anything —
this is the one branch where the file's own star count does not decide the
confidence tier.
"""

from os.path import *
