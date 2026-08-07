#!/usr/bin/env bash
# Single source of truth for "what does this project's CI actually check."
# Used by ci.yml (post-push, the real gate) AND implement.yml's pre-push
# validation step (best-effort, lets the implementer self-correct before
# ever pushing). Keeping one script instead of two copies of this logic
# means the pre-push check can never silently drift from what CI itself
# enforces - a pre-push check that doesn't match real CI is worse than
# none, since it would give false confidence.
#
# Exits 0 if there is nothing this generic step knows how to run yet (no
# pnpm workspace), 0 if every present script passed, 1 otherwise.
set -uo pipefail

if [ ! -f package.json ] || [ ! -f pnpm-lock.yaml ]; then
  echo "no pnpm workspace yet - nothing this generic CI step knows how to run"
  exit 0
fi

corepack enable
if ! pnpm install --frozen-lockfile; then
  echo "pnpm install --frozen-lockfile failed"
  exit 1
fi

failed=0

run_script_if_present() {
  local script="$1"
  if pnpm run 2>/dev/null | grep -qE "^\s+$script$"; then
    echo "== pnpm run $script =="
    if ! pnpm run "$script"; then
      failed=1
    fi
  else
    echo "== no '$script' script - skipping =="
  fi
}

run_script_if_present format:check
run_script_if_present lint
run_script_if_present typecheck
run_script_if_present test
run_script_if_present build

exit "$failed"
