"""Shapes the rest of the corpus mutates."""

TITLE = "untitled"


class Config:
    """A container with an attribute chain worth mutating."""

    def __init__(self) -> None:
        """Two mutable attributes, so a two-segment receiver chain resolves."""
        self.options: dict[str, str] = {}
        self.enabled = False
