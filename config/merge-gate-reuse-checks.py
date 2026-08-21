#!/usr/bin/env python3
"""Merge-gate reuse-aware check evaluation for VOC-104."""

from __future__ import annotations

import argparse
import json
import sys

from ready_for_review_reuse import (
    compute_checks_ok,
    compute_checks_ok_with_reuse,
    publisher_check_ok,
    shared_policy_sha,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["checks", "publisher", "policy"])
    parser.add_argument("--pr-checks-file", default="")
    parser.add_argument("--prior-jobs-file", default="")
    parser.add_argument("--current-run-file", default="")
    parser.add_argument("--prior-run-file", default="")
    parser.add_argument("--head-ref", default="")
    parser.add_argument("--reuse-outcome", default="")
    args = parser.parse_args()

    if args.mode == "policy":
        if not args.current_run_file or not args.prior_run_file:
            raise ValueError("missing policy run payload")
        current_run = json.loads(
            open(args.current_run_file, encoding="utf-8").read()
        )
        prior_run = json.loads(open(args.prior_run_file, encoding="utf-8").read())
        if not isinstance(current_run, dict) or not isinstance(prior_run, dict):
            raise ValueError("invalid policy run payload")
        current_sha = shared_policy_sha(current_run)
        prior_sha = shared_policy_sha(prior_run)
        print("true" if current_sha and current_sha == prior_sha else "false")
        return 0

    if not args.pr_checks_file or not args.head_ref:
        raise ValueError("missing check evaluation input")
    pr_checks = json.loads(open(args.pr_checks_file, encoding="utf-8").read())
    prior_jobs: list[dict] = []
    if args.prior_jobs_file:
        prior_payload = json.loads(
            open(args.prior_jobs_file, encoding="utf-8").read()
        )
        if not isinstance(prior_payload, dict) or not isinstance(
            prior_payload.get("jobs"), list
        ):
            raise ValueError("invalid prior jobs payload")
        prior_jobs = prior_payload["jobs"]

    if args.mode == "checks":
        if args.reuse_outcome == "reuse-evidence" and prior_jobs:
            ok = compute_checks_ok_with_reuse(
                pr_checks=pr_checks,
                head_ref=args.head_ref,
                prior_jobs=prior_jobs,
                reuse_outcome=args.reuse_outcome,
            )
        else:
            ok = compute_checks_ok(pr_checks)
        print("true" if ok else "false")
        return 0

    ok = publisher_check_ok(
        pr_checks=pr_checks,
        head_ref=args.head_ref,
        prior_jobs=prior_jobs or None,
        reuse_outcome=args.reuse_outcome,
    )
    print("true" if ok else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
