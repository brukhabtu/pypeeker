"""Owns the non-public names other modules reach into."""

_SECRET = "secret"


def _protected_fn() -> str:
    """Single underscore: PROTECTED."""
    return "protected"


def __deep_fn() -> str:
    """Double underscore, no trailing pair: PRIVATE, not DUNDER."""
    return "deep"


def _exempt_fn() -> str:
    """Reached from another module, but exempted by the `allow` option."""
    return "exempt"


def public_fn() -> str:
    """Public: reaching in from anywhere is fine and must stay silent."""
    return "public"


def _same_module_user() -> str:
    """A same-module reach-in, which must stay silent."""
    return _SECRET + _protected_fn()
