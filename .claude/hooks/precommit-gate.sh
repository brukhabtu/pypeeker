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

# `check --baseline` runs the FULL rule suite (all rules in [tool.pypeeker])
# but fails only on violations NOT in the committed baseline
# (.pypeeker/check-baseline.json) — so every rule is dogfooded and only NEW
# regressions block. Re-seed intentionally with `pypeeker check --update-baseline`.
out=$( { uv run pypeeker index src && uv run pypeeker check --baseline && uv run ruff check src tests; } 2>&1 )
status=$?

if [ "$status" -eq 0 ]; then
  exit 0
fi

reason=$(printf 'pypeeker pre-commit gate failed (exit %s) — commit blocked. Fix and retry:\n\n%s' "$status" "$out")
jq -n --arg r "$reason" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
