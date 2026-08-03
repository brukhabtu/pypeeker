"""envl blob cache: content addressing, append-only manifest, pruning, no stale serve."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from envl import BlobCache, Capture, run_command


@pytest.fixture(autouse=True)
def _envl_env(tmp_path, monkeypatch):
    """Keep every test in this module off the developer's real ~/.cache."""
    monkeypatch.setenv("ENVL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ENVL_CONFIG", raising=False)


def _capture(output: str, *, command: str = "echo hi", at: str = "2026-01-01T00:00:00Z"):
    return Capture(
        argv=("echo", "hi"),
        command=command,
        cwd="/repo",
        exit_code=0,
        output=output,
        captured_at=at,
    )


def test_identical_payloads_dedupe_to_one_blob(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    first = cache.store(_capture("same bytes"), "text")
    second = cache.store(_capture("same bytes", at="2026-01-02T00:00:00Z"), "text")

    assert first.digest == second.digest
    assert first.path == second.path
    assert first.deduped is False
    assert second.deduped is True
    blobs = [p for p in cache.blobs_dir.rglob("*") if p.is_file()]
    assert len(blobs) == 1
    assert blobs[0].read_text(encoding="utf-8") == "same bytes"


def test_blob_path_is_sharded_by_digest_prefix(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    record = cache.store(_capture("payload"), "text")
    assert record.path.name == record.digest
    assert record.path.parent.name == record.digest[:2]
    assert record.path.parent.parent == cache.blobs_dir


def test_manifest_is_append_only_and_records_the_capture(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    cache.store(_capture("same bytes"), "text")
    cache.store(_capture("same bytes", at="2026-01-02T00:00:00Z"), "json")

    lines = cache.manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert [r["captured_at"] for r in records] == [
        "2026-01-01T00:00:00Z",
        "2026-01-02T00:00:00Z",
    ]
    assert records[0]["command"] == "echo hi"
    assert records[0]["cwd"] == "/repo"
    assert records[0]["exit_code"] == 0
    assert records[0]["bytes"] == len(b"same bytes")
    assert records[0]["format"] == "text"
    assert records[1]["format"] == "json"
    assert records[0]["blob"] == records[1]["blob"]


def test_cache_root_is_outside_the_repository(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    record = cache.store(_capture("payload"), "text")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    assert not str(record.path).startswith(repo_root + os.sep)


def test_prune_drops_blobs_past_the_ttl(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    stale = cache.store(_capture("old payload"), "text")
    fresh = cache.store(_capture("new payload"), "text")
    old = time.time() - 30 * 86_400
    os.utime(stale.path, (old, old))

    removed = cache.prune(max_bytes=10**9, ttl_days=14)
    assert removed == 1
    assert not stale.path.exists()
    assert fresh.path.exists()
    assert cache.manifest_path.exists()


def test_prune_drops_oldest_first_until_under_max_bytes(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    oldest = cache.store(_capture("a" * 100), "text")
    middle = cache.store(_capture("b" * 100), "text")
    newest = cache.store(_capture("c" * 100), "text")
    now = time.time()
    for offset, record in enumerate((oldest, middle, newest)):
        stamp = now - (10 - offset) * 60
        os.utime(record.path, (stamp, stamp))

    removed = cache.prune(max_bytes=150, ttl_days=0)
    assert removed == 2
    assert not oldest.path.exists()
    assert not middle.path.exists()
    assert newest.path.exists()


def test_a_deduped_blob_is_kept_alive_by_being_used(tmp_path):
    """A stable command's blob must not expire while it is still being handed out.

    Dedupe used to leave the original mtime untouched, so a command whose
    output does not change — a passing `pytest -q`, a clean `pypeeker check` —
    aged out under the TTL and the very next run's `prune` deleted the blob
    that run's envelope was about to name.
    """
    cache = BlobCache(tmp_path / "cache")
    first = cache.store(_capture("stable output"), "text")
    old = time.time() - 40 * 86_400
    os.utime(first.path, (old, old))

    second = cache.store(_capture("stable output", at="2026-02-10T00:00:00Z"), "text")
    assert second.deduped is True
    assert cache.prune(max_bytes=10**9, ttl_days=14) == 0
    assert second.path.exists()
    assert second.path.read_text(encoding="utf-8") == "stable output"


def test_prune_never_evicts_the_digest_it_was_told_to_keep(tmp_path):
    """The envelope's own blob is exempt from both prune passes.

    An envelope shouting "FULL OUTPUT IS ON DISK AT <path>" for a file the same
    process just deleted is worse than an over-quota cache: the recipe fails
    and the dropped payload is gone.
    """
    cache = BlobCache(tmp_path / "cache")
    record = cache.store(_capture("k" * 500), "text")
    old = time.time() - 99 * 86_400
    os.utime(record.path, (old, old))

    removed = cache.prune(max_bytes=0, ttl_days=1, keep=record.digest)

    assert removed == 0
    assert record.path.exists()
    assert cache.prune(max_bytes=0, ttl_days=1) == 1, "unprotected, it does go"


def test_prune_never_touches_the_manifest(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    cache.store(_capture("payload"), "text")
    before = cache.manifest_path.read_bytes()
    cache.prune(max_bytes=0, ttl_days=0)
    assert cache.manifest_path.read_bytes() == before


def test_cache_has_no_lookup_by_command_api():
    # The absence of a read-by-command path is a correctness guarantee, not an
    # omission: serving a previous snapshot as live output would be silently
    # wrong the moment an agent edits a file.
    public = [name for name in dir(BlobCache) if not name.startswith("_")]
    assert public == ["blobs_dir", "manifest_path", "prune", "store"]


def test_rerunning_a_command_always_executes_it_again(tmp_path):
    counter = tmp_path / "runs.txt"
    counter.write_text("", encoding="utf-8")
    argv = [
        sys.executable,
        "-c",
        f"open({str(counter)!r}, 'a').write('x')\nprint('output line')",
    ]
    cache = BlobCache(tmp_path / "cache")

    first = run_command(argv)
    record_a = cache.store(first, "text")
    time.sleep(1.05)
    second = run_command(argv)
    record_b = cache.store(second, "text")

    assert counter.read_text(encoding="utf-8") == "xx"
    assert record_a.path == record_b.path
    assert record_b.deduped is True
    stamps = [
        json.loads(line)["captured_at"]
        for line in cache.manifest_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(stamps) == 2
    assert stamps[0] != stamps[1]


def test_run_command_records_non_zero_exit_without_raising(tmp_path):
    capture = run_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert capture.exit_code == 3


def test_run_command_combines_stdout_and_stderr(tmp_path):
    capture = run_command(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"]
    )
    assert capture.output == "out\nerr\n"


def test_non_utf8_output_is_replaced_rather_than_raising(tmp_path):
    """A wrapper must stay transparent when a command emits a stray byte.

    Strict decoding turned `git diff` over a latin-1 file, a `grep` across
    mixed encodings or a test dumping raw bytes into a UnicodeDecodeError that
    destroyed the output and substituted exit 1 for the command's real code.
    """
    capture = run_command(
        [
            sys.executable,
            "-c",
            "import sys;sys.stdout.buffer.write(b'\\xf2\\xf3'+b'A'*10);sys.exit(7)",
        ]
    )

    assert capture.exit_code == 7
    assert capture.output == "��" + "A" * 10
    # The whole point of `replace` over `surrogateescape`: the payload still
    # encodes, so the blob write and the size check downstream cannot raise.
    assert len(capture.output.encode("utf-8")) == 16


def test_a_non_utf8_capture_still_reaches_the_blob_verbatim_as_replaced(tmp_path):
    cache = BlobCache(tmp_path / "cache")
    capture = run_command(
        [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'\\x80'*3000)"]
    )
    record = cache.store(capture, "text")
    assert record.path.read_text(encoding="utf-8") == "�" * 3000
