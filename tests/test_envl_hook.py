"""Tests for the PostToolUse envelope hook (.claude/hooks/envelope-post-tool-use.py).

The hook rewrites the tool result the model sees, so the properties worth
pinning are the ones that bound its blast radius rather than the ones that
demonstrate it works: what it refuses to touch, what it does when it breaks,
and whether the output it replaces is still recoverable afterwards.

Everything here is driven from the committed fixture corpus
(``tests/fixtures/envelope``) — real captured ``git`` and ``pytest`` output with
its real command strings — so "git is enveloped, pytest is not" is checked
against the same data the counterfactual measured, not against a hand-written
string that happens to match the glob.

The hook lives in ``.claude/hooks/`` and its filename is hyphenated, so it is
loaded by path exactly as ``test_envl_replay_harness.py`` loads the replay
harness out of ``scripts/``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from envl import load_config

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOK_PATH = _REPO_ROOT / ".claude" / "hooks" / "envelope-post-tool-use.py"
_SETTINGS_PATH = _REPO_ROOT / ".claude" / "settings.json"
_CORPUS = Path(__file__).resolve().parent / "fixtures" / "envelope"


def _load_hook():
    spec = importlib.util.spec_from_file_location("envelope_post_tool_use", _HOOK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["envelope_post_tool_use"] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook()

_MANIFEST = json.loads((_CORPUS / "MANIFEST.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Keep every run off the developer's real cache, config and kill switches."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    for name in ("ENVL_CONFIG", "ENVL_ENABLED", "ENVL_HOOK_ENABLED", "ENVL_THRESHOLD_BYTES"):
        monkeypatch.delenv(name, raising=False)


def _fixture_text(relative_path: str) -> str:
    return (_CORPUS / relative_path).read_text(encoding="utf-8", errors="replace")


def _corpus(*, family: str | None = None, min_bytes: int = 0) -> list[dict[str, Any]]:
    return [
        entry
        for entry in _MANIFEST["fixtures"]
        if (family is None or entry["command_family"] == family)
        and entry["original_bytes"] >= min_bytes
    ]


def _payload(command: str, output: str, **response: Any) -> dict[str, Any]:
    tool_response: dict[str, Any] = {
        "stdout": output,
        "stderr": "",
        "interrupted": False,
        "isImage": False,
    }
    tool_response.update(response)
    return {
        "session_id": "test-session",
        "hook_event_name": "PostToolUse",
        "cwd": str(_REPO_ROOT),
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "test"},
        "tool_response": tool_response,
    }


def _replacement_of(emitted: str) -> dict[str, Any]:
    """Unwrap the hook's stdout into the tool result the harness would install."""
    outer = json.loads(emitted)
    specific = outer["hookSpecificOutput"]
    assert specific["hookEventName"] == "PostToolUse"
    return specific["updatedToolOutput"]


def _envelope_of(emitted: str) -> dict[str, Any]:
    """Unwrap the hook's stdout into the envelope the model would receive."""
    return json.loads(_replacement_of(emitted)["stdout"])


def _blobs(tmp_path: Path) -> list[Path]:
    return sorted(p for p in (tmp_path / "cache" / "blobs").rglob("*") if p.is_file())


# --------------------------------------------------------------------------
# The allowlist: git in, everything else out
# --------------------------------------------------------------------------


def test_qualifying_git_output_is_replaced_by_an_envelope(tmp_path):
    entry = max(_corpus(family="git"), key=lambda e: e["original_bytes"])
    output = _fixture_text(entry["path"])

    emitted = hook.hook_output(_payload(entry["command"], output))

    envelope = _envelope_of(emitted)
    assert envelope["envelope"] == "envl/1"
    assert envelope["format"] == "diff"
    assert envelope["exit_code"] == 0
    # The point of the exercise: 28 KB of diff becomes at most the 4 KB ceiling.
    assert len(emitted) < len(output)
    assert len(json.dumps(envelope)) <= load_config(hook.CONFIG_PATH).max_envelope_bytes


def test_the_replacement_wears_the_bash_tools_own_output_shape(tmp_path):
    """A bare string is discarded by the harness, so the shape is the contract.

    ``updatedToolOutput`` is fed to the Bash tool's result mapper, which
    destructures ``{interrupted, stdout, stderr, isImage, ...}`` and calls
    ``.trim()`` on ``stderr``. Given a string that throws, and the harness keeps
    the original output and logs ``hook_error_during_execution`` — the hook
    would envelope nothing while still paying for a blob on every git call.
    """
    entry = max(_corpus(family="git"), key=lambda e: e["original_bytes"])
    payload = _payload(entry["command"], _fixture_text(entry["path"]))

    replacement = _replacement_of(hook.hook_output(payload))

    assert isinstance(replacement, dict), "a string makes the mapper's stderr undefined"
    assert set(replacement) >= {"stdout", "stderr", "interrupted", "isImage"}
    assert replacement["stderr"] == ""
    assert replacement["interrupted"] is False
    assert replacement["isImage"] is False
    assert json.loads(replacement["stdout"])["envelope"] == "envl/1"


def test_the_replacement_carries_the_rest_of_the_response_through(tmp_path):
    """The envelope replaces the output, not the result: other fields survive."""
    entry = max(_corpus(family="git"), key=lambda e: e["original_bytes"])
    payload = _payload(
        entry["command"], _fixture_text(entry["path"]), backgroundTaskId="bg-7"
    )

    replacement = _replacement_of(hook.hook_output(payload))

    assert replacement["backgroundTaskId"] == "bg-7"


def test_the_envelope_keeps_the_full_output_drillable_on_disk(tmp_path):
    entry = max(_corpus(family="git"), key=lambda e: e["original_bytes"])
    output = _fixture_text(entry["path"])

    envelope = _envelope_of(hook.hook_output(_payload(entry["command"], output)))

    blob = Path(envelope["blob"]["path"])
    assert blob.is_file(), "the envelope names a blob that must already exist"
    assert blob.read_bytes().decode("utf-8") == output
    assert envelope["blob"]["bytes"] == len(output.encode("utf-8"))
    assert envelope["bytes"] == len(output.encode("utf-8"))


def test_every_committed_git_fixture_is_enveloped_and_recoverable(tmp_path):
    """Corpus-wide: no captured git output slips through, and none loses bytes."""
    entries = _corpus(family="git")
    assert entries, "the corpus must carry git fixtures for this test to mean anything"
    for entry in entries:
        output = _fixture_text(entry["path"])
        emitted = hook.hook_output(_payload(entry["command"], output))
        assert emitted, f"git fixture passed through unwrapped: {entry['path']}"
        envelope = _envelope_of(emitted)
        assert Path(envelope["blob"]["path"]).read_bytes().decode("utf-8") == output


def test_pytest_output_is_untouched_even_far_above_the_threshold(tmp_path):
    """r = 1.13 makes pytest a net loss; the exclusion is explicit, not incidental."""
    entries = [e for e in _MANIFEST["fixtures"] if "pytest" in e["command"]]
    above = [e for e in entries if e["original_bytes"] >= 4096]
    assert above, "need an above-threshold pytest fixture to prove exclusion beats size"
    for entry in entries:
        output = _fixture_text(entry["path"])
        assert hook.hook_output(_payload(entry["command"], output)) == ""
    assert _blobs(tmp_path) == [], "an excluded command must not even write a blob"


def test_a_pipeline_ending_in_pytest_is_excluded_despite_starting_with_git(tmp_path):
    """First-match-wins: the exclusion is listed before the git allow rules."""
    output = _fixture_text(_corpus(family="git")[0]["path"])

    assert hook.hook_output(_payload("git diff && uv run pytest -q", output)) == ""


def test_unregistered_commands_pass_through_untouched(tmp_path):
    """Allowlist, not filter: anything not in the registry is not this hook's business."""
    fat = _fixture_text(max(_corpus(family="git"), key=lambda e: e["original_bytes"])["path"])
    for command in (
        "rg -n 'def load_config' src",
        "sed -n '1,400p' src/envl/config.py",
        "uv run pypeeker check",
        "cat architecture.md",
        "legit --not-git",
        "gitk --all",
    ):
        assert hook.hook_output(_payload(command, fat)) == "", command
    assert _blobs(tmp_path) == []


def test_a_command_that_only_mentions_git_in_an_argument_is_not_enveloped(tmp_path):
    """The allowlist is anchored on the executed program, not on the string.

    grep/rg is a measured net loss (r = 0.81) that the counterfactual excluded
    on purpose, so a `* git *` reading of the registry — which matches any
    command with the word git in an argument — would widen the blast radius past
    what the design constraint bounds.
    """
    fat = _fixture_text(max(_corpus(family="git"), key=lambda e: e["original_bytes"])["path"])
    for command in (
        "grep -rn ' git ' CLAUDE.md",
        'rg -n "run git status" src',
        "echo 'git diff is the command' > note.txt",
        "sed -n '1,900p' .git/config",
        "legit --not-git commit",
        'grep -rn "x && git y" src',
    ):
        assert hook.hook_output(_payload(command, fat)) == "", command
    assert _blobs(tmp_path) == []


def test_git_is_matched_after_a_cd_prefix_as_the_corpus_records_it():
    """The captured commands are `cd <dir> && git diff ... | head -200`, not bare git."""
    config = load_config(hook.CONFIG_PATH)
    prefixed = [e for e in _corpus(family="git") if not e["command"].startswith("git ")]
    assert prefixed, "the corpus should contain a prefixed git command"
    for entry in prefixed:
        assert hook.allowed_rule(entry["command"], config) is not None, entry["command"]


@pytest.mark.parametrize(
    "command,expected",
    [
        ("git diff", ("git diff",)),
        ("cd /repo && git diff --cached | head -200", ("cd /repo", "git diff --cached", "head -200")),
        ("git log; git status", ("git log", "git status")),
        ("grep -rn 'a && git b' f", ("grep -rn 'a && git b' f",)),
        ('rg -n "x | git y" src', ('rg -n "x | git y" src',)),
        ("echo $(git log)", ("echo $(git log)",)),
        ("git diff 2>&1 > out.txt", ("git diff 2>", "1 > out.txt")),
    ],
)
def test_executed_segments_splits_on_separators_outside_quotes(command, expected):
    assert hook.executed_segments(command) == expected


# --------------------------------------------------------------------------
# The threshold
# --------------------------------------------------------------------------


def test_output_below_the_threshold_passes_through_untouched(tmp_path):
    threshold = load_config(hook.CONFIG_PATH).threshold_bytes
    small = "diff --git a/x b/x\n" + "+line\n" * 10
    assert len(small.encode("utf-8")) < threshold

    assert hook.hook_output(_payload("git diff", small)) == ""
    assert _blobs(tmp_path) == [], "sub-threshold output must not be cached either"


def test_the_threshold_boundary_is_exactly_where_the_envelope_starts(tmp_path):
    threshold = load_config(hook.CONFIG_PATH).threshold_bytes
    assert threshold == 4096, "the measured net-loss band ends at 4 KB, not 2 KB"
    below = "d" * (threshold - 1)
    at = "d" * threshold

    assert hook.hook_output(_payload("git show HEAD", below)) == ""
    assert hook.hook_output(_payload("git show HEAD", at)) != ""


# --------------------------------------------------------------------------
# Fail-open
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tool_name": "Bash"},
        {"tool_name": "Bash", "tool_input": {}, "tool_response": {}},
        {"tool_name": "Bash", "tool_input": None, "tool_response": None},
        {"tool_name": "Bash", "tool_input": {"command": "git diff"}},
        {"tool_name": "Bash", "tool_input": {"command": 17}, "tool_response": "x" * 9000},
        {"tool_name": "Read", "tool_input": {"command": "git diff"}},
        {"tool_name": "Bash", "tool_input": {"command": "git diff"}, "tool_response": []},
    ],
)
def test_malformed_payloads_emit_nothing(payload, tmp_path):
    assert hook.hook_output(payload) == ""
    assert _blobs(tmp_path) == []


def test_an_internal_failure_passes_the_output_through(monkeypatch, tmp_path):
    """A bug anywhere inside must degrade to pass-through, never to a raised error."""

    def _explode(*_args, **_kwargs):
        raise RuntimeError("blob cache is on fire")

    monkeypatch.setattr("envl.BlobCache.store", _explode)
    output = _fixture_text(_corpus(family="git")[0]["path"])

    assert hook.hook_output(_payload("git diff --cached", output)) == ""


def test_main_returns_zero_and_writes_nothing_for_unparseable_stdin():
    stdout = io.StringIO()

    assert hook.main(io.StringIO("not json at all {{{"), stdout) == 0
    assert stdout.getvalue() == ""


def test_main_returns_zero_and_writes_nothing_for_a_json_scalar():
    stdout = io.StringIO()

    assert hook.main(io.StringIO('"a bare string"'), stdout) == 0
    assert stdout.getvalue() == ""


def test_failing_command_output_is_never_replaced(tmp_path):
    """The failing path is the one a reader needs whole — and the one whose exit
    code this hook cannot establish, so it declines rather than stamp a zero."""
    output = _fixture_text(_corpus(family="git")[0]["path"])
    for signal in (
        {"is_error": True},
        {"isError": True},
        {"interrupted": True},
        {"exit_code": 1},
        {"returnCode": 128},
    ):
        assert hook.hook_output(_payload("git diff --cached", output, **signal)) == "", signal
    assert _blobs(tmp_path) == []


def test_a_string_tool_response_is_a_failure_and_is_never_replaced(tmp_path):
    """The shape this harness actually uses for a failed Bash call.

    Successful results arrive as a mapping; failures arrive as a plain string
    beginning ``Error: Exit code N``. Enveloping one would hand the model a
    structured claim that a command which exited 1 exited 0, with the error text
    demoted into a head excerpt — the exact fabrication the module docstring
    promises never to make.
    """
    output = _fixture_text(_corpus(family="git")[0]["path"])
    for response in (
        "Error: Exit code 1\n" + output,
        "Error: pypeeker pre-commit gate failed (exit 1)\n" + output,
    ):
        payload = _payload("cd /repo && git stash pop", "")
        payload["tool_response"] = response
        assert hook.hook_output(payload) == "", response[:40]
    assert _blobs(tmp_path) == []


def test_a_string_response_is_classified_as_a_failure_and_yields_no_output():
    """The two predicates must agree about the string shape, or the guard leaks."""
    assert hook.is_failure("Error: Exit code 1\ndiff --git a/x b/x\n") is True
    assert hook.response_output("Error: Exit code 1\ndiff --git a/x b/x\n") == ""


def test_a_zero_exit_code_is_not_mistaken_for_a_failure(tmp_path):
    output = _fixture_text(_corpus(family="git")[0]["path"])

    assert hook.hook_output(_payload("git diff --cached", output, exit_code=0)) != ""


# --------------------------------------------------------------------------
# Kill switches
# --------------------------------------------------------------------------


def test_the_hook_only_kill_switch_disables_enveloping(monkeypatch, tmp_path):
    output = _fixture_text(_corpus(family="git")[0]["path"])
    monkeypatch.setenv("ENVL_HOOK_ENABLED", "0")

    assert hook.hook_output(_payload("git diff --cached", output)) == ""
    assert _blobs(tmp_path) == []


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_every_falsey_spelling_of_the_hook_kill_switch_is_honoured(value, monkeypatch):
    monkeypatch.setenv("ENVL_HOOK_ENABLED", value)

    assert hook.hook_enabled() is False


def test_the_shared_env_kill_switch_disables_enveloping(monkeypatch, tmp_path):
    """``ENVL_ENABLED`` is the library's switch; the hook honours it through load_config."""
    output = _fixture_text(_corpus(family="git")[0]["path"])
    monkeypatch.setenv("ENVL_ENABLED", "0")

    assert hook.hook_output(_payload("git diff --cached", output)) == ""


def test_the_yaml_kill_switch_disables_enveloping(monkeypatch, tmp_path):
    config = tmp_path / "off.yaml"
    config.write_text(
        'enabled: false\nthreshold_bytes: 4096\ncommands:\n  - {match: "git *"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ENVL_CONFIG", str(config))
    output = _fixture_text(_corpus(family="git")[0]["path"])

    assert hook.hook_output(_payload("git diff --cached", output)) == ""


def test_envl_config_overrides_the_committed_hook_config(monkeypatch, tmp_path):
    """A caller who pointed envl at a config meant it for the hook too."""
    config = tmp_path / "custom.yaml"
    config.write_text(
        'threshold_bytes: 100\ncommands:\n  - {match: "backlog *"}\n', encoding="utf-8"
    )
    monkeypatch.setenv("ENVL_CONFIG", str(config))

    # git is not in the overriding registry, so it is no longer allowlisted...
    assert hook.hook_output(_payload("git diff --cached", "x" * 9000)) == ""
    # ...and the command that is, is.
    assert hook.hook_output(_payload("cd /repo && backlog task list", "x" * 9000)) != ""


# --------------------------------------------------------------------------
# The committed config and its wiring
# --------------------------------------------------------------------------


def test_the_committed_config_allowlists_git_and_excludes_pytest():
    config = load_config(hook.CONFIG_PATH)

    assert config.enabled is True
    assert config.threshold_bytes == 4096
    for allowed in (
        "git diff --cached",
        "cd /repo && git log --oneline -20",
        "git status --porcelain | head -50",
    ):
        assert hook.allowed_rule(allowed, config) is not None, allowed
    for excluded in ("uv run pytest -q", "pytest tests/test_envl_hook.py", "git diff && uv run pytest"):
        rule = config.matching_rule(excluded)
        assert rule is not None and rule.envelope is False, excluded
        assert hook.allowed_rule(excluded, config) is None, excluded
    for unregistered in (
        "rg -n foo src",
        "uv run pypeeker check",
        "sed -n 1,20p a.py",
        "grep -rn ' git ' CLAUDE.md",
    ):
        assert hook.allowed_rule(unregistered, config) is None, unregistered


def test_every_allow_rule_in_the_committed_config_is_program_anchored():
    """A leading-wildcard rule can match an argument, so it can only exclude."""
    config = load_config(hook.CONFIG_PATH)

    for rule in config.commands:
        if rule.envelope is False:
            continue
        assert not rule.match.startswith("*"), (
            f"{rule.match!r} would allow any command merely mentioning it in an argument"
        )


def test_the_committed_config_forces_no_format_override():
    """r = 0.17 was measured with content sniffing; a forced format is untested tuning."""
    config = load_config(hook.CONFIG_PATH)

    assert config.settings_for("git log --stat").format is None


def test_settings_json_wiring_is_valid_when_present(tmp_path):
    """The hook ships INERT: activating it means adding a PostToolUse entry to
    .claude/settings.json — an output-rewriting change to the agent harness
    that is reserved for an explicit human edit. This test tolerates the
    entry's absence, but if someone has wired it, the wiring must be correct.
    """
    settings = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))

    entries = settings.get("hooks", {}).get("PostToolUse", [])
    matching = [e for e in entries if e.get("matcher") == "Bash"]
    if not matching:
        return  # inert by design; nothing to validate yet
    assert len(matching) == 1
    commands = [h["command"] for h in matching[0]["hooks"]]
    assert any("envelope-post-tool-use.py" in c for c in commands)
    assert all(h.get("timeout", 0) > 0 for h in matching[0]["hooks"]), (
        "a hook without a timeout can hang a tool call; on timeout the output "
        "is left untouched, which is the fail-open path"
    )


def test_the_hook_runs_end_to_end_as_the_script_settings_json_invokes(tmp_path):
    """The wired entrypoint, exercised as a real process over real stdin."""
    entry = max(_corpus(family="git"), key=lambda e: e["original_bytes"])
    payload = _payload(entry["command"], _fixture_text(entry["path"]))

    completed = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT),
        env={**_script_env(tmp_path)},
    )

    assert completed.returncode == 0
    envelope = _envelope_of(completed.stdout)
    assert envelope["envelope"] == "envl/1"
    assert Path(envelope["blob"]["path"]).is_file()


def test_the_hook_exits_zero_on_garbage_stdin_as_a_real_process(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input="}{ not json",
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT),
        env={**_script_env(tmp_path)},
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def _script_env(tmp_path: Path) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["ENVL_CACHE_DIR"] = str(tmp_path / "cache")
    for name in ("ENVL_CONFIG", "ENVL_ENABLED", "ENVL_HOOK_ENABLED"):
        env.pop(name, None)
    return env
