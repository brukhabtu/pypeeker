"""Both rules are configured to ignore this module: every mutation is silent."""

BUFFER: list[str] = []


def collect(items: list[str], value: str) -> None:
    """Silenced by symbol-id glob for one rule and by module path for the other."""
    items.append(value)
    BUFFER.append(value)
