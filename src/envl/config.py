"""Configuration for the envelope: kill switch, threshold, cache limits, command registry.

This module is the ONLY place the YAML schema is written down. No ``.envl.yaml``
is committed to this repository on purpose: a repo-root config would be picked
up by every ``envl`` run whose cwd is the repo — including tests that ``chdir``
— making behaviour depend on a file the caller never wrote.

Schema (every key optional; the defaults below are the built-ins)::

    enabled: true                 # kill switch; false => never envelope
    threshold_bytes: 2048         # output smaller than this passes through
    cache:
      dir: ~/.cache/envl          # MUST be absolute and outside any repo
      max_bytes: 268435456        # 256 MiB of blobs
      ttl_days: 14
    defaults:
      max_items: 8                # array/list elements kept per summary field
      max_lines: 30               # excerpt lines kept
      max_line_chars: 400         # per-excerpt-line clip
      max_envelope_bytes: 4096    # hard ceiling on the serialized envelope
    commands:                     # FIRST MATCH WINS — order is meaningful
      - {match: "git diff*", format: diff, max_items: 6}
      - {match: "* pytest*", format: pytest, max_lines: 40}

``match`` is an :mod:`fnmatch` pattern tested against the joined command string
(``shlex.join(argv)``). ``format`` is one of ``json`` / ``diff`` / ``pytest`` /
``search`` / ``text`` and, when set, overrides content sniffing — registry
first, sniff second, because content alone is measurably wrong (a ``sed`` of a
TOML file opens with ``[project]`` and sniffs as JSON).

Discovery order for :func:`load_config`: explicit path, then ``$ENVL_CONFIG``,
then ``./.envl.yaml``, then ``$XDG_CONFIG_HOME/envl/config.yaml``, then
``~/.config/envl/config.yaml``, then the built-in defaults. Environment
overrides (``ENVL_ENABLED``, ``ENVL_CACHE_DIR``, ``ENVL_THRESHOLD_BYTES``) are
applied last and always win.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

_DEFAULT_THRESHOLD_BYTES = 2048
_DEFAULT_CACHE_MAX_BYTES = 268_435_456
_DEFAULT_CACHE_TTL_DAYS = 14
_DEFAULT_MAX_ITEMS = 8
_DEFAULT_MAX_LINES = 30
_DEFAULT_MAX_LINE_CHARS = 400
_DEFAULT_MAX_ENVELOPE_BYTES = 4096


def default_cache_dir() -> Path:
    """Return the blob cache root: ``$ENVL_CACHE_DIR``, else XDG, else ``~/.cache/envl``.

    Never repo-relative — see :class:`EnvelopeConfig`. A relative
    ``$ENVL_CACHE_DIR`` is rejected rather than resolved against the cwd.
    """
    override = os.environ.get("ENVL_CACHE_DIR")
    if override:
        return _resolve_cache_dir(Path(override), source="$ENVL_CACHE_DIR")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return _resolve_cache_dir(Path(xdg) / "envl", source="$XDG_CACHE_HOME")
    return Path.home() / ".cache" / "envl"


def _resolve_cache_dir(value: Path, *, source: str) -> Path:
    """Return ``value`` as an absolute cache root, or raise if it is repo-local.

    Two failures this rules out, both reported from the field:

    * A **relative** dir (``cache.dir: .envl-cache``, ``ENVL_CACHE_DIR=tmp``)
      resolves against whatever the cwd happens to be, so running ``envl`` from
      the repo creates the cache *inside* the repo and ``git status
      --porcelain`` grows an untracked entry — the envelope adding noise in
      order to remove noise. It also makes ``blob.path`` and every recipe
      relative, so the drill-in the envelope exists to enable reads the wrong
      file from any other directory.
    * An absolute dir **inside the enclosing git repository**, for the same
      reason. The enclosing repo is looked up from the cwd and the check is
      skipped when that repo contains ``$HOME`` (a dotfiles checkout must not
      make ``~/.cache/envl`` illegal).
    """
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise ValueError(
            f"envl cache dir must be absolute and outside the repository; "
            f"{source} is {str(value)!r}. A relative cache dir lands inside the "
            f"repo and makes every blob path unresolvable from another cwd."
        )
    resolved = Path(os.path.normpath(str(expanded)))
    repo = _enclosing_repo(Path.cwd())
    if repo is not None and _is_within(resolved, repo):
        raise ValueError(
            f"envl cache dir must be outside the repository; {source} is "
            f"{str(resolved)!r}, which is inside {str(repo)!r}. A repo-local "
            f"cache shows up in `git status --porcelain`."
        )
    return resolved


def _enclosing_repo(start: Path) -> Path | None:
    home = Path.home()
    for candidate in (start, *start.parents):
        if not (candidate / ".git").exists():
            continue
        if candidate == home or candidate in home.parents:
            # A dotfiles repo checked out at (or above) $HOME would otherwise
            # outlaw the default cache root.
            return None
        return candidate
    return None


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


@dataclass(frozen=True)
class CommandRule:
    """One entry of the command registry: a match pattern and its overrides.

    ``format`` and the truncation fields are ``None`` when the entry does not
    override the config-level defaults. :meth:`EnvelopeConfig.settings_for`
    returns a rule with every ``None`` already filled in, so summarizers never
    have to resolve inheritance themselves.
    """

    match: str = "*"
    format: str | None = None
    max_items: int | None = None
    max_lines: int | None = None
    max_line_chars: int | None = None


@dataclass(frozen=True)
class EnvelopeConfig:
    """Resolved envelope settings for one process.

    ``cache_dir`` is never repo-relative: a repo-local cache would show up in
    ``git status --porcelain``, which agents and hooks poll constantly, so the
    envelope would be adding noise in order to remove noise.
    """

    enabled: bool = True
    threshold_bytes: int = _DEFAULT_THRESHOLD_BYTES
    cache_dir: Path = field(default_factory=default_cache_dir)
    cache_max_bytes: int = _DEFAULT_CACHE_MAX_BYTES
    cache_ttl_days: int = _DEFAULT_CACHE_TTL_DAYS
    max_items: int = _DEFAULT_MAX_ITEMS
    max_lines: int = _DEFAULT_MAX_LINES
    max_line_chars: int = _DEFAULT_MAX_LINE_CHARS
    max_envelope_bytes: int = _DEFAULT_MAX_ENVELOPE_BYTES
    commands: tuple[CommandRule, ...] = ()

    def settings_for(self, command: str) -> CommandRule:
        """Return the first registry rule matching ``command``, defaults filled in.

        First match wins, so registry order is meaningful. When nothing
        matches, an all-defaults rule is returned rather than ``None`` — the
        caller always gets concrete truncation numbers.
        """
        matched = CommandRule()
        for rule in self.commands:
            if fnmatch.fnmatch(command, rule.match):
                matched = rule
                break
        return replace(
            matched,
            max_items=self.max_items if matched.max_items is None else matched.max_items,
            max_lines=self.max_lines if matched.max_lines is None else matched.max_lines,
            max_line_chars=(
                self.max_line_chars
                if matched.max_line_chars is None
                else matched.max_line_chars
            ),
        )


def load_config(path: Path | None = None) -> EnvelopeConfig:
    """Load envelope settings, applying environment overrides last.

    See the module docstring for the discovery order and the YAML schema. A
    missing file at every discovery step yields the built-in defaults; an
    explicitly supplied ``path`` that does not exist raises.
    """
    raw = _read_document(path)
    config = _config_from_document(raw)
    return _apply_env_overrides(config)


def _read_document(path: Path | None) -> Mapping[str, Any]:
    if path is not None:
        return _load_yaml(path)
    for candidate in _discovery_candidates():
        if candidate.is_file():
            return _load_yaml(candidate)
    return {}


def _discovery_candidates() -> list[Path]:
    candidates: list[Path] = []
    from_env = os.environ.get("ENVL_CONFIG")
    if from_env:
        candidates.append(Path(from_env).expanduser())
    candidates.append(Path.cwd() / ".envl.yaml")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg).expanduser() / "envl" / "config.yaml")
    candidates.append(Path.home() / ".config" / "envl" / "config.yaml")
    return candidates


def _load_yaml(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"envl config must be a YAML mapping: {path}")
    return loaded


def _config_from_document(raw: Mapping[str, Any]) -> EnvelopeConfig:
    cache = _sub_mapping(raw, "cache")
    defaults = _sub_mapping(raw, "defaults")
    cache_dir_value = cache.get("dir")
    cache_dir = (
        _resolve_cache_dir(Path(str(cache_dir_value)), source="cache.dir")
        if cache_dir_value is not None
        else default_cache_dir()
    )
    return EnvelopeConfig(
        enabled=bool(raw.get("enabled", True)),
        threshold_bytes=_as_int(raw.get("threshold_bytes"), _DEFAULT_THRESHOLD_BYTES),
        cache_dir=cache_dir,
        cache_max_bytes=_as_int(cache.get("max_bytes"), _DEFAULT_CACHE_MAX_BYTES),
        cache_ttl_days=_as_int(cache.get("ttl_days"), _DEFAULT_CACHE_TTL_DAYS),
        max_items=_as_int(defaults.get("max_items"), _DEFAULT_MAX_ITEMS),
        max_lines=_as_int(defaults.get("max_lines"), _DEFAULT_MAX_LINES),
        max_line_chars=_as_int(defaults.get("max_line_chars"), _DEFAULT_MAX_LINE_CHARS),
        max_envelope_bytes=_as_int(
            defaults.get("max_envelope_bytes"), _DEFAULT_MAX_ENVELOPE_BYTES
        ),
        commands=_rules_from_document(raw.get("commands")),
    )


def _rules_from_document(entries: Any) -> tuple[CommandRule, ...]:
    if not isinstance(entries, list):
        return ()
    rules: list[CommandRule] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        match = entry.get("match")
        if not match:
            continue
        rules.append(
            CommandRule(
                match=str(match),
                format=_as_optional_str(entry.get("format")),
                max_items=_as_optional_int(entry.get("max_items")),
                max_lines=_as_optional_int(entry.get("max_lines")),
                max_line_chars=_as_optional_int(entry.get("max_line_chars")),
            )
        )
    return tuple(rules)


def _apply_env_overrides(config: EnvelopeConfig) -> EnvelopeConfig:
    enabled = config.enabled
    raw_enabled = os.environ.get("ENVL_ENABLED")
    if raw_enabled is not None:
        enabled = raw_enabled.strip().lower() not in _FALSE_VALUES
    cache_dir = config.cache_dir
    raw_cache_dir = os.environ.get("ENVL_CACHE_DIR")
    if raw_cache_dir:
        cache_dir = _resolve_cache_dir(Path(raw_cache_dir), source="$ENVL_CACHE_DIR")
    threshold = config.threshold_bytes
    raw_threshold = os.environ.get("ENVL_THRESHOLD_BYTES")
    if raw_threshold:
        threshold = _as_int(raw_threshold, config.threshold_bytes)
    return replace(
        config, enabled=enabled, cache_dir=cache_dir, threshold_bytes=threshold
    )


def _sub_mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, Mapping) else {}


def _as_int(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
