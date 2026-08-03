#!/usr/bin/env bash
# CI guard for the DSL rewrite's frozen oracle paths (dsl-rewrite.md -> "The freeze").
#
# Fails when a change set touches a frozen path WITHOUT also touching
# dsl-rewrite.md (a sanctioned oracle fix must carry a ledger entry there).
# This is the authoritative enforcement layer; the Claude settings deny and
# the bash guard hook are in-session friction on top of it.
#
# Usage: check-frozen-paths.sh <base-ref>
#   e.g. check-frozen-paths.sh origin/main   (CI passes the PR base)
set -uo pipefail

base="${1:-origin/main}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

changed=$(git diff --name-only "${base}...HEAD" 2>/dev/null) || {
  echo "check-frozen-paths: cannot diff against ${base}; skipping (fail-open)" >&2
  exit 0
}

frozen_hits=$(printf '%s\n' "$changed" | grep -E '^(src/pypeeker/check/|src/pypeeker/app/check_fixes\.py$|src/pypeeker/app/privatize\.py$)' || true)

if [ -z "$frozen_hits" ]; then
  echo "check-frozen-paths: PASS (no frozen paths touched)"
  exit 0
fi

if printf '%s\n' "$changed" | grep -qx 'dsl-rewrite.md'; then
  echo "check-frozen-paths: PASS with sanctioned exception (dsl-rewrite.md ledger updated alongside):"
  printf '  %s\n' $frozen_hits
  exit 0
fi

echo "check-frozen-paths: FAIL — frozen oracle paths modified without a ledger entry:" >&2
printf '  %s\n' $frozen_hits >&2
echo "These files are the executable spec for the DSL rewrite's differential oracle." >&2
echo "A sanctioned oracle fix must update the ledger in dsl-rewrite.md in the same PR." >&2
exit 1
