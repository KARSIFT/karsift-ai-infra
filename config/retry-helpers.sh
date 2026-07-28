#!/usr/bin/env bash
# Generic transient-failure retry for the opencode/claude CLI invocations in
# plan.yml/implement.yml/review.yml. Meant to be `source`d (not executed as
# a subprocess, unlike resolve-model.sh) because retry_if_transient needs to
# re-invoke the CALLER's own locally-defined function (run_review,
# run_implementer, run_planner, run_claude_implementer) by name in the same
# shell.
#
# Confirmed live 2026-07-28 (VOC-031-T09's PR review): review.yml's "Run
# independent verification" step failed twice in a row with exit code 1,
# ~11-12s elapsed, a completely empty /tmp/opencode-stderr.log - no match
# against the existing quota-exhaustion grep pattern, so nothing auto-
# recovered and a human had to notice and `gh run rerun --job` by hand. Both
# manual reruns succeeded immediately - textbook transient flakiness, not a
# real content/logic failure.
#
# Heuristic: retry ONLY a failure that is fast (<60s) or produced near-empty
# output (<200 bytes combined stderr/stdout) - a bare content-free crash. A
# failure that ran long or produced real output is NOT retried here; it
# falls straight through to whatever fallback/escalation logic the call site
# already has (quota-swap to a second account, model escalation, or final
# job failure) - that's much more likely a genuine content/model problem,
# and blindly retrying it would just burn 2x the time hiding a signal that
# should surface loudly instead.
#
# Deliberately safe against masking a real reviewer verdict: `opencode run`/
# `claude -p` both exit 0 whenever they produce ANY response, regardless of
# whether that response says PASS or FAIL - this wrapper's first check
# (`rc -eq 0` -> return immediately) means a normal FAIL verdict never
# reaches the retry heuristic at all. No change to reviewer judgment.
#
# Bounded to 2 extra same-model attempts, 5s then 15s backoff, so total
# added wall-clock on the worst case (3 fast failures in a row) is under a
# minute - negligible against these steps' 15-55 minute timeouts, and
# self-limiting against those timeouts by construction: a near-timeout
# failure necessarily has duration >= 60s, which never matches the
# short-duration retry branch.
#
# Usage:
#   source karsift-ai-infra/config/retry-helpers.sh
#   retry_if_transient <log-file-1> [<log-file-2> ...] -- <command> [args...]
#   rc=$?
#
# The log files (everything before the literal `--`) are read to compute the
# "near-empty output" byte count; they don't need to exist yet on the first
# attempt (missing files count as zero bytes).
retry_if_transient() {
  local max_extra_attempts=2
  local -a backoffs=(5 15)
  local -a log_files=()
  local start end duration out_bytes rc attempt=0

  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    log_files+=("$1")
    shift
  done
  if [ "$#" -eq 0 ]; then
    echo "retry_if_transient: missing '--' before the command to run" >&2
    return 2
  fi
  shift # drop the "--"

  while true; do
    start=$(date +%s)
    "$@"
    rc=$?
    end=$(date +%s)
    duration=$((end - start))

    if [ "$rc" -eq 0 ]; then
      return 0
    fi

    out_bytes=$(cat "${log_files[@]}" 2>/dev/null | wc -c)

    if [ "$attempt" -ge "$max_extra_attempts" ] || { [ "$duration" -ge 60 ] && [ "$out_bytes" -ge 200 ]; }; then
      return "$rc"
    fi

    echo "::warning::attempt $((attempt + 1)) of '$*' failed after ${duration}s / ${out_bytes} bytes of output (exit $rc) - looks transient, retrying same model in ${backoffs[$attempt]}s." >&2
    sleep "${backoffs[$attempt]}"
    attempt=$((attempt + 1))
  done
}
