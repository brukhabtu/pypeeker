"""Assembling the envelope: shape, counts, loud truncation markers, blob, recipes.

The envelope is one flat JSON object. It is built as a Python structure and
serialized exactly once, at the end — no code path here slices a serialized
string, so "always valid JSON, including when truncated" is a structural
property rather than something a length check has to defend.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from envl.cache import BlobRecord
from envl.capture import Capture
from envl.config import CommandRule, EnvelopeConfig
from envl.formats import Truncation, detect_format, summarize
from envl.formats._base import clip_line

ENVELOPE_VERSION = "envl/1"

# The echoed command is the one envelope field carrying arbitrary-length caller
# input: a heredoc-bearing `cd /tmp && cat > s.py <<'PYEOF' ...` capture in the
# fixture corpus has a 2091-character command. The shrink loop can only shrink
# the summary, so without this clip a fat command alone pushes the envelope past
# `max_envelope_bytes` and the documented ceiling is not a ceiling. The full
# command is still recorded, untouched, in the cache manifest.
_MAX_COMMAND_CHARS = 400

_SNAPSHOT_NOTE = (
    "immutable snapshot of THIS run; re-run the command for current state"
)
_SHRINK_ROUNDS = 6
_MIN_LINE_CHARS = 80
_EXCERPT_KEYS = ("head", "tail", "sample", "first_failure", "excerpt")


def should_envelope(capture: Capture, config: EnvelopeConfig) -> bool:
    """Report whether ``capture``'s output is big enough to be worth enveloping.

    Below ``threshold_bytes``, or with the kill switch off, the caller passes
    the raw output through untouched and writes no blob.
    """
    if not config.enabled:
        return False
    return len(capture.output.encode("utf-8")) >= config.threshold_bytes


def build_envelope(
    capture: Capture, config: EnvelopeConfig, blob: BlobRecord
) -> dict[str, Any]:
    """Build the envelope dict for ``capture``, shrinking it under the size ceiling.

    The bounded shrink loop is what makes the always-valid-JSON guarantee hold
    under *adversarial* input, not merely typical input: a 900-file diff still
    produces a fat summary at the default item limits, so the loop halves the
    per-format limits and, as a last resort, drops the excerpt keys entirely
    and rebuilds. Every step operates on the Python structure before
    serialization, so no intermediate state is ever invalid JSON.

    The loop can only shrink the summary, so the one unbounded fixed-cost field
    — the echoed command — is clipped separately; see ``_MAX_COMMAND_CHARS``.
    """
    rule = config.settings_for(capture.command)
    fmt = detect_format(capture.command, capture.output, declared=rule.format)
    envelope = _assemble(capture, blob, fmt, rule, drop_excerpts=False)
    for _ in range(_SHRINK_ROUNDS):
        if _serialized_bytes(envelope) <= config.max_envelope_bytes:
            return envelope
        shrunk = _shrink(rule)
        if shrunk == rule:
            break
        rule = shrunk
        envelope = _assemble(capture, blob, fmt, rule, drop_excerpts=False)
    if _serialized_bytes(envelope) > config.max_envelope_bytes:
        envelope = _assemble(capture, blob, fmt, rule, drop_excerpts=True)
    return envelope


def envelope_json(envelope: dict[str, Any]) -> str:
    """Serialize an envelope to a single-line JSON string."""
    return json.dumps(envelope, ensure_ascii=False)


def _serialized_bytes(envelope: dict[str, Any]) -> int:
    """Size the ceiling is measured in: UTF-8 bytes, as ``max_envelope_bytes`` says.

    Characters would be the flattering measure — a summary of CJK or Cyrillic
    output is up to three bytes per character, so a char-counted ceiling is not
    the ceiling the setting names.
    """
    return len(envelope_json(envelope).encode("utf-8"))


def _shrink(rule: CommandRule) -> CommandRule:
    items = max(1, (rule.max_items or 1) // 2)
    lines = max(1, (rule.max_lines or 1) // 2)
    chars = max(_MIN_LINE_CHARS, (rule.max_line_chars or _MIN_LINE_CHARS) // 2)
    return replace(rule, max_items=items, max_lines=lines, max_line_chars=chars)


def _assemble(
    capture: Capture,
    blob: BlobRecord,
    fmt: str,
    rule: CommandRule,
    *,
    drop_excerpts: bool,
) -> dict[str, Any]:
    summary = summarize(fmt, capture, blob.path, rule)
    body = dict(summary.body)
    truncation = list(summary.truncation)
    if drop_excerpts:
        body, truncation = _drop_excerpts(body, truncation)
    command = clip_line(capture.command, _MAX_COMMAND_CHARS)
    envelope: dict[str, Any] = {
        "envelope": ENVELOPE_VERSION,
        "command": command,
        "cwd": capture.cwd,
        "exit_code": capture.exit_code,
        "captured_at": capture.captured_at,
        # `summary.format` rather than `fmt`: `summarize` demotes an adapter
        # that recovered no structure from the payload to the text adapter, and
        # the envelope must not keep announcing the format that failed.
        "format": summary.format,
        "bytes": blob.bytes,
        "lines": len(capture.output.splitlines()),
        "truncated": bool(truncation),
    }
    if command != capture.command:
        # Distinct from `truncated`, which means payload content was dropped and
        # can be recovered from the blob. A clipped command is not recoverable
        # from the blob; it is in the cache manifest.
        envelope["command_clipped"] = True
    if truncation:
        envelope["TRUNCATED"] = _truncation_message(truncation, blob)
        envelope["dropped"] = [
            {"field": t.field, "shown": t.shown, "total": t.total} for t in truncation
        ]
    envelope["summary"] = body
    envelope["blob"] = {
        "path": str(blob.path),
        "bytes": blob.bytes,
        "sha256": blob.digest,
        "note": _SNAPSHOT_NOTE,
    }
    envelope["recipes"] = list(summary.recipes)
    return envelope


def _truncation_message(records: list[Truncation], blob: BlobRecord) -> str:
    parts = [
        f"SHOWING {t.shown} OF {t.total} in {t.field} ({t.total - t.shown} DROPPED)"
        for t in records
    ]
    return "; ".join(parts) + f". FULL OUTPUT IS ON DISK AT {blob.path}"


def _drop_excerpts(
    body: dict[str, Any], truncation: list[Truncation]
) -> tuple[dict[str, Any], list[Truncation]]:
    """Remove the excerpt keys and rewrite their truncation records to ``shown=0``.

    Deleting ``head`` / ``sample`` / ``first_failure`` without touching
    ``truncation`` is the one way this module can emit a *dishonest* envelope,
    in two shapes. A summary whose only truncation was its excerpt loses the
    excerpt and still reports ``truncated: false`` with no LOUD marker, so a
    model reads a silently amputated summary as a complete one. A summary that
    keeps other truncations reports ``SHOWING 8 OF 400 in summary.first_failure``
    for a key that is no longer in the document, so a model told to read it
    finds nothing and cannot tell "no failure block" from "withheld".

    So each removed key leaves a ``SHOWING 0 OF n`` record behind — rewritten
    in place when it already had one, appended otherwise. ``truncated``, the
    marker, ``dropped`` and ``summary`` then stay mutually consistent by
    construction.
    """
    removed = {key: value for key, value in body.items() if key in _EXCERPT_KEYS}
    if not removed:
        return body, truncation
    kept = {key: value for key, value in body.items() if key not in removed}
    # Prefix, not equality: the JSON adapter records its samples per array as
    # `summary.sample.<name>`, so dropping `sample` drops those records' field.
    names = tuple(f"summary.{key}" for key in removed)
    rewritten = [
        replace(record, shown=0) if _names_any(record.field, names) else record
        for record in truncation
    ]
    already = {record.field for record in truncation}
    for key, value in removed.items():
        name = f"summary.{key}"
        if any(_names_any(field, (name,)) for field in already):
            continue
        rewritten.append(Truncation(field=name, shown=0, total=_item_count(value)))
    return kept, rewritten


def _names_any(field: str, names: tuple[str, ...]) -> bool:
    return any(field == name or field.startswith(f"{name}.") for name in names)


def _item_count(value: Any) -> int:
    return len(value) if isinstance(value, (list, dict, str)) else 1
