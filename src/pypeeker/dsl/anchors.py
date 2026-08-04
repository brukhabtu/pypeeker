"""Evidence-typed anchors, and the loud resolution that produces them.

Fork #12 of ``dsl-rewrite.md``: an anchor lookup that finds nothing, or finds
more than one thing, raises a **structured error carrying candidates** — it
never returns an empty selection for the caller to misread as "no matches".
The distinction is the whole point: *zero rows matched the predicate* and *the
thing you anchored on does not exist* are different answers, and collapsing
them is how a query silently lies.

The other half of the fork is that an anchor carries **evidence**, like every
other fact in this DSL. An id typed at the CLI is :attr:`Confidence.DECLARED`:
the user stated it, and no inference was involved. An anchor produced by
navigation inherits whatever evidence the path to it carried — a symbol reached
through a dynamically-recovered import is ``HEURISTIC`` no matter how exact its
id looks. That evidence joins the meet like any other contribution, so a
finding anchored on a shaky binding cannot report ``DECLARED``.

Resolution is deliberately split across two substrates. **Matching** runs
against the corpus, so an anchor can only ever name a row the selection could
actually produce. **Candidate suggestion** runs against the whole index through
:class:`~pypeeker.query.SemanticQueryEngine`, so a user who typed an id for a
file outside the configured source roots still gets told where it lives instead
of a bare "not found". The tail-match ladder — try the segment after the last
``:``, then after the last ``.`` — repairs the common file-path-versus-module
dialect confusion (``src/pkg/mod.py:Name`` typed for ``pkg.mod:Name``).

Reaching outside the corpus for suggestions imposes two obligations, because a
refusal that a caller cannot act on is worse than a terse one:

* **A candidate is never the anchor itself.** Echoing the input back as "did
  you mean" reads as a taunt to a human and is a dead loop to a machine — the
  retry is byte-identical to the request that failed.
* **An exactly-indexed id gets its own diagnosis.** When the whole index holds
  a symbol with precisely the typed id, "no symbol matches" is simply false;
  the symbol exists and the source-root configuration is what puts it out of
  reach. That is a different problem with a different fix, so it gets different
  prose. Fork #12's structured errors are for a consumer that cannot attach a
  debugger, and the only thing that makes them worth their weight is that they
  carry information the caller did not already have.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pypeeker.dsl.corpus import Corpus
from pypeeker.dsl.errors import AmbiguousAnchorError, UnresolvedAnchorError
from pypeeker.models import Confidence
from pypeeker.query import SemanticQueryEngine

MAX_ANCHOR_CANDIDATES = 10
"""Cap on suggested candidates, matching the CLI's existing refusal shape."""


class AnchorKind(Enum):
    """Which universe an anchor names a row in.

    One member per universe, so an anchor is self-describing: a consumer
    reading ``{"kind": "reference", "id": ...}`` knows not to feed it to a
    symbol-shaped API.
    """

    SYMBOL = "symbol"
    REFERENCE = "reference"
    IMPORT = "import"
    MODULE = "module"
    SCOPE = "scope"


@dataclass(frozen=True)
class Anchor:
    """What a row is *about*, and how well-founded that identification is.

    ``kind`` — the universe the id belongs to.
    ``id`` — the identifying string: a symbol id, a scope id, a module name, or
    for a reference the synthetic ``<symbol-id>@<file>:<line>:<column>``, which
    is stable across runs and points at the exact use site.
    ``evidence`` — how well-founded the identification is. ``DECLARED`` for an
    id the caller typed or the binder recorded directly; weaker when the anchor
    was reached through something less certain.
    """

    kind: AnchorKind
    id: str
    evidence: Confidence = Confidence.DECLARED

    def with_evidence(self, evidence: Confidence) -> Anchor:
        """Return this anchor restated on ``evidence``."""
        return Anchor(kind=self.kind, id=self.id, evidence=evidence)


def _matches(corpus: Corpus, raw: str) -> set[str]:
    """Symbol ids in ``corpus`` that ``raw`` names, by id, tail, or bare name.

    The same four-way match :meth:`SemanticQueryEngine.find_symbol` uses,
    applied to the corpus rather than the whole index so an anchor cannot
    resolve to a file the selection would never visit.
    """
    found: set[str] = set()
    for file_index in corpus.indexes:
        for symbol in file_index.symbols:
            if (
                symbol.symbol_id == raw
                or symbol.name == raw
                or symbol.symbol_id.endswith(f":{raw}")
                or symbol.symbol_id.endswith(f".{raw}")
            ):
                found.add(symbol.symbol_id)
    return found


def _elsewhere(corpus: Corpus, raw: str) -> tuple[bool, tuple[str, ...]]:
    """What the *whole* index knows about an anchor the corpus could not match.

    Returns ``(indexed_exactly, candidates)``.

    ``indexed_exactly`` is True when some indexed symbol carries precisely this
    id: the anchor is real and the source-root configuration is what puts it out
    of the selection's reach, which the caller needs told in those words.

    ``candidates`` are the nearest *other* ids, by the tail ladder, with ``raw``
    itself excluded — suggesting the input back is no information and loops a
    caller that follows suggestions.
    """
    engine = SemanticQueryEngine(corpus.store)
    indexed_exactly = any(symbol.symbol_id == raw for symbol in engine.find_symbol(raw))
    for tail in (raw.rsplit(":", 1)[-1], raw.rsplit(".", 1)[-1]):
        found = sorted({symbol.symbol_id for symbol in engine.find_symbol(tail)} - {raw})
        if found:
            return indexed_exactly, tuple(found[:MAX_ANCHOR_CANDIDATES])
    return indexed_exactly, ()


def resolve_symbol_anchor(
    corpus: Corpus,
    raw: str,
    *,
    evidence: Confidence = Confidence.DECLARED,
) -> Anchor:
    """Resolve ``raw`` to exactly one symbol anchor, or raise.

    Args:
        corpus: the indexes the anchor must name a symbol in.
        raw: the id as the caller typed it — a full symbol id, a trailing
            segment of one, or a bare name.
        evidence: how well-founded this identification is. Defaults to
            ``DECLARED``, which is the right level for an id a user typed at
            the CLI: fork #12 makes a CLI-typed id declared evidence.

    Returns:
        The single matching :class:`Anchor`.

    Raises:
        UnresolvedAnchorError: the corpus matched nothing. Carries the nearest
            *other* indexed ids as ``candidates`` when a tail match exists, and
            an empty tuple when not — never an empty result standing in for the
            failure, and never the anchor itself. When the whole index does hold
            this exact id, the error says so and names the source roots as the
            reason it is unreachable, rather than denying it exists.
        AmbiguousAnchorError: more than one symbol matched. Carries every
            matching id, so the caller can retype an exact one.
    """
    found = _matches(corpus, raw)
    if raw in found:
        # An exact id always wins over the partial matches it happens to be a
        # tail of; otherwise typing the canonical form could be "ambiguous".
        return Anchor(kind=AnchorKind.SYMBOL, id=raw, evidence=evidence)
    if len(found) == 1:
        return Anchor(kind=AnchorKind.SYMBOL, id=found.pop(), evidence=evidence)
    if found:
        raise AmbiguousAnchorError(raw, sorted(found))
    indexed_exactly, candidates = _elsewhere(corpus, raw)
    raise UnresolvedAnchorError(raw, candidates, outside_source_roots=indexed_exactly)
