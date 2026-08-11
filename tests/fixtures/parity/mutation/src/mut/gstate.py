"""Hidden mutations of module-level state, in every shape the rule detects."""

import os

from mut import support

ITEMS: list[str] = []
COUNTER = 0
CACHE: dict[str, str] = {}


def add_item(value: str) -> None:
    """Shape 2: a mutator call on a module-level container."""
    ITEMS.append(value)


def bump_counter() -> None:
    """Shape 1b: a plain rebind after `global`."""
    global COUNTER
    COUNTER = 1


def accumulate(step: int) -> None:
    """Shape 1a: an augmented assignment landing at module scope."""
    global COUNTER
    COUNTER += step


def remember(key: str, value: str) -> None:
    """Shape 1a: a subscript write on a module-level container."""
    CACHE[key] = value


def set_env(value: str) -> None:
    """Shape 3: an attribute write rooted at an imported module."""
    os.environ["PYPEEKER_FIXTURE"] = value


def retitle(title: str) -> None:
    """Shape 3 where the imported module's name differs from the local one."""
    support.TITLE = title


def ignored_writer(value: str) -> None:
    """Allowed by an fnmatch on this function's own symbol id."""
    ITEMS.append(value)


class Registry:
    """A class whose method mutates module-level state."""

    def register(self, value: str) -> None:
        """The message says 'function' even when the symbol is a method."""
        ITEMS.append(value)
