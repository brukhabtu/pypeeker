"""A general container for analysis observations.

Used by every probe and composition to return zero or more typed
observations. Standard Python container semantics:

* ``bool(o)`` — ``True`` iff non-empty
* ``len(o)`` — number of observations
* ``for x in o`` — iterate observations in source order
* ``o[i]`` / ``o[i:j]`` — index / slice

For the purity composition: an empty :class:`Observations` means "pure"
(no impurity found); a non-empty one means "impure with these reasons."
Other compositions can give the bool different meaning per context — but
the underlying convention (empty=falsy, non-empty=truthy) matches every
other Python container, so it doesn't surprise readers.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Observations(Generic[T]):
    """A collection of observations from an analysis."""

    _observations: tuple[T, ...] = ()

    def __bool__(self) -> bool:
        return bool(self._observations)

    def __iter__(self) -> Iterator[T]:
        return iter(self._observations)

    def __len__(self) -> int:
        return len(self._observations)

    def __getitem__(self, index):
        return self._observations[index]
