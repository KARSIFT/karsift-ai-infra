#!/usr/bin/env python3
"""Hosted read-only adapter for VOC-113 post-promotion workflow proof."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from verify_post_promotion_workflow import (
    VerificationResult,
    verify_current_ref,
    verify_post_promotion_run,
    verify_promotion_merged,
)


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


def gh_api_list(token: str, repository: str, path: str) -> list[dict]:
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
        batch = page.get("workflow_runs")
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
        print("verify-post-promotion-workflow: missing repository or token", file=sys.stderr)
        return 1

    try:
        pr = gh_api(
            args.github_token,
            args.repository,
            f"repos/{args.repository}/pulls/{args.promotion_pr_number}",
        )
        require(
            verify_promotion_merged(
                pr,
                repository=args.repository,
                pr_number=args.promotion_pr_number,
            )
        )
        merge_sha = pr.get("merge_commit_sha")
        if not isinstance(merge_sha, str):
            raise VerificationError("missing_merge_commit")
        require(verify_current_ref(args.current_ref, merge_sha))
        runs = gh_api_list(
            args.github_token,
            args.repository,
            f"repos/{args.repository}/actions/runs?head_sha={merge_sha}&per_page=100",
        )
        require(
            verify_post_promotion_run(
                runs,
                repository=args.repository,
                merge_sha=merge_sha,
            )
        )
    except VerificationError as exc:
        print(f"verify-post-promotion-workflow: {exc}", file=sys.stderr)
        return 1

    print(
        "verify-post-promotion-workflow: ok "
        f"promotion_pr={args.promotion_pr_number} merge_sha={merge_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
