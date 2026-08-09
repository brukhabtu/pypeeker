"""An internal module the curated barrel deliberately does NOT re-export.

A deep import of `internal_helper` is therefore clean: no barrel path to it
exists, and demanding that one be created is not `barrel-only`'s job.
"""


def internal_helper() -> str:
    """Return the internal label."""
    return "internal"
