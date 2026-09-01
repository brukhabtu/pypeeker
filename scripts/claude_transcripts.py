"""Claude Code transcript conventions shared by the envelope scripts.

Two things live here because two scripts need them to agree exactly:

* :data:`COMMAND_FAMILIES` and :func:`command_family` bucket a shell command
  by the tool it actually runs. The table is the one the token baseline in
  ``.claude/workflows/TOKEN-COSTS.md`` was published with, and the skill that
  produced that baseline (``.claude/skills/measure-tool-costs/measure-tool-costs.py``)
  carries the same table verbatim so it stays runnable in any project on its
  own. ``tests/test_envl_replay_harness.py`` pins the two copies to each other;
  change one and the test says so. Re-deriving the rule in either place would
  make the replay projection's ``B_f`` and ``r_f`` describe different
  partitions of the same commands.
* :func:`default_transcript_dir` locates the workflow transcripts Claude Code
  wrote for *this* repository, so a no-argument run of the envelope scripts
  measures the project it is run from rather than a path captured from some
  other machine.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from pathlib import Path

# First match wins; ordered so specific runners precede the generic binaries
# they are invoked through.
COMMAND_FAMILIES: list[tuple[str, str]] = [
    (r"^uv run pytest", "uv run pytest"),
    (r"^uv run ruff", "uv run ruff"),
    (r"^uv run pypeeker", "uv run pypeeker"),
    (r"^uv run python", "uv run python"),
    (r"^uv sync", "uv sync"),
    (r"^\./scripts/verify", "verify-repo.sh"),
    (r"^\S*python3?\b", "python3"),
    (r"^git\b", "git"),
    (r"^(cat|head|tail)\b", "cat/head/tail"),
    (r"^(sed|awk)\b", "sed/awk"),
    (r"^(grep|rg)\b", "grep/rg"),
    (r"^(ls|find)\b", "ls/find"),
    (r"^backlog\b", "backlog"),
    (r"^(mkdir|cp|mv|rm|chmod|touch|echo|printf)\b", "file ops/echo"),
    (r"^(diff|wc|sort|uniq|jq)\b", "diff/wc/jq"),
]

# A leading `cd <path> &&` says nothing about what the command does; strip it so
# the same command in a worktree and in the primary checkout land in one family.
_CD_PREFIX = re.compile(r"^\(?cd [^&;|]+(&&|;)\s*")

_PROJECTS_DIR = "~/.claude/projects"
_PROJECT_DIR_UNSAFE = re.compile(r"[^A-Za-z0-9]")


def command_family(command: str) -> str:
    """Bucket a shell command by the tool it actually runs."""
    body = _CD_PREFIX.sub("", command.strip()).strip()
    for pattern, label in COMMAND_FAMILIES:
        if re.match(pattern, body):
            return label
    parts = body.split()
    return parts[0][:18] if parts else "?"


def project_dir_name(repo_root: Path) -> str:
    """Encode a checkout path the way Claude Code names its per-project directory.

    Every character outside ``[A-Za-z0-9]`` becomes ``-``, so
    ``/home/user/pypeeker`` is stored under ``-home-user-pypeeker``.
    """
    return _PROJECT_DIR_UNSAFE.sub("-", str(repo_root))


def main_checkout(repo_root: Path) -> Path:
    """Resolve the primary checkout for ``repo_root``, seeing through a git worktree.

    Claude Code keys its transcripts by the directory a session was started
    in, which for a linked worktree is the primary checkout, not the worktree.
    Fall back to ``repo_root`` itself when git is unavailable.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return repo_root
    return Path(common).parent if common else repo_root


def default_transcript_dir(repo_root: Path) -> str | None:
    """Return this repository's most recently active workflow-transcript directory.

    Claude Code writes subagent transcripts under
    ``~/.claude/projects/<project>/<session-uuid>/subagents/workflows``. The
    project component is derived from ``repo_root`` (see
    :func:`project_dir_name`); the session component changes every session,
    so the newest one by mtime is chosen. Return ``None`` when the project
    has no such directory at all — the caller should say so and exit
    non-zero rather than silently measure an empty baseline.
    """
    project = project_dir_name(main_checkout(repo_root))
    pattern = os.path.join(
        os.path.expanduser(_PROJECTS_DIR), project, "*", "subagents", "workflows"
    )
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)
