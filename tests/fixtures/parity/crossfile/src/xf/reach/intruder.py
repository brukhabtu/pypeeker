"""Production code reaching into another module's non-public surface.

Three findings, worded "accessed from '<module>' outside its defining module";
`_exempt_fn` is excluded by the `allow` option and `public_fn` by its
visibility.
"""

from xf.reach.owner import (
    __deep_fn,
    _exempt_fn,
    _protected_fn,
    _SECRET,
    public_fn,
)


def peek() -> str:
    """Read every one of them."""
    return _SECRET + _protected_fn() + __deep_fn() + _exempt_fn() + public_fn()
