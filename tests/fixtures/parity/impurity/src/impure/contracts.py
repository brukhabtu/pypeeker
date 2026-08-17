"""Every shape `pure-decorator-contracts` reports, and every silence it keeps."""

import functools
import time
from functools import cache, cached_property

from impure.support import chained, label, make_handle, now


def memoize(fn: object) -> object:
    """A local decorator that is a purity contract ONLY through configuration."""
    return fn


@cache
def cached_clock() -> float:
    """Impure under a bare `@cache`: the first answer is frozen forever."""
    return time.time()


@functools.lru_cache(maxsize=None)
def cached_stamp() -> float:
    """Impure under a prefixed, CALLED decorator.

    The message quotes the decorator head as written — `@functools.lru_cache`,
    with the call parentheses stripped and the module prefix kept — so a port
    normalizing it to `@lru_cache` fails here and nowhere else.
    """
    return time.time()


@memoize
def memoized_clock() -> float:
    """Impure under a decorator that is a contract only because of `decorators`."""
    return time.time()


@cache
def cached_chain() -> float:
    """Impure only transitively: the observation renders with no line suffix."""
    return chained()


@cache
def cached_pile() -> float:
    """FOUR observations, so the summary shows three and a `+1 more` tail."""
    print("one")
    print("two")
    print("three")
    return time.time()


@cache
def cached_opaque() -> str:
    """Impure through a receiver the binder cannot classify: the HEURISTIC tier.

    The call is chained inline deliberately. Binding the handle to a local
    first would classify the receiver as VARIABLE and report DECLARED, which
    silently drops the only place any corpus grades the weak tier at all.
    """
    return make_handle().write("x")


class Box:
    """Properties and dunders under purity contracts, reported and not."""

    @property
    def stamp(self) -> float:
        """Impure under `@property`."""
        return time.time()

    @cached_property
    def slow(self) -> float:
        """Impure under `@cached_property`."""
        return time.time()

    @property
    def __len__(self) -> int:
        """Decorated AND a contract dunder: reported ONCE, as `@property`.

        The frozen `_contract_for` returns on the first matching decorator, so
        the decorator contract wins and the dunder never gets a chance. A port
        that checks both and reports both grades 12-vs-11 here.
        """
        return int(time.time())

    def __eq__(self, other: object) -> bool:
        """An impure dunder with no decorator: the dunder-contract message shape."""
        return time.time() > 0

    def __bool__(self) -> bool:
        """A contract only because `dunders` was extended in configuration."""
        return time.time() > 0

    @property
    def name(self) -> str:
        """Pure under `@property`, so it must stay silent."""
        return label()

    def refresh(self) -> float:
        """Impure, but neither decorated nor a contract dunder: silent."""
        return now()


class Exempt:
    """Impure under a contract, and silenced by the `allow` id glob."""

    @property
    def stamp(self) -> float:
        """Impure under `@property`, and reported nowhere."""
        return time.time()


def __eq__(other: object) -> bool:
    """A module-level FUNCTION named like a dunder: the dunder arm is METHOD-only.

    Nothing in this corpus but this line separates "the symbol's name is a
    configured dunder" from "the symbol is a METHOD whose name is one".
    """
    return time.time() > 0
