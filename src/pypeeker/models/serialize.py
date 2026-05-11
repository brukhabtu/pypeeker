"""Serialization helpers for stdlib dataclass models.

Provides JSON round-trip for any frozen dataclass tree built from:
* nested ``@dataclass`` instances
* enums (serialized as their ``.value``)
* ``Optional[X]`` / ``X | None``
* ``list[X]`` / ``tuple[X, ...]``

This replaces pydantic's ``model_dump_json`` / ``model_validate_json`` for
the persisted index and transaction models. Validation guarantees pydantic
offered (runtime type checking) are not preserved — callers are responsible
for passing correct types into constructors.

Usage:
    json_str = to_json(file_index, indent=2)
    file_index = from_json(FileIndex, json_str)
"""

from __future__ import annotations

import dataclasses
import functools
import json
from enum import Enum
from types import NoneType, UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints


@functools.lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    """Cached ``get_type_hints`` — expensive enough to be worth memoizing."""
    return get_type_hints(cls)


@functools.lru_cache(maxsize=None)
def _field_names(cls: type) -> tuple[str, ...]:
    """Cached tuple of dataclass field names."""
    return tuple(f.name for f in dataclasses.fields(cls))


def to_dict(obj: Any) -> Any:
    """Recursively convert a dataclass tree to plain Python data.

    Dataclasses become dicts, enums become their value, lists/tuples are
    converted element-wise. Anything else (str, int, None, ...) passes
    through.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            name: to_dict(getattr(obj, name))
            for name in _field_names(type(obj))
        }
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def from_dict(cls: type, data: Any) -> Any:
    """Recursively construct a dataclass tree from plain Python data.

    Inverse of :func:`to_dict`. Handles nested dataclasses, enums, and
    optional/union/list annotations. Unknown keys in the input dict are
    silently ignored (forwards compatibility with new fields); missing
    keys fall back to the dataclass field's default.
    """
    if not dataclasses.is_dataclass(cls):
        return _coerce(cls, data)

    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for name in _field_names(cls):
        if name in data:
            kwargs[name] = _coerce(hints[name], data[name])
        # else: let the field's default / default_factory apply
    return cls(**kwargs)


def to_json(obj: Any, indent: int | None = None) -> str:
    """Serialize a dataclass tree to JSON."""
    return json.dumps(to_dict(obj), indent=indent)


def from_json(cls: type, data: str) -> Any:
    """Deserialize a JSON string into a dataclass tree of type ``cls``."""
    return from_dict(cls, json.loads(data))


def _coerce(target_type: Any, value: Any) -> Any:
    """Convert ``value`` to ``target_type``, recursing into nested types."""
    if value is None:
        return None

    origin = get_origin(target_type)

    # Optional[X] / X | None — pick the non-None branch.
    if origin is Union or origin is UnionType:
        non_none = [a for a in get_args(target_type) if a is not NoneType]
        if len(non_none) == 1:
            return _coerce(non_none[0], value)
        # Multi-variant union — try the first that doesn't raise.
        for variant in non_none:
            try:
                return _coerce(variant, value)
            except (TypeError, ValueError):
                continue
        return value

    # list[X] / tuple[X, ...]
    if origin is list:
        (elem_type,) = get_args(target_type)
        return [_coerce(elem_type, x) for x in value]
    if origin is tuple:
        args = get_args(target_type)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce(args[0], x) for x in value)
        return tuple(_coerce(t, x) for t, x in zip(args, value))

    # dict[K, V]
    if origin is dict:
        key_t, val_t = get_args(target_type)
        return {_coerce(key_t, k): _coerce(val_t, v) for k, v in value.items()}

    # set[X] / frozenset[X]
    if origin in (set, frozenset):
        (elem_type,) = get_args(target_type)
        coerced = [_coerce(elem_type, x) for x in value]
        return origin(coerced)

    # Enum
    if isinstance(target_type, type) and issubclass(target_type, Enum):
        return target_type(value)

    # Nested dataclass
    if dataclasses.is_dataclass(target_type):
        return from_dict(target_type, value)

    return value
