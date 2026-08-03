"""Summarizer for JSON output: shape, counts, element keys, and jq recipes.

``element_keys`` is the load-bearing field. Without the keys of an array's
elements a model cannot construct a correct ``jq`` path and burns a whole call
discovering the shape — which is exactly the cost the envelope exists to
avoid.

Every collection this module emits is bounded by the caller's ``max_items``,
including the *width* of the document: a payload whose root object carries
20,000 keys (a path-to-size map, a per-symbol index) would otherwise produce an
envelope larger than the payload it replaces, while still reporting
``truncated: false``. Width is truncated exactly like length — by slicing the
Python structure and recording a :class:`Truncation` — so the shrink loop can
reach it and the LOUD marker fires.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from envl.capture import Capture
from envl.config import CommandRule
from envl.formats._base import Summary, Truncation, clip_line, take

_ELEMENT_KEY_SCAN = 50
_NESTED_ITEMS = 3
# Keys are cheap next to values, so shape gets a wider budget than samples do —
# but a budget, and one that shrinks with `max_items` so the envelope ceiling
# stays reachable.
_KEY_BUDGET_FACTOR = 4
# Key *length* is bounded too, and separately from value length: one 400-byte
# key repeated across `top_level`, `sample`, a truncation field name and four
# recipes is enough on its own to blow the envelope ceiling.
_KEY_CHARS_FACTOR = 4
_MIN_KEY_CHARS = 24
_MORE_KEYS = "…"
_ROOT_KEY = "root"
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _summarize_json(capture: Capture, blob_path: Path, rule: CommandRule) -> Summary:
    """Describe a JSON payload's top-level shape and sample its arrays.

    Arrays are sampled by slicing the decoded Python list, never the serialized
    text, and each dropped tail is recorded as a :class:`Truncation`. The same
    applies to the set of top-level keys and to every ``element_keys`` list.
    """
    try:
        document = json.loads(capture.output)
    except (json.JSONDecodeError, ValueError):
        return Summary(format="json", body={"parse_error": True})

    max_items = rule.max_items or 0
    max_line_chars = rule.max_line_chars or 0
    key_budget = max(1, max_items * _KEY_BUDGET_FACTOR)
    key_chars = max(_MIN_KEY_CHARS, max_line_chars // _KEY_CHARS_FACTOR)
    is_object = isinstance(document, dict)

    truncation: list[Truncation] = []
    keys_clipped = _KeyClipping()
    sample: dict[str, Any] = {}
    body: dict[str, Any] = {}
    array_fields: list[tuple[str, list[Any]]] = []

    if is_object:
        items, dropped_keys = take(list(document.items()), key_budget, "summary.top_level")
        if dropped_keys is not None:
            truncation.append(dropped_keys)
        body["top_level"] = {
            _display_key(key, key_chars, keys_clipped): _describe(
                value, key_budget, key_chars, keys_clipped
            )
            for key, value in items
        }
        array_fields = [
            (str(key), value)
            for key, value in items
            if isinstance(value, list) and value
        ]
    else:
        # A non-object root is described under its own key rather than being
        # given a fake top-level name: the name would leak into the jq recipes
        # and produce programs that do not run.
        body["root"] = _describe(document, key_budget, key_chars, keys_clipped)
        if isinstance(document, list) and document:
            array_fields = [(_ROOT_KEY, document)]

    for name, value in array_fields:
        # Empty arrays are already fully described by the shape; sampling and
        # recipe-ing them would spend tokens saying "nothing here".
        # Already counted when `top_level` rendered this key, so clip without
        # recording a second time.
        shown_name = clip_line(name, key_chars)
        head, dropped = take(list(value), max_items, f"summary.sample.{shown_name}")
        sample[shown_name] = [
            _compact(item, max_line_chars, key_budget, key_chars, keys_clipped)
            for item in head
        ]
        if dropped is not None:
            truncation.append(dropped)

    if sample:
        body["sample"] = sample
    truncation.extend(keys_clipped.truncations())
    return Summary(
        format="json",
        body=body,
        truncation=tuple(truncation),
        recipes=_recipes(blob_path, array_fields, is_object, key_budget, key_chars),
    )


class _KeyClipping:
    """Running tally of what the summary had to do to object keys.

    Two distinct losses, kept apart because conflating them would make the LOUD
    marker say something untrue: keys *omitted* from a list, and key names
    *abbreviated* to fit. Each is one aggregate :class:`Truncation` rather than
    one per field — the marker concatenates every record, so a per-field record
    on a 20,000-key document would itself be what blows the ceiling.
    """

    def __init__(self) -> None:
        self.shown = 0
        self.total = 0
        self.names_full = 0
        self.names_all = 0

    def record(self, shown: int, total: int) -> None:
        """Record one key list clipped from ``total`` keys down to ``shown``."""
        self.shown += shown
        self.total += total

    def record_name(self, *, abbreviated: bool) -> None:
        """Record one rendered key name, and whether it had to be abbreviated."""
        self.names_all += 1
        if not abbreviated:
            self.names_full += 1

    def truncations(self) -> list[Truncation]:
        """Return the aggregate records, one per kind of loss that occurred."""
        records: list[Truncation] = []
        if self.total > self.shown:
            records.append(
                Truncation(
                    field="summary object keys", shown=self.shown, total=self.total
                )
            )
        if self.names_all > self.names_full:
            records.append(
                Truncation(
                    field="summary key names not abbreviated",
                    shown=self.names_full,
                    total=self.names_all,
                )
            )
        return records


def _describe(
    value: Any, key_budget: int, key_chars: int, clipping: _KeyClipping
) -> dict[str, Any]:
    kind = _type_name(value)
    described: dict[str, Any] = {"type": kind}
    if kind == "array":
        described["count"] = len(value)
        described["element_type"] = _element_type(value)
        keys = _element_keys(value)
        if keys:
            described["element_keys"] = _clip_keys(
                keys, key_budget, key_chars, clipping
            )
            if len(keys) > key_budget:
                # `count` is the array's length, so the number of distinct
                # element keys has nowhere else to be recorded.
                described["element_keys_total"] = len(keys)
    elif kind == "object":
        # `count` is already the true key total, so a clipped list needs no
        # second number beside it.
        described["count"] = len(value)
        described["element_keys"] = _clip_keys(
            sorted(str(k) for k in value), key_budget, key_chars, clipping
        )
    return described


def _display_key(key: Any, key_chars: int, clipping: _KeyClipping) -> str:
    """Render one key for display, clipped to ``key_chars``.

    A clipped key is marked with the ellipsis, and — see :func:`_recipes` — is
    never used to build a jq path: a truncated key names nothing.
    """
    text = str(key)
    clipping.record_name(abbreviated=len(text) > key_chars)
    return clip_line(text, key_chars)


def _clip_keys(
    keys: list[str], key_budget: int, key_chars: int, clipping: _KeyClipping
) -> list[str]:
    kept = keys
    if len(keys) > key_budget:
        clipping.record(key_budget, len(keys))
        kept = keys[:key_budget]
    return [_display_key(key, key_chars, clipping) for key in kept]


def _element_type(values: list[Any]) -> str:
    kinds = {_type_name(item) for item in values[:_ELEMENT_KEY_SCAN]}
    if not kinds:
        return "empty"
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def _element_keys(values: list[Any]) -> list[str]:
    keys: set[str] = set()
    for item in values[:_ELEMENT_KEY_SCAN]:
        if isinstance(item, dict):
            keys.update(str(k) for k in item)
    return sorted(keys)


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return "null"


def _compact(
    value: Any,
    max_line_chars: int,
    key_budget: int,
    key_chars: int,
    clipping: _KeyClipping,
) -> Any:
    if isinstance(value, str):
        return clip_line(value, max_line_chars)
    if isinstance(value, list):
        return [
            _compact(item, max_line_chars, key_budget, key_chars, clipping)
            for item in value[:_NESTED_ITEMS]
        ]
    if isinstance(value, dict):
        items = list(value.items())
        compacted = {
            _display_key(k, key_chars, clipping): _compact(
                v, max_line_chars, key_budget, key_chars, clipping
            )
            for k, v in items[:key_budget]
        }
        if len(items) > key_budget:
            clipping.record(key_budget, len(items))
            compacted[_MORE_KEYS] = f"+{len(items) - key_budget} more keys"
        return compacted
    return value


def _jq_key(key: str) -> str:
    """Render one object key as a jq path step, quoting whatever needs it.

    ``.check-findings`` parses as ``.check - findings`` and ``.(root)`` does not
    parse at all; both fail at exit 3, and the ``| sort -u`` variant fails at
    exit 0 with empty output, which a model reads as "no values exist".
    """
    if _BARE_KEY.match(key):
        return f".{key}"
    return f".[{json.dumps(key)}]"


def _recipes(
    blob_path: Path,
    array_fields: list[tuple[str, list[Any]]],
    is_object: bool,
    key_budget: int,
    key_chars: int,
) -> tuple[str, ...]:
    blob = str(blob_path)
    recipes = [f"jq 'keys' {blob}" if is_object else f"jq 'length' {blob}"]
    # A key too long to be shown in full is skipped rather than abbreviated: an
    # abbreviated key makes a jq path that resolves to nothing, and
    # `jq … | sort -u` reports that as empty output at exit 0 — a silent wrong
    # answer, which is worse than one recipe fewer.
    named = [(n, v) for n, v in array_fields if not is_object or len(n) <= key_chars]
    for name, value in named[:3]:
        path = _jq_key(name) if is_object else "."
        recipes.append(f"jq '{path}[0:20]' {blob}")
        recipes.append(f"jq '{path} | length' {blob}")
        element_keys = [
            key for key in _element_keys(value)[:key_budget] if len(key) <= key_chars
        ]
        if element_keys:
            step = _jq_key(element_keys[0])
            recipes.append(f"jq -r '{path}[] | {step}' {blob} | sort -u")
    return tuple(recipes)
