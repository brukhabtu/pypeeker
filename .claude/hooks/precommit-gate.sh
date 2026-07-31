#!/usr/bin/env bash
# PreToolUse(Bash) gate: block `git commit` unless the pypeeker self-lint passes.
#
# Wired in .claude/settings.json under PreToolUse with `if: "Bash(git commit*)"`,
# so it only runs for commit commands. On failure it emits a PreToolUse "deny"
# decision carrying the gate output; on success it stays silent (= allow).
#
# Fail-open by design: if the repo can't be located the hook allows the commit
# rather than hard-blocking on an environment quirk.
set -uo pipefail

root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
cd "$root" 2>/dev/null || exit 0

# `check` runs the curated hard-gate rule set (see [tool.pypeeker].rules); it is
# clean on this codebase with no baseline, so any finding is a real regression.
out=$( { uv run pypeeker index src && uv run pypeeker check && uv run ruff check src tests; } 2>&1 )
status=$?

if [ "$status" -eq 0 ]; then
  exit 0
fi

reason=$(printf 'pypeeker pre-commit gate failed (exit %s) — commit blocked. Fix and retry:\n\n%s' "$status" "$out")
jq -n --arg r "$reason" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
