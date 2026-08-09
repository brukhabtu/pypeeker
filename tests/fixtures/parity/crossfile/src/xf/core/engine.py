"""An internal module whose two public names the barrel re-exports."""


class Engine:
    """The engine consumers are supposed to reach through the barrel."""

    def run(self) -> str:
        """Return the engine's label."""
        return "engine"


def helper() -> str:
    """Return the helper's label."""
    return "helper"
