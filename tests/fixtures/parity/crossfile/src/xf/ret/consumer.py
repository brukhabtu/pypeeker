"""Calls the producers. `discarded` is called four times, all discarding, which
is one more than the message shows — so the `+N more` tail is graded here.
"""

from xf.ret.producer import discarded, exempt_producer, procedure, unannotated, used


def run() -> None:
    """Call everything, keeping only one result."""
    discarded()
    discarded()
    discarded()
    discarded()
    exempt_producer()
    procedure()
    unannotated()
    kept = used()
    del kept
