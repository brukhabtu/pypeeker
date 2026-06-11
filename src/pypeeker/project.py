"""Project-level configuration shared across the tool.

Exposes the source roots declared under ``[tool.pypeeker]`` in
``pyproject.toml``. These were originally read only by the ``check`` command;
they're general project config now — the indexer needs them to map file paths
to dotted module paths.

Also owns the ``[tool.pypeeker.visibility]`` section
(:class:`VisibilityConfig`): project-wide knobs the visibility / dead-code
rules consume so library authors can declare their public API surface instead
of allow-listing rule by rule.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SRC_ROOTS: tuple[str, ...] = ("src",)

VISIBILITY_MODES: tuple[str, ...] = ("app", "library")
"""Valid values for ``[tool.pypeeker.visibility].mode``."""


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


def parse_visibility_config(raw: Mapping[str, Any] | None) -> VisibilityConfig:
    """Shape a raw ``[tool.pypeeker.visibility]`` table into a config.

    Tolerant like the rest of config loading: a missing table, an unknown
    ``mode``, or non-list values fall back to defaults (app mode, no roots,
    no decorators) rather than raising.
    """
    if not isinstance(raw, Mapping):
        return VisibilityConfig()
    mode = raw.get("mode")
    if mode not in VISIBILITY_MODES:
        mode = "app"
    return VisibilityConfig(
        mode=mode,
        public_roots=_as_str_tuple(raw.get("public-roots")),
        allow_decorators=_as_str_tuple(raw.get("allow-decorators")),
    )


def load_visibility_config(project_root: Path) -> VisibilityConfig:
    """Read ``[tool.pypeeker.visibility]`` from ``project_root/pyproject.toml``."""
    section = load_pypeeker_section(project_root).get("visibility")
    return parse_visibility_config(section if isinstance(section, dict) else None)


def coerce_visibility(value: Any) -> VisibilityConfig:
    """Coerce a rule-options value under the reserved ``visibility`` key.

    ``check.config.load_config`` injects the raw visibility table into each
    enabled rule's options; tests (and plugins) may pass either a raw mapping
    or an already-parsed :class:`VisibilityConfig`. Anything else means "no
    visibility config" and yields the defaults.
    """
    if isinstance(value, VisibilityConfig):
        return value
    return parse_visibility_config(value if isinstance(value, Mapping) else None)


def _as_str_tuple(raw: Any) -> tuple[str, ...]:
    """Coerce a TOML value to a tuple of strings ('' / None / [] -> ())."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw else ()
    return tuple(str(value) for value in raw)


def load_pypeeker_section(project_root: Path) -> dict:
    """Read the raw ``[tool.pypeeker]`` table from ``project_root/pyproject.toml``.

    This is the single owner of ``[tool.pypeeker]`` access; other modules
    (e.g. ``check.config``) build their typed config on top of it. Returns
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

    Returns the default ``("src",)`` when the file or section is absent.
    """
    src = load_pypeeker_section(project_root).get("src")
    if not src:
        return DEFAULT_SRC_ROOTS
    return tuple(src)
