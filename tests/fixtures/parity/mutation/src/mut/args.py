"""Parameter mutations, in every shape the rule detects."""

from mut.support import Config


def extend_items(items: list[str], value: str) -> None:
    """Mutator call whose receiver root is a parameter (single-segment chain)."""
    items.append(value)


def update_options(cfg: Config, extra: dict[str, str]) -> None:
    """Mutator call through a two-segment receiver chain."""
    cfg.options.update(extra)


def set_flag(target: Config) -> None:
    """Attribute write on a parameter receiver."""
    target.enabled = True


def store_first(xs: list[int]) -> None:
    """Subscript write whose root is a parameter."""
    xs[0] = 1


def bump(n: int) -> int:
    """Augmented assignment on a parameter: the documented coarseness."""
    n += 1
    return n


class Box:
    """A class whose methods separate self-mutation from argument mutation."""

    def __init__(self) -> None:
        """Writing to self is the job of a method, not an argument mutation."""
        self.contents: list[str] = []

    def absorb(self, other: list[str]) -> None:
        """A METHOD-kind finding: the message says 'method', not 'function'."""
        other.append("absorbed")

    def keep(self, value: str) -> None:
        """Mutating self must stay silent."""
        self.contents.append(value)
