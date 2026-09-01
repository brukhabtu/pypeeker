"""Application service: a throwaway transaction store for simulated re-plans.

Every planner persists the transaction it plans. A workflow that simulates
several intents before writing ONE durable transaction (``batch``, the
``check --fix`` pass, the plural :func:`~pypeeker.app.submit.submit_intents`)
therefore needs somewhere for the per-intent intermediates to go that is not
the project's ``.pypeeker/``. This is that place: a
:class:`~pypeeker.storage.TransactionStore` under a temp directory that dies
with the ``with`` block.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pypeeker.storage import TransactionStore

__all__ = ["scratch_transactions"]


@contextmanager
def scratch_transactions(prefix: str = "pypeeker-scratch-") -> Iterator[TransactionStore]:
    """Yield a throwaway :class:`TransactionStore` under a temp directory.

    ``prefix`` names the directory for anyone inspecting the temp area
    mid-run. Everything written through the store is discarded when the
    block exits, so only the caller's own, deliberately persisted transaction
    reaches the project.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        yield TransactionStore(Path(tmp))
