#!/usr/bin/env python3
"""Return the remediation lifecycle decision for one exact PR-head run."""

import argparse


def parse_bool(value: str) -> bool:
    if value not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return value == "true"


def decide(
    *,
    expected_sha: str,
    current_sha: str,
    review_state: str,
    ci_failed: bool,
    review_job_failed: bool,
) -> str:
    if expected_sha and expected_sha != current_sha:
        return "STALE"
    if ci_failed or review_job_failed:
        return "RETRY"
    if review_state == "WAITING":
        return "WAITING"
    if review_state == "FAIL":
        return "RETRY"
    return "NOOP"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--review-state", required=True)
    parser.add_argument("--ci-failed", required=True, type=parse_bool)
    parser.add_argument("--review-job-failed", required=True, type=parse_bool)
    args = parser.parse_args()
    print(decide(**vars(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
