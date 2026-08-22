"""One surviving row, with the evidence it survived on.

A module of its own, and a very small one, because :class:`Match` is the
**shared contract between the read half and the write half**.
:mod:`pypeeker.dsl.selection` produces them; :mod:`pypeeker.dsl.terminals`
consumes them to decide a repair. The dependency between those two runs
selection -> terminals, so the application operator can live on
:class:`~pypeeker.dsl.Selection` where a caller expects it, and terminals
importing ``Match`` back out of selection would close a real import cycle —
one pypeeker's own ``no-import-cycles`` rule flags, and which an
``if TYPE_CHECKING:`` guard would not fix, since that rule counts a guarded
import as load-time (only a function, lambda or comprehension scope defers
one). Extracting the shared contract to a sibling both can depend on is the
rule's own stated remedy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pypeeker.dsl.anchors import Anchor
from pypeeker.dsl.evidence import Derivation
from pypeeker.models import Confidence

@dataclass(frozen=True)
class Match:
    """One row that survived every stage, with the evidence it survived on.

    ``anchor`` — what the row is about, carrying its own evidence.
    ``fields`` — the currently-visible fields, after any ``project``.
    ``confidence`` — the meet of the row's intrinsic evidence and every
    ``where`` derivation that passed. This is the number a rule ported onto the
    DSL would report, and it is order-independent by construction.
    ``derivations`` — one derivation per ``where`` stage, in written order.
    Plural because a selection may filter more than once, and collapsing them
    into a single node would invent a conjunction the author did not write;
    ``--why`` renders the list.
    """

    anchor: Anchor
    fields: Mapping[str, Any]
    confidence: Confidence
    derivations: tuple[Derivation, ...] = ()
