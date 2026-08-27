#!/usr/bin/env bash
# Single source of truth for "what does this project's CI actually check."
# Used by ci.yml (post-push, the real gate) AND implement.yml's pre-push
# validation step (best-effort, lets the implementer self-correct before
# ever pushing). Keeping one script instead of two copies of this logic
# means the pre-push check can never silently drift from what CI itself
# enforces - a pre-push check that doesn't match real CI is worse than
# none, since it would give false confidence.
#
# A pull-request validation caller may supply an exact base/head pair. This
# lets repository tests distinguish an immutable PR comparison from a local
# checkout without fetching otherwise-unreachable evidence commits. The
# benchmark fixture is the only caller-specific path inspected here; repos
# without it simply receive the harmless exact PR context variables.
#
# Exits 0 if there is nothing this generic step knows how to run yet (no
# pnpm workspace), 0 if every present script passed, 1 otherwise.
set -uo pipefail

usage() {
  echo "usage: $0 [--pr-base-sha SHA --pr-head-sha SHA | --squash-safe-push]" >&2
}

validation_mode="local"
validation_base_sha=""
validation_head_sha=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pr-base-sha)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      validation_base_sha="$2"
      shift 2
      ;;
    --pr-head-sha)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      validation_head_sha="$2"
      shift 2
      ;;
    --squash-safe-push)
      validation_mode="squash-safe-push"
      shift
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [ -n "$validation_base_sha" ] || [ -n "$validation_head_sha" ]; then
  if [ "$validation_mode" != "local" ] ||
     ! [[ "$validation_base_sha" =~ ^[0-9a-f]{40}$ ]] ||
     ! [[ "$validation_head_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "invalid or conflicting exact PR validation context" >&2
    exit 2
  fi
  if ! git cat-file -e "${validation_base_sha}^{commit}" 2>/dev/null ||
     ! git cat-file -e "${validation_head_sha}^{commit}" 2>/dev/null ||
     ! git merge-base "$validation_base_sha" "$validation_head_sha" >/dev/null 2>&1; then
    echo "exact PR validation commits are unavailable or unrelated" >&2
    exit 2
  fi

  validation_mode="pr-validation"
  capture_fixture="scripts/foundation/fixtures/voc112-navigation-benchmark-traces.json"
  git diff --quiet "$validation_base_sha" "$validation_head_sha" -- "$capture_fixture"
  fixture_diff_status=$?
  case "$fixture_diff_status" in
    0) ;;
    1) validation_mode="pr-ancestry" ;;
    *)
      echo "capture fixture comparison failed" >&2
      exit 2
      ;;
  esac
elif [ "$validation_mode" = "squash-safe-push" ]; then
  :
elif [ "$validation_mode" != "local" ]; then
  usage
  exit 2
fi

export VOC112_CAPTURE_PROVENANCE_MODE="$validation_mode"
if [ -n "$validation_base_sha" ]; then
  export PR_BASE_SHA="$validation_base_sha"
  export PR_HEAD_SHA="$validation_head_sha"
else
  unset PR_BASE_SHA PR_HEAD_SHA
fi
echo "application-check provenance mode: $validation_mode"

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
