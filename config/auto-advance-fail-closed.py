#!/usr/bin/env python3
"""Fail-closed escalation marker for auto-advance ownership metadata (VOC-102)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

from auto_advance_ownership import FAIL_CLOSED_MARKER_PREFIX


SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
TRUSTED_BOT_LOGINS = {"app/karsift-ai-infra-bot", "karsift-ai-infra-bot"}


def issue_has_marker(repo: str, issue_number: int, token: str, prefix: str) -> bool:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repo
    completed = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            "--json",
            "comments",
            "--repo",
            repo,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    comments = json.loads(completed.stdout)["comments"]
    matches = [comment for comment in comments if prefix in comment.get("body", "")]
    if any(
        (comment.get("author") or {}).get("login") not in TRUSTED_BOT_LOGINS
        for comment in matches
    ):
        raise ValueError("untrusted_fail_closed_marker")
    if len(matches) > 1:
        raise ValueError("duplicate_fail_closed_marker")
    return bool(matches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("KARSIFT_APP_TOKEN", ""))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    if not args.repository or not args.token:
        print("repository and token are required", file=sys.stderr)
        return 2
    if not SAFE_REASON_RE.fullmatch(args.reason):
        print("invalid fail-closed reason", file=sys.stderr)
        return 2

    prefix = f"{FAIL_CLOSED_MARKER_PREFIX} `{args.task_id}`"
    try:
        if issue_has_marker(args.repository, args.issue_number, args.token, prefix):
            print("fail-closed marker already present", file=sys.stderr)
            return 0
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        print("fail-closed marker validation failed", file=sys.stderr)
        return 1

    body = (
        f"{prefix} ({args.reason}). "
        "No implementer run was started and no evidence carrier was created from "
        "untrusted metadata. Operator review required."
    )
    env = os.environ.copy()
    env["GH_TOKEN"] = args.token
    env["GH_REPO"] = args.repository
    subprocess.run(
        [
            "gh",
            "issue",
            "comment",
            str(args.issue_number),
            "--body",
            body,
            "--repo",
            args.repository,
        ],
        check=True,
        env=env,
    )
    print(f"fail-closed marker posted for {args.task_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
