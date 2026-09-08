"""Project-level configuration shared across the tool.

Exposes the source roots declared under ``[tool.pypeeker]`` in
``pyproject.toml``. These were originally read only by the ``check`` command;
they're general project config now — the indexer needs them to map file paths
to dotted module paths.

Also owns the ``[tool.pypeeker.visibility]`` section
(:class:`VisibilityConfig`): project-wide knobs the visibility / dead-code
rules consume so library authors can declare their public API surface instead
of allow-listing rule by rule.

Finally, this module owns ``[tool.pypeeker]`` **option coercion** — the one
implementation of "what shape may this option hold, and what happens when it
holds something else" (:func:`coerce_str_list`, :func:`coerce_enum_set`,
:func:`coerce_visibility_table`). It lives here because this is the only
module every consumer can legally reach: ``dsl`` and ``refactor`` may import
``project``, ``cli`` is unconstrained, and ``project`` imports nothing under
``pypeeker``, so a coercion here can create no cycle. Six near-copies of that
question used to answer it six ways, three of them silently (TASK-163); every
one of them now refuses through :class:`ConfigOptionError`, naming the option,
the offending value and the accepted values.
"""

from __future__ import annotations

import tomllib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

DEFAULT_SRC_ROOTS: tuple[str, ...] = ("src",)

VISIBILITY_MODES: tuple[str, ...] = ("app", "library")
"""Valid values for ``[tool.pypeeker.visibility].mode``."""

VISIBILITY_KEYS: tuple[str, ...] = ("mode", "public-roots", "allow-decorators")
"""The keys ``[tool.pypeeker.visibility]`` accepts; anything else refuses.

Spelled out rather than ignored because the near-misses are silent today:
``public_roots`` (underscore) parses as TOML, is never read, and leaves a
library-mode project protecting nothing at all.
"""


class ConfigOptionError(ValueError):
    """A ``[tool.pypeeker]`` option holds a value the tool will not guess about.

    Carries the option key, the offending value and the accepted values so the
    message can name all three — a user fixes config by searching the key in
    ``pyproject.toml``, so the key is what the message leads with.

    A ``ValueError``, not a ``DslError``: ``DslError`` exists for
    expression-*authoring* mistakes and is rendered through the JSON envelope,
    whereas a misconfigured ``[tool.pypeeker]`` is a usage error and ``cli``
    renders it as a :exc:`click.UsageError`, beside the two config refusals
    ``check`` already renders that way.
    """

    code = "config-option"

    def __init__(
        self, option: str, value: Any, expected: str, *, hint: str | None = None
    ) -> None:
        self.option = option
        self.value = value
        self.expected = expected
        self.message = (
            f"[tool.pypeeker] option {option!r}: {value!r} is not accepted; "
            f"expected {expected}"
        )
        if hint:
            self.message = f"{self.message}. {hint}"
        super().__init__(self.message)


def coerce_str_list(
    option: str, raw: Any, *, allow_scalar: bool = True
) -> tuple[str, ...]:
    """Coerce a TOML option value to a tuple of strings, or refuse.

    ``None`` and ``""`` yield ``()``. A list or tuple is stringified
    element-wise (frozen behaviour, harmless, changes no finding) — which means
    callers must pass plain **strings**, never enum members: ``SymbolKind`` is a
    ``str``-mixin ``Enum`` whose ``str()`` is ``'SymbolKind.FUNCTION'``, not
    ``'function'``.

    ``allow_scalar`` is the entire axis of difference between the two kinds of
    option this tool has, which is why this stays one function. Rule options
    keep the convenience that ``kinds = "class"`` means one value (the frozen
    contract). The three top-level list keys — ``rules``, ``plugins``, ``src``
    — pass ``allow_scalar=False``, because there a bare string was silently
    iterated per character: ``rules = "prefer-tuple"`` refused with ``unknown
    expression 'p'``, ``src = "pkg"`` matched no file and exited 0.

    Anything else (a Mapping, an int, a bool) refuses rather than being
    iterated or stringified into nonsense.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        if not allow_scalar:
            raise ConfigOptionError(
                option,
                raw,
                "a list of strings",
                hint=(
                    f"write {option} = [{raw!r}] — a bare string is a list of "
                    "characters, not one name"
                ),
            )
        return (raw,) if raw else ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(value) for value in raw)
    raise ConfigOptionError(
        option,
        raw,
        "a list of strings",
        hint=f"got a {type(raw).__name__}",
    )


def coerce_enum_set[E: Enum](
    option: str,
    raw: Any,
    enum_cls: type[E],
    *,
    default: Sequence[str] = (),
    choices: Collection[E] | None = None,
) -> tuple[E, ...]:
    """Coerce an option into enum members, refusing anything that will not parse.

    An absent or empty option falls back to ``default`` — a sequence of enum
    *names* (plain strings), parsed through the same loop so a typo in a
    module-level default surfaces at rule-build time as the programming error
    it is, rather than quietly disabling a rule.

    Every remaining value must parse as ``enum_cls`` and, when ``choices`` is
    given, be one of them; otherwise it refuses, naming the accepted values.
    This is the one place the tool answers "this option accepts exactly these
    values": three sites used to answer it three different silent ways, and one
    of those silences is what let a project's ``[tool.pypeeker.visibility]``
    table empty ``require-docstrings``' visibility set.

    Deduped, in written order, as a tuple: the only consumer is
    :meth:`~pypeeker.dsl.Expr.is_in`, whose membership test is the same either
    way, and written order stays inspectable in a derivation's ``rhs``.
    """
    values = coerce_str_list(option, raw) or coerce_str_list(option, tuple(default))
    accepted = sorted(
        member.value for member in (choices if choices is not None else enum_cls)
    )
    expected = f"one of: {', '.join(accepted)}"
    out: list[E] = []
    for value in values:
        try:
            member = enum_cls(value)
        except ValueError as exc:
            raise ConfigOptionError(option, value, expected) from exc
        if choices is not None and member not in choices:
            raise ConfigOptionError(option, value, expected)
        if member not in out:
            out.append(member)
    return tuple(out)


def coerce_visibility_table(raw: Any) -> Mapping[str, Any]:
    """Check the shape of a raw ``[tool.pypeeker.visibility]`` table, or refuse.

    The one shape check both readers of that table share — this module's
    :func:`_parse_visibility_config` (the ``refactor`` path) and
    ``pypeeker.dsl.visibility._visibility_table`` (the rule path). They used to
    disagree: one silently defaulted a non-table and an unknown ``mode``, the
    other raised a bare ``TypeError`` on a non-table and read an unknown
    ``mode`` as app mode.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigOptionError(
            "visibility",
            raw,
            "a table",
            hint="[tool.pypeeker.visibility] is a TOML table",
        )
    for key in raw:
        if key not in VISIBILITY_KEYS:
            raise ConfigOptionError(
                f"visibility.{key}",
                key,
                f"one of: {', '.join(VISIBILITY_KEYS)}",
            )
    if "mode" in raw and raw["mode"] not in VISIBILITY_MODES:
        raise ConfigOptionError(
            "visibility.mode",
            raw["mode"],
            f"one of: {', '.join(VISIBILITY_MODES)}",
        )
    return raw


@dataclass(frozen=True)
class VisibilityConfig:
    """Parsed ``[tool.pypeeker.visibility]`` section.

    Visibility and dead-code rules are dangerous for libraries: external
    consumers are invisible to the index, so "nothing references this" is not
    evidence of dead code. This config lets a project declare that contract
    once instead of duplicating allow-lists per rule.

    Attributes:
        mode:             ``"app"`` (default — in-repo references are the
                          whole story) or ``"library"`` (barrel exports under
                          the public roots are sacred API).
        public_roots:     dotted package/module prefixes whose barrel-exported
                          names are public API. Empty means "use the default":
                          in library mode, every top-level package (so all
                          barrels are protected); in app mode, nothing.
        allow_decorators: global decorator-name fnmatch patterns marking
                          symbols as externally called; merged with each
                          rule's own ``allow-decorators`` option.
    """

    mode: str = "app"
    public_roots: tuple[str, ...] = ()
    allow_decorators: tuple[str, ...] = ()

    @property
    def is_library(self) -> bool:
        """True when the project declared ``mode = "library"``."""
        return self.mode == "library"

    def effective_public_roots(
        self, top_level_packages: Iterable[str]
    ) -> tuple[str, ...]:
        """The dotted prefixes whose barrel exports are protected.

        Explicit ``public_roots`` win; otherwise library mode defaults to the
        project's top-level packages (every barrel is then under a root, so
        all barrel exports are sacred — the safe default when consumers are
        invisible). App mode protects nothing.
        """
        if not self.is_library:
            return ()
        if self.public_roots:
            return self.public_roots
        return tuple(sorted(set(top_level_packages)))


def _parse_visibility_config(raw: Mapping[str, Any] | None) -> VisibilityConfig:
    """Shape a raw ``[tool.pypeeker.visibility]`` table into a config.

    A missing table still yields the defaults (app mode, no roots, no
    decorators), but a table that is *present and wrong* refuses: a non-table
    value, an unknown ``mode``, or a key outside :data:`VISIBILITY_KEYS`. The
    tolerance this used to advertise was indistinguishable from working — a
    ``public_roots`` typo left a library-mode project protecting nothing and
    said so nowhere.
    """
    raw = coerce_visibility_table(raw)
    return VisibilityConfig(
        mode=raw.get("mode", "app"),
        public_roots=coerce_str_list("visibility.public-roots", raw.get("public-roots")),
        allow_decorators=coerce_str_list(
            "visibility.allow-decorators", raw.get("allow-decorators")
        ),
    )


def load_visibility_config(project_root: Path) -> VisibilityConfig:
    """Read ``[tool.pypeeker.visibility]`` from ``project_root/pyproject.toml``.

    The section is passed through unfiltered: an ``isinstance`` guard here
    would swallow the non-table refusal on the ``refactor`` path, which is the
    only path that reaches this function.
    """
    return _parse_visibility_config(load_pypeeker_section(project_root).get("visibility"))


def load_pypeeker_section(project_root: Path) -> dict:
    """Read the raw ``[tool.pypeeker]`` table from ``project_root/pyproject.toml``.

    This is the single owner of ``[tool.pypeeker]`` access; other modules
    (e.g. ``dsl.config``) build their typed config on top of it. Returns
    ``{}`` when the file or section is absent or malformed.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return {}
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    section = data.get("tool", {}).get("pypeeker")
    if not isinstance(section, dict):
        return {}
    return section


def load_src_roots(project_root: Path) -> tuple[str, ...]:
    """Read ``[tool.pypeeker].src`` from ``project_root/pyproject.toml``.

    Returns the default ``("src",)`` when the file or section is absent — and,
    unlike :func:`pypeeker.dsl.config.read_config`, also when it is empty: the
    indexer has no "index nothing" mode to fall into. A bare string refuses
    rather than splitting into characters.
    """
    src = coerce_str_list(
        "src", load_pypeeker_section(project_root).get("src"), allow_scalar=False
    )
    if not src:
        return DEFAULT_SRC_ROOTS
    return src
