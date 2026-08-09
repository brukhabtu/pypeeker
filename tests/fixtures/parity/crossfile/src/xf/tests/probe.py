"""A test-shaped path, so the reach-in below gets the other wording.

`under-exposed-access` ships eight default test globs and this file's path
matches `*/tests/*`, which is what makes the "accessed from tests" line graded
by the oracle rather than only by unit test. The file is named `probe.py`
rather than `test_probe.py` on purpose: pytest's own collection would try to
import a `test_*.py` under `tests/fixtures/`, and a fixture corpus must not be
importable as a test module.
"""

from xf.reach.owner import _SECRET


def probe() -> str:
    """Read a protected name from test code."""
    return _SECRET
