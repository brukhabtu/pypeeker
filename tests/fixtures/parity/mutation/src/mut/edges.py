"""Attribution edges: which function a mutation is charged to, and when none is."""

TOTALS: dict[str, int] = {}
TOTAL_COUNT = 0

TOTALS["seed"] = 0


def outer(values: list[str]) -> None:
    """A nested def's mutations are charged to the nested def, not to `outer`."""

    def inner(target: list[str]) -> None:
        """Charged here."""
        target.append("inner")

    inner(values)


def fill(bucket: list[str], values: list[str]) -> None:
    """A comprehension body runs inline, so its mutation is charged to `fill`."""
    [bucket.append(value) for value in values]


def build_class(registry: dict[str, str]) -> type:
    """A class body inside a function runs inline: charged to `build_class`."""

    class Holder:
        """Defined inline; the subscript write below belongs to the function."""

        registry["built"] = "yes"

    return Holder


def make_appender(sink: list[str]):
    """A mutation inside a lambda body has no function symbol to charge."""
    return lambda value: sink.append(value)


def outer_rebind() -> None:
    """The rebind belongs to the innermost containing function."""

    def inner_rebind() -> None:
        """Charged here, not to `outer_rebind`."""
        global TOTAL_COUNT
        TOTAL_COUNT = 2

    inner_rebind()
