#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Provisions the toolchain a fresh, ephemeral web container needs so that
# tests, linters, and task management work without manual setup:
#   1. Python 3.14 (pyproject requires >=3.14)
#   2. project dependencies (uv sync)
#   3. the Backlog.md CLI that CLAUDE.md mandates for ALL task operations
#
# Runs synchronously: the session waits until provisioning finishes, so the
# agent never races ahead of a half-installed toolchain.
set -euo pipefail

# Local checkouts already have this toolchain; only the ephemeral web
# container needs provisioning. Bail out everywhere else.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# 1. Python 3.14. uv's managed python-build-standalone download is blocked by
#    this environment's egress proxy (HTTP 403 from GitHub releases), so
#    install the system interpreter from the preconfigured deadsnakes apt
#    source and let uv discover it on PATH.
if ! command -v python3.14 >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends python3.14 python3.14-dev
fi

# 2. Project dependencies (builds .venv from uv.lock; tree-sitter needs a
#    C toolchain, already present in the base image).
uv sync

# 3. Backlog.md CLI. CLAUDE.md requires every task operation to go through it;
#    npm's global prefix (/opt/node22) is already on PATH.
if ! command -v backlog >/dev/null 2>&1; then
  npm install -g backlog.md
fi
