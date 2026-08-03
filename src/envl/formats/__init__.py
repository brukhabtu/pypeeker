"""Format detection and per-format summarization.

Detection is **registry first, content sniff second**. That ordering is
load-bearing rather than stylistic: content alone is measurably wrong — a
``sed -n '1,200p' pyproject.toml`` opens with ``[project]`` and a naive sniffer
calls it JSON, which would hand a model ``jq`` recipes for a TOML file and
waste exactly the call the envelope exists to save.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from envl.capture import Capture
from envl.config import CommandRule
from envl.formats._base import Summary, Truncation
from envl.formats.diff import _summarize_diff
from envl.formats.json_fmt import _summarize_json
from envl.formats.pytest_fmt import _summarize_pytest
from envl.formats.text import _summarize_search, _summarize_text

__all__ = ["Summary", "Truncation", "detect_format", "summarize"]

FORMATS: tuple[str, ...] = ("json", "diff", "pytest", "search", "text")

_GIT_DIFF = re.compile(r"^diff --git ", re.M)
_OLD_FILE = re.compile(r"^--- ", re.M)
_NEW_FILE = re.compile(r"^\+\+\+ ", re.M)
_HUNK = re.compile(r"^@@ ", re.M)
_PYTEST_TALLY = re.compile(r"\d+ (?:passed|failed|error)")
_PYTEST_NODE = re.compile(r"^\S+\.py::", re.M)
# Kept in step with `text._MATCH_LINE`: a path with spaces is still a match, so
# detection and the per-file tally have to agree about what one looks like.
_SEARCH_LINE = re.compile(r"^[^\s:][^:]*:\d+[:-]")

_SEARCH_RATIO = 0.6

_SUMMARIZERS: dict[str, Callable[[Capture, Path, CommandRule], Summary]] = {
    "json": _summarize_json,
    "diff": _summarize_diff,
    "pytest": _summarize_pytest,
    "search": _summarize_search,
    "text": _summarize_text,
}


def detect_format(command: str, payload: str, *, declared: str | None = None) -> str:
    """Classify ``payload`` as one of json / diff / pytest / search / text.

    A ``declared`` format from the command registry wins outright here —
    :func:`summarize` is where an implausible declaration is caught, once the
    adapter has had its chance and produced nothing. ``command`` is accepted so
    a future registry-free heuristic can use it without a signature change.
    Otherwise the payload is sniffed in a fixed order: diff
    markers, then a **strict** ``json.loads`` (not a ``startswith('{')``
    guess), then pytest tallies, then the ``path:line:`` shape of search
    output, else plain text.
    """
    del command
    if declared in _SUMMARIZERS:
        return str(declared)
    if _is_diff(payload):
        return "diff"
    if _is_json(payload):
        return "json"
    if _is_pytest(payload):
        return "pytest"
    if _is_search(payload):
        return "search"
    return "text"


def summarize(
    fmt: str, capture: Capture, blob_path: Path, rule: CommandRule
) -> Summary:
    """Summarize ``capture`` with the adapter registered for ``fmt``.

    Two ways the requested adapter can be the wrong one, both absorbed here so
    no caller has to second-guess the registry:

    * An **unknown** format name — a mistyped registry entry — falls back to
      the plain-text adapter, so it degrades rather than crashes.
    * A **known but implausible** one is caught by re-checking the result.
      ``format: diff`` on a ``git diff*`` rule also matches ``git diff --stat``
      and ``--name-only``, whose output carries no ``diff --git`` markers at
      all; the diff adapter parses nothing out of it and reports
      ``files: []``, ``totals.files: 0``, ``truncated: false`` — a confident
      assertion that nothing changed, while the real per-file stat sits unread
      on the blob with no excerpt, count or marker pointing at it. Whenever an
      adapter recovers *no structure whatsoever*, its summary is discarded for
      the text adapter's honest line count, excerpt and truncation marker.

    The returned :class:`Summary` carries the format actually used, so the
    envelope's ``format`` field never keeps naming an adapter that failed.
    """
    summarizer = _SUMMARIZERS.get(fmt, _summarize_text)
    summary = summarizer(capture, blob_path, rule)
    if summary.format != "text" and _extracted_nothing(summary):
        return _summarize_text(capture, blob_path, rule)
    return summary


def _extracted_nothing(summary: Summary) -> bool:
    """Report whether an adapter recovered no structure at all from its payload.

    Per-format, because "nothing" has a different shape in each: an
    unparseable JSON document, a diff with zero files, a pytest run with no
    tally and no node ids, a search with no matches. Text is never vacuous —
    it always has a line count — so it is the fallback rather than a subject.
    """
    body = summary.body
    if summary.format == "json":
        return bool(body.get("parse_error"))
    if summary.format == "diff":
        totals = body.get("totals")
        return not (isinstance(totals, dict) and totals.get("files"))
    if summary.format == "pytest":
        return not body.get("counts") and not body.get("failed_nodes")
    if summary.format == "search":
        return not body.get("matches")
    return False


def _is_diff(payload: str) -> bool:
    if _GIT_DIFF.search(payload) is not None:
        return True
    # A headerless unified diff needs all three markers; requiring `@@` keeps
    # documents that merely contain `---`/`+++` lines out of the diff bucket.
    return (
        _OLD_FILE.search(payload) is not None
        and _NEW_FILE.search(payload) is not None
        and _HUNK.search(payload) is not None
    )


def _is_json(payload: str) -> bool:
    stripped = payload.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _is_pytest(payload: str) -> bool:
    if _PYTEST_TALLY.search(payload) is None:
        return False
    return "=== FAILURES ===" in payload or _PYTEST_NODE.search(payload) is not None


def _is_search(payload: str) -> bool:
    lines = [line for line in payload.splitlines() if line.strip()]
    if not lines:
        return False
    hits = sum(1 for line in lines if _SEARCH_LINE.match(line) is not None)
    return hits / len(lines) >= _SEARCH_RATIO
