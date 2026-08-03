#!/usr/bin/env bash
# PreToolUse(Bash) guard: block shell WRITE patterns that target the frozen
# oracle paths of the DSL rewrite (see dsl-rewrite.md -> "The freeze").
#
# The settings.json permission deny already blocks Edit/Write/NotebookEdit on
# these paths; this hook closes the common shell bypasses (in-place sed,
# redirection, rm/mv/cp/tee/touch). It is deliberately conservative: only
# commands that clearly WRITE to a frozen path are denied, so reads (grep,
# sed -n, cat) of frozen paths pass untouched. Best-effort and fail-open —
# the CI guard (scripts/check-frozen-paths.sh) is the authoritative backstop.
set -uo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0

# Frozen path fragments (any path form: absolute, relative, worktree).
frozen='(src/pypeeker/check/|src/pypeeker/app/check_fixes\.py|src/pypeeker/app/privatize\.py)'

deny=0
# In-place edit / delete / move / copy-onto / tee / touch / chmod with a frozen path as an argument.
if printf '%s' "$cmd" | grep -qE "(sed[[:space:]]+(-[a-zA-Z]*i|--in-place)|rm[[:space:]]|mv[[:space:]]|tee[[:space:]]|touch[[:space:]]|chmod[[:space:]]|truncate[[:space:]])[^|;&]*${frozen}"; then
  deny=1
fi
# Redirection INTO a frozen path (>, >>), with optional whitespace.
if printf '%s' "$cmd" | grep -qE ">[>]?[[:space:]]*[\"']?[^|;&[:space:]]*${frozen}"; then
  deny=1
fi
# cp with a frozen path as the DESTINATION (last arg heuristic: cp ... <frozen>).
if printf '%s' "$cmd" | grep -qE "cp[[:space:]][^|;&]*[[:space:]][\"']?[^|;&[:space:]]*${frozen}[^/]*[[:space:]]*($|[|;&])"; then
  deny=1
fi

if [ "$deny" -eq 1 ]; then
  jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"Frozen oracle path (DSL rewrite). src/pypeeker/check/**, app/check_fixes.py, and app/privatize.py are the executable spec the new engine is differentially graded against — they take no edits except a sanctioned oracle fix carrying a ledger entry in dsl-rewrite.md. See dsl-rewrite.md -> The freeze."}}'
fi
exit 0
