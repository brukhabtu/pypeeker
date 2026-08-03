"""The envl CLI: pass-through, envelope emission, exit-code propagation, caching."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from envl.cli import main

CORPUS = Path(__file__).parent / "fixtures" / "envelope"
MANIFEST = json.loads((CORPUS / "MANIFEST.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _envl_env(tmp_path, monkeypatch):
    """Keep every test in this module off the developer's real ~/.cache."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ENVL_CONFIG", raising=False)
    monkeypatch.delenv("ENVL_ENABLED", raising=False)
    monkeypatch.delenv("ENVL_THRESHOLD_BYTES", raising=False)


def _emit(path: Path) -> list[str]:
    """An argv that prints the given file verbatim, without a shell."""
    return [
        sys.executable,
        "-c",
        "import sys;sys.stdout.write(open(sys.argv[1],encoding='utf-8').read())",
        str(path),
    ]


def _fixture(fmt: str, *, largest: bool = True) -> dict:
    entries = [e for e in MANIFEST["fixtures"] if e["format"] == fmt]
    return sorted(entries, key=lambda e: e["original_bytes"], reverse=True)[
        0 if largest else -1
    ]


def _run(argv: list[str], *extra: str):
    return CliRunner().invoke(main, [*extra, "--", *argv], catch_exceptions=False)


def test_small_output_passes_through_untouched(tmp_path):
    result = _run([sys.executable, "-c", "print('hi there')"])
    assert result.exit_code == 0
    assert result.output == "hi there\n"
    assert not (tmp_path / "cache").exists(), "pass-through writes no blob"


def test_large_output_is_replaced_by_a_parseable_envelope(tmp_path):
    entry = _fixture("diff")
    result = _run(_emit(CORPUS / entry["path"]))
    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["envelope"] == "envl/1"
    assert envelope["format"] == "diff"
    assert envelope["bytes"] == entry["original_bytes"]
    assert len(result.output) < entry["original_bytes"]


def test_the_envelope_names_a_blob_holding_the_full_output(tmp_path):
    entry = _fixture("text")
    result = _run(_emit(CORPUS / entry["path"]))
    envelope = json.loads(result.output)
    blob = Path(envelope["blob"]["path"])
    assert blob.is_file()
    assert blob.read_bytes() == (CORPUS / entry["path"]).read_bytes()
    assert blob.name == entry["sha256"], "blobs are addressed by content"


def test_each_run_appends_a_manifest_line(tmp_path):
    entry = _fixture("search")
    argv = _emit(CORPUS / entry["path"])
    _run(argv)
    _run(argv)
    manifest = tmp_path / "cache" / "manifest.jsonl"
    records = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(records) == 2, "two runs are two captures, even when deduped"
    assert {r["sha256"] for r in records} == {entry["sha256"]}
    assert all(r["exit_code"] == 0 for r in records)
    assert all(r["format"] == "search" for r in records)


def test_a_re_run_executes_again_rather_than_serving_the_snapshot(tmp_path):
    """Serving a cached snapshot as live output would be a silent-wrong-data bug."""
    counter = tmp_path / "runs"
    counter.write_text("", encoding="utf-8")
    payload = tmp_path / "payload.txt"
    payload.write_text("x" * 5000, encoding="utf-8")
    argv = [
        sys.executable,
        "-c",
        "import sys;open(sys.argv[1],'a').write('.');"
        "sys.stdout.write(open(sys.argv[2],encoding='utf-8').read())",
        str(counter),
        str(payload),
    ]
    _run(argv)
    _run(argv)
    assert counter.read_text() == "..", "the wrapped command ran both times"


def test_exit_code_propagates_for_a_small_output(tmp_path):
    result = _run([sys.executable, "-c", "import sys;print('nope');sys.exit(3)"])
    assert result.exit_code == 3
    assert result.output == "nope\n"


def test_exit_code_propagates_alongside_an_envelope(tmp_path):
    entry = _fixture("pytest")
    result = _run(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.write(open(sys.argv[1],encoding='utf-8').read());"
            "sys.exit(1)",
            str(CORPUS / entry["path"]),
        ]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["exit_code"] == 1


def test_stderr_is_captured_into_the_envelope(tmp_path):
    result = _run(
        [
            sys.executable,
            "-c",
            "import sys;sys.stderr.write('E'*3000)",
        ]
    )
    envelope = json.loads(result.output)
    assert envelope["bytes"] == 3000


def test_format_flag_overrides_both_the_label_and_the_adapter(tmp_path):
    entry = _fixture("diff")
    argv = _emit(CORPUS / entry["path"])
    sniffed = json.loads(_run(argv).output)
    forced = json.loads(_run(argv, "--format", "text").output)
    assert sniffed["format"] == "diff"
    assert "totals" in sniffed["summary"]
    assert forced["format"] == "text"
    # The adapter itself changed, not just the label: a text summary has no
    # per-file diff stat and does report a line count.
    assert "totals" not in forced["summary"]
    assert forced["summary"]["lines"] == sniffed["lines"]


def test_the_kill_switch_disables_enveloping_entirely(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVL_ENABLED", "0")
    entry = _fixture("diff")
    result = _run(_emit(CORPUS / entry["path"]))
    assert result.exit_code == 0
    assert result.output == (CORPUS / entry["path"]).read_text(encoding="utf-8")
    assert not (tmp_path / "cache").exists()


def test_the_threshold_decides_between_raw_and_enveloped(tmp_path, monkeypatch):
    entry = _fixture("search", largest=False)
    argv = _emit(CORPUS / entry["path"])
    monkeypatch.setenv("ENVL_THRESHOLD_BYTES", str(entry["original_bytes"] + 1))
    assert _run(argv).output == (CORPUS / entry["path"]).read_text(encoding="utf-8")
    monkeypatch.setenv("ENVL_THRESHOLD_BYTES", str(entry["original_bytes"]))
    assert json.loads(_run(argv).output)["envelope"] == "envl/1"


def test_a_yaml_config_drives_the_command_registry(tmp_path):
    entry = _fixture("diff")
    config = tmp_path / "envl.yaml"
    config.write_text(
        "threshold_bytes: 100\n"
        f"cache:\n  dir: {tmp_path / 'cache'}\n"
        "defaults:\n  max_items: 2\n"
        "commands:\n"
        '  - {match: "*", format: text, max_lines: 3}\n',
        encoding="utf-8",
    )
    result = _run(_emit(CORPUS / entry["path"]), "--config", str(config))
    envelope = json.loads(result.output)
    assert envelope["format"] == "text", "the registry beats content sniffing"
    assert len(envelope["summary"]["head"]) == 3


def test_a_command_emitting_non_utf8_bytes_still_gets_an_envelope(tmp_path):
    """Transparency: a stray byte must not cost the output *and* the exit code.

    Before, `envl -- cat some.bin` printed a traceback, wrote nothing to
    stdout, and exited 1 for a command that exited 0 — silently breaking any
    `envl -- cmd && next` chain.
    """
    result = _run(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(b'\\xf2\\xf3'+b'A'*4000);sys.exit(0)",
        ]
    )

    assert result.exit_code == 0
    envelope = json.loads(result.output)
    assert envelope["envelope"] == "envl/1"
    assert Path(envelope["blob"]["path"]).is_file()


def test_the_blob_the_envelope_names_still_exists_after_the_prune(tmp_path):
    """The CLI prunes between storing and printing; the named blob is exempt."""
    payload = tmp_path / "payload.txt"
    payload.write_text("stable line\n" * 400, encoding="utf-8")
    config = tmp_path / "envl.yaml"
    config.write_text(
        f"cache:\n  dir: {tmp_path / 'cache'}\n  max_bytes: 0\n  ttl_days: 1\n",
        encoding="utf-8",
    )

    result = _run(_emit(payload), "--config", str(config))

    envelope = json.loads(result.output)
    blob = Path(envelope["blob"]["path"])
    assert blob.is_file(), "the envelope must not name a file it just deleted"
    assert blob.read_text(encoding="utf-8") == payload.read_text(encoding="utf-8")
    assert "FULL OUTPUT IS ON DISK AT" in envelope["TRUNCATED"]


def test_a_repo_relative_cache_dir_is_refused_before_the_command_runs(tmp_path):
    ran = tmp_path / "ran"
    config = tmp_path / "envl.yaml"
    config.write_text("cache:\n  dir: .envl-cache\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(config),
            "--",
            sys.executable,
            "-c",
            f"open({str(ran)!r}, 'w').write('x')",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "must be absolute and outside the repository" in result.output
    assert not ran.exists(), "a refused config must not run the command"
    assert not (Path.cwd() / ".envl-cache").exists()


def test_running_with_no_command_is_a_usage_error():
    result = CliRunner().invoke(main, [], catch_exceptions=False)
    assert result.exit_code == 2
    assert "usage: envl -- <command>" in result.output
