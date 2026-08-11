"""The `allow` and `extra-mutators` options, graded as silence and as findings."""

QUEUE: list[str] = []


def documented_in_place(items: list[str]) -> None:
    """Allowed by an fnmatch on this function's symbol id: stays silent."""
    items.append("x")


def enqueue_argument(sink: list[str]) -> None:
    """`extra-mutators` promotes `enqueue`, so this parameter mutation fires."""
    sink.enqueue("x")


def enqueue_global(value: str) -> None:
    """`extra-mutators` applies to the module-level container rule too."""
    QUEUE.enqueue(value)
