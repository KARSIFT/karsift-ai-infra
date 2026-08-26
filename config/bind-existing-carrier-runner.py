#!/usr/bin/env python3
"""Workflow runner: bind existing-carrier identity via GitHub metadata (VOC-125)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from bind_existing_carrier import BindFailure, bind_existing_carrier


def gh_json(args: list[str], *, token: str) -> Any:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    completed = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(completed.stdout or "null")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--attempt", required=True, type=int)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--issue-number", required=True)
    parser.add_argument("--integration-branch", required=True)
    parser.add_argument("--existing-pr-number", default="")
    parser.add_argument("--expected-head-sha", default="")
    parser.add_argument("--expected-base-sha", default="")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        print("GH_TOKEN is required", file=sys.stderr)
        return 1

    from auto_advance_ownership import branch_name

    branch = branch_name(args.change_id, args.task_id)
    repo = args.repository

    issue_state = gh_json(
        [
            "issue",
            "view",
            args.issue_number,
            "--repo",
            repo,
            "--json",
            "state",
        ],
        token=token,
    ).get("state", "")

    remote_line = subprocess.run(
        [
            "git",
            "ls-remote",
            "--heads",
            "origin",
            branch,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    remote_branch_head = None
    has_remote_branch = False
    if remote_line.returncode == 0 and remote_line.stdout.strip():
        has_remote_branch = True
        remote_branch_head = remote_line.stdout.strip().split()[0]

    open_pr = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GH_TOKEN": token},
    )
    open_pr_number = None
    if open_pr.returncode == 0:
        items = json.loads(open_pr.stdout or "[]")
        if items:
            open_pr_number = str(items[0].get("number") or "")

    pr_data = None
    review_comments: list[dict[str, Any]] | None = None
    pr_number_for_comments: str | None = None

    if args.existing_pr_number.strip():
        pr_number_for_comments = args.existing_pr_number.strip()
        pr_data = gh_json(
            [
                "pr",
                "view",
                pr_number_for_comments,
                "--repo",
                repo,
                "--json",
                "number,state,title,body,headRefName,baseRefName,headRefOid,baseRefOid",
            ],
            token=token,
        )
        pr_data["repository"] = repo
    elif args.attempt == 2 and (args.expected_head_sha or args.expected_base_sha):
        if not open_pr_number:
            pr_data = None
        else:
            pr_number_for_comments = open_pr_number
            pr_data = gh_json(
                [
                    "pr",
                    "view",
                    open_pr_number,
                    "--repo",
                    repo,
                    "--json",
                    "number,state,title,body,headRefName,baseRefName,headRefOid,baseRefOid",
                ],
                token=token,
            )
            pr_data["repository"] = repo

    if pr_number_for_comments:
        pages = gh_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repo}/issues/{pr_number_for_comments}/comments?per_page=100",
            ],
            token=token,
        )
        review_comments = [item for page in pages for item in page]

    result = bind_existing_carrier(
        attempt=args.attempt,
        change_id=args.change_id,
        package_path=args.package_path,
        task_id=args.task_id,
        issue_number=args.issue_number,
        integration_branch=args.integration_branch,
        repository=repo,
        existing_pr_number=args.existing_pr_number,
        expected_head_sha=args.expected_head_sha,
        expected_base_sha=args.expected_base_sha,
        issue_state=issue_state,
        remote_branch_head=remote_branch_head,
        has_remote_branch=has_remote_branch,
        open_pr_number=open_pr_number,
        pr_data=pr_data,
        review_comments=review_comments,
    )

    if isinstance(result, BindFailure):
        detail = f" ({result.detail})" if result.detail else ""
        print(
            f"Existing-carrier bind failed: {result.code}{detail}",
            file=sys.stderr,
        )
        return 1

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"expected_head_sha={result.expected_head_sha}\n")
            handle.write(f"expected_base_sha={result.expected_base_sha}\n")

    print(
        json.dumps(
            {
                "expected_head_sha": result.expected_head_sha,
                "expected_base_sha": result.expected_base_sha,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
