#!/usr/bin/env python3
"""Hosted read-only adapter for VOC-113 promotion check recovery proof."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from verify_promotion_check_recovery import (
    VerificationResult,
    verify_current_ref,
    verify_promotion_pr_identity,
    verify_required_checks,
)
from actions_check_recovery import select_gate_evidence


class VerificationError(RuntimeError):
    """Sanitized fail-closed verification refusal."""


def require(result: VerificationResult) -> None:
    if not result.ok:
        raise VerificationError(result.reason)


def gh_api(token: str, repository: str, path: str) -> dict:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    completed = subprocess.run(
        ["gh", "api", path, "--repo", repository],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise VerificationError("github_metadata_read_failed")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise VerificationError("invalid_github_payload")
    return payload


def gh_api_paginate(token: str, repository: str, path: str) -> list[dict]:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    completed = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path, "--repo", repository],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise VerificationError("github_metadata_read_failed")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise VerificationError("invalid_paginated_payload")
    items: list[dict] = []
    for page in payload:
        if not isinstance(page, dict):
            raise VerificationError("invalid_paginated_page")
        batch = page.get("check_runs")
        if isinstance(batch, list):
            items.extend(batch)
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--promotion-pr-number", type=int, required=True)
    parser.add_argument("--current-ref", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = parser.parse_args()

    if not args.repository or not args.github_token:
        print("verify-promotion-check-recovery: missing repository or token", file=sys.stderr)
        return 1

    try:
        pr = gh_api(
            args.github_token,
            args.repository,
            f"repos/{args.repository}/pulls/{args.promotion_pr_number}",
        )
        require(
            verify_promotion_pr_identity(
                pr,
                repository=args.repository,
                pr_number=args.promotion_pr_number,
            )
        )
        head_sha = (pr.get("head") or {}).get("sha")
        if not isinstance(head_sha, str):
            raise VerificationError("invalid_head_sha")
        require(verify_current_ref(args.current_ref, head_sha))
        check_runs = gh_api_paginate(
            args.github_token,
            args.repository,
            f"repos/{args.repository}/commits/{head_sha}/check-runs?per_page=100",
        )
        statuses = json.loads(
            subprocess.run(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{args.repository}/commits/{head_sha}/status?per_page=100",
                    "--repo",
                    args.repository,
                ],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "GH_TOKEN": args.github_token, "GH_REPO": args.repository},
            ).stdout
        )
        gate_summary = select_gate_evidence(
            [{"check_runs": check_runs, "total_count": len(check_runs)}],
            statuses,
            head_sha=head_sha,
        )
        require(verify_required_checks(gate_summary, head_sha=head_sha))
    except VerificationError as exc:
        print(f"verify-promotion-check-recovery: {exc}", file=sys.stderr)
        return 1

    print(
        "verify-promotion-check-recovery: ok "
        f"promotion_pr={args.promotion_pr_number} head_sha={head_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
