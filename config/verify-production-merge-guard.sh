#!/usr/bin/env bash
# Fetch and validate the effective server-side base-race guard without
# publishing GitHub CLI errors or credentials.
set -euo pipefail

repository=${1:-}
production_branch=${2:-}
policy_root=${3:-}

if ! [[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] ||
   ! [[ "$production_branch" =~ ^[A-Za-z0-9._/-]+$ ]] ||
   [ -z "$policy_root" ]; then
  echo "production-merge-guard: invalid_input" >&2
  exit 1
fi

guard_tmp=$(mktemp -d)
cleanup() {
  rm -f "$guard_tmp/effective.json" "$guard_tmp/ruleset.json" \
    "$guard_tmp/rulesets.json" "$guard_tmp/rulesets.next.json"
  rmdir "$guard_tmp" 2>/dev/null || true
}
trap cleanup EXIT

encoded_branch=$(jq -rn --arg branch "$production_branch" '$branch | @uri')
if ! gh api "repos/$repository/rules/branches/$encoded_branch" \
  > "$guard_tmp/effective.json" 2>/dev/null; then
  echo "production-merge-guard: effective_rules_unavailable" >&2
  exit 1
fi

mapfile -t ruleset_ids < <(
  jq -r '.[] | select(.type == "required_status_checks") | .ruleset_id' \
    "$guard_tmp/effective.json" | sort -nu
)
printf '[]\n' > "$guard_tmp/rulesets.json"
for ruleset_id in "${ruleset_ids[@]}"; do
  if ! [[ "$ruleset_id" =~ ^[1-9][0-9]*$ ]]; then
    echo "production-merge-guard: ruleset_identity_invalid" >&2
    exit 1
  fi
  if ! gh api "repos/$repository/rulesets/$ruleset_id" \
    > "$guard_tmp/ruleset.json" 2>/dev/null; then
    echo "production-merge-guard: ruleset_unavailable" >&2
    exit 1
  fi
  jq -s '.[0] + [.[1]]' "$guard_tmp/rulesets.json" "$guard_tmp/ruleset.json" \
    > "$guard_tmp/rulesets.next.json"
  mv "$guard_tmp/rulesets.next.json" "$guard_tmp/rulesets.json"
done

guard_output=$(mktemp)
if ! python3 "$policy_root/production-merge-guard-runner.py" \
  --repository "$repository" \
  --effective-rules-file "$guard_tmp/effective.json" \
  --rulesets-file "$guard_tmp/rulesets.json" \
  >"$guard_output" 2>&1; then
  cat "$guard_output" >&2
  if grep -q 'production_merge_guard_payload_incomplete' "$guard_output"; then
    echo "production-merge-guard: operator_action=configure karsift-ai-infra-bot Administration Read and write, approve KARSIFT installation 148001476, retain caller-repository guard scope, do not rotate secrets, rerun" >&2
  fi
  rm -f "$guard_output"
  exit 1
fi
cat "$guard_output"
rm -f "$guard_output"
