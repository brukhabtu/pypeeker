"""envl config: YAML loading, discovery, env overrides, registry match order."""

from __future__ import annotations

from pathlib import Path

import pytest

from envl import CommandRule, EnvelopeConfig, default_cache_dir, load_config


@pytest.fixture(autouse=True)
def _envl_env(tmp_path, monkeypatch):
    """Keep every test in this module off the developer's real ~/.cache."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ENVL_CONFIG", raising=False)
    monkeypatch.delenv("ENVL_ENABLED", raising=False)
    monkeypatch.delenv("ENVL_THRESHOLD_BYTES", raising=False)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.enabled is True
    assert config.threshold_bytes == 2048
    assert config.max_items == 8
    assert config.max_lines == 30
    assert config.max_line_chars == 400
    assert config.max_envelope_bytes == 4096
    assert config.cache_max_bytes == 268_435_456
    assert config.cache_ttl_days == 14
    assert config.commands == ()


def test_yaml_controls_switch_threshold_cache_and_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("ENVL_CACHE_DIR", raising=False)
    path = _write(
        tmp_path / "envl.yaml",
        "enabled: false\n"
        "threshold_bytes: 999\n"
        "cache:\n"
        "  dir: /var/tmp/envl-blobs\n"
        "  max_bytes: 4096\n"
        "  ttl_days: 3\n"
        "defaults:\n"
        "  max_items: 2\n"
        "  max_lines: 4\n"
        "  max_line_chars: 11\n"
        "  max_envelope_bytes: 512\n"
        "commands:\n"
        '  - {match: "git diff*", format: diff, max_items: 6}\n'
        '  - {match: "* pytest*", format: pytest, max_lines: 40}\n',
    )
    config = load_config(path)
    assert config.enabled is False
    assert config.threshold_bytes == 999
    assert config.cache_dir == Path("/var/tmp/envl-blobs")
    assert config.cache_max_bytes == 4096
    assert config.cache_ttl_days == 3
    assert config.max_envelope_bytes == 512
    assert config.commands == (
        CommandRule(match="git diff*", format="diff", max_items=6),
        CommandRule(match="* pytest*", format="pytest", max_lines=40),
    )


def test_settings_for_first_match_wins_and_fills_defaults(tmp_path):
    path = _write(
        tmp_path / "envl.yaml",
        "defaults:\n  max_items: 8\n  max_lines: 30\n  max_line_chars: 400\n"
        "commands:\n"
        '  - {match: "git diff*", format: diff, max_items: 6}\n'
        '  - {match: "git*", format: text, max_items: 1}\n',
    )
    config = load_config(path)
    rule = config.settings_for("git diff --cached")
    assert rule.format == "diff"
    assert rule.max_items == 6
    assert rule.max_lines == 30
    assert rule.max_line_chars == 400

    other = config.settings_for("git status --porcelain")
    assert other.format == "text"
    assert other.max_items == 1


def test_settings_for_unmatched_command_returns_all_defaults(tmp_path):
    path = _write(
        tmp_path / "envl.yaml",
        'defaults:\n  max_items: 3\ncommands:\n  - {match: "git*", format: diff}\n',
    )
    rule = load_config(path).settings_for("uv run pytest -q")
    assert rule.format is None
    assert rule.max_items == 3


def test_env_overrides_win_over_yaml(tmp_path, monkeypatch):
    path = _write(
        tmp_path / "envl.yaml",
        "enabled: true\nthreshold_bytes: 100\ncache:\n  dir: /var/tmp/from-yaml\n",
    )
    monkeypatch.setenv("ENVL_ENABLED", "false")
    monkeypatch.setenv("ENVL_THRESHOLD_BYTES", "77")
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "from-env"))
    config = load_config(path)
    assert config.enabled is False
    assert config.threshold_bytes == 77
    assert config.cache_dir == tmp_path / "from-env"


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_kill_switch_false_spellings(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVL_ENABLED", value)
    assert load_config().enabled is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_kill_switch_true_spellings(tmp_path, monkeypatch, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVL_ENABLED", value)
    assert load_config().enabled is True


def test_repo_local_dot_envl_yaml_is_discovered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / ".envl.yaml", "threshold_bytes: 12345\n")
    assert load_config().threshold_bytes == 12345


def test_envl_config_env_var_is_discovered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path / "elsewhere.yaml", "threshold_bytes: 4242\n")
    monkeypatch.setenv("ENVL_CONFIG", str(path))
    assert load_config().threshold_bytes == 4242


def test_default_cache_dir_is_never_repo_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENVL_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    resolved = default_cache_dir()
    assert resolved.is_absolute()
    assert resolved == Path.home() / ".cache" / "envl"
    assert tmp_path not in resolved.parents


def test_a_relative_yaml_cache_dir_is_rejected(tmp_path, monkeypatch):
    """A relative dir lands the cache inside the repo and breaks every recipe.

    `cache: {dir: .envl-cache}` used to create `.envl-cache/` in whatever
    directory `envl` was invoked from — showing up in `git status --porcelain`,
    which agents and the stop hook poll constantly — and to put a cwd-relative
    path in `blob.path` and in every drill-in recipe.
    """
    monkeypatch.delenv("ENVL_CACHE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path / "envl.yaml", "cache:\n  dir: .envl-cache\n")

    with pytest.raises(ValueError, match="must be absolute and outside the repository"):
        load_config(path)

    assert not (tmp_path / ".envl-cache").exists()


def test_a_relative_env_cache_dir_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVL_CACHE_DIR", "envlcache-rel")
    with pytest.raises(ValueError, match=r"\$ENVL_CACHE_DIR is 'envlcache-rel'"):
        load_config()


def test_an_absolute_cache_dir_inside_the_repository_is_rejected(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    monkeypatch.chdir(repo / "src")
    monkeypatch.setenv("ENVL_CACHE_DIR", str(repo / ".envl-cache"))

    with pytest.raises(ValueError, match="must be outside the repository"):
        load_config()


def test_an_absolute_cache_dir_outside_the_repository_is_accepted(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "blobs"))

    assert load_config().cache_dir == tmp_path / "blobs"


def test_default_cache_dir_honours_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("ENVL_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert default_cache_dir() == tmp_path / "xdg" / "envl"


def test_non_mapping_yaml_is_rejected(tmp_path):
    path = _write(tmp_path / "bad.yaml", "- one\n- two\n")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_config(path)


def test_empty_yaml_yields_defaults(tmp_path):
    path = _write(tmp_path / "empty.yaml", "")
    assert load_config(path) == EnvelopeConfig(cache_dir=load_config(path).cache_dir)
