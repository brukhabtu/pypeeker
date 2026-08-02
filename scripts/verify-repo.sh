#!/bin/bash
# One-shot full gate for pypeeker: runs the three checks CLAUDE.md documents
# (test suite, lint, self-lint) and reports a compact PASS/FAIL per step plus
# a final summary. This is the canonical thing to run before calling a change
# done — see CLAUDE.md -> Commands.
#
# Intentionally NOT `set -e`: an early failure must not skip later steps, so
# every step's result is visible in one run instead of one-at-a-time.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

declare -a NAMES=()
declare -a RESULTS=()

run_step() {
  local name="$1"
  shift
  echo "==> ${name}"
  if "$@"; then
    NAMES+=("$name")
    RESULTS+=("PASS")
    echo "[PASS] ${name}"
  else
    NAMES+=("$name")
    RESULTS+=("FAIL")
    echo "[FAIL] ${name}"
  fi
  echo
}

run_pytest() {
  uv run pytest -q
}

run_ruff() {
  uv run ruff check src tests
}

run_self_lint() {
  uv run pypeeker index src && uv run pypeeker check
}

run_step "pytest" run_pytest
run_step "ruff" run_ruff
run_step "self-lint" run_self_lint

echo "==================== SUMMARY ===================="
overall="PASS"
for i in "${!NAMES[@]}"; do
  printf '%-10s %s\n' "${RESULTS[$i]}" "${NAMES[$i]}"
  if [ "${RESULTS[$i]}" != "PASS" ]; then
    overall="FAIL"
  fi
done
echo "==================================================="
echo "OVERALL: ${overall}"

if [ "$overall" != "PASS" ]; then
  exit 1
fi
