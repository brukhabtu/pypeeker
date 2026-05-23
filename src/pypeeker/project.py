"""Project-level configuration shared across the tool.

Currently exposes the source roots declared under ``[tool.pypeeker]`` in
``pyproject.toml``. These were originally read only by the ``check`` command;
they're general project config now — the indexer needs them to map file paths
to dotted module paths.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_SRC_ROOTS: tuple[str, ...] = ("src",)


def load_src_roots(project_root: Path) -> tuple[str, ...]:
    """Read ``[tool.pypeeker].src`` from ``project_root/pyproject.toml``.

    Returns the default ``("src",)`` when the file or section is absent.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return DEFAULT_SRC_ROOTS
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)
    section = data.get("tool", {}).get("pypeeker")
    if not isinstance(section, dict):
        return DEFAULT_SRC_ROOTS
    src = section.get("src")
    if not src:
        return DEFAULT_SRC_ROOTS
    return tuple(src)
