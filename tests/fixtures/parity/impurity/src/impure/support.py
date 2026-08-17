"""The pure and impure helpers the rest of the corpus calls."""

import time


def now() -> float:
    """Read the clock: impure directly, through the module-call policy."""
    return time.time()


def label() -> str:
    """A pure helper, so a contract-decorated caller of only this stays silent."""
    return "label"


def chained() -> float:
    """Impure ONLY through `now`.

    Its caller's single observation is therefore a `TransitiveImpureCall`,
    which is about a callee rather than a site and so carries no line — the one
    shape whose summary renders without the ` (line N)` suffix.
    """
    return now()


class Handle:
    """A receiver the binder cannot classify when it is produced inline."""

    def write(self, text: str) -> str:
        """A tracked I/O method name; the use site decides the receiver kind."""
        return text


def make_handle() -> Handle:
    """Return a handle; the HEURISTIC tier comes from the CALL SHAPE alone.

    Measured, not assumed: making this function impure leaves `cached_opaque`'s
    finding byte-identical, because chaining off the call keeps this call
    reference from contributing an observation of its own. The tier flips only
    if the caller binds the handle to a local first — the receiver becomes a
    VARIABLE and the finding reports DECLARED.
    """
    return Handle()


class Maker:
    """Owner of the impure method an import-time call resolves to."""

    @staticmethod
    def build() -> float:
        """Impure, so `Maker.build()` at import time is a project METHOD finding."""
        return time.time()


def blessed_setup() -> float:
    """Impure, and silenced at its import-time call site by the `allow` id glob."""
    return time.time()
