#!/usr/bin/env python3
"""Publish ruleset-compatible statuses from verified promotion check evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from actions_check_recovery import RecoveryError, validate_promotion_target, validate_sha
from promotion_status_attestation import AttestationError, attestable_contexts


REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RunnerError(RuntimeError):
    """Sanitized publication failure."""


def gh_api(
    token: str,
    repository: str,
    endpoint: str,
    *,
    method: str | None = None,
    payload: dict | None = None,
) -> dict:
    command = ["gh", "api"]
    if method:
        command.extend(["--method", method])
    command.append(endpoint)
    if payload is not None:
        command.extend(["--input", "-"])
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    completed = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if completed.returncode:
        raise RunnerError("github_api_failed")
    return json.loads(completed.stdout or "{}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authoritative-file", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--branch-ref", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = parser.parse_args()

    try:
        if not args.github_token:
            raise RunnerError("missing_token")
        if not REPOSITORY_RE.fullmatch(args.repository):
            raise RunnerError("invalid_repository")
        head_sha = validate_sha(args.head_sha, "head_sha")
        expected_url = re.compile(
            rf"^https://github\.com/{re.escape(args.repository)}/actions/runs/[1-9][0-9]*$"
        )
        if not expected_url.fullmatch(args.target_url):
            raise RunnerError("invalid_target_url")
        summary = json.loads(Path(args.authoritative_file).read_text(encoding="utf-8"))
        contexts = attestable_contexts(summary)
        pull_request = gh_api(
            args.github_token,
            args.repository,
            f"repos/{args.repository}/pulls/{args.pr_number}",
        )
        validate_promotion_target(
            pull_request,
            target_sha=head_sha,
            branch_ref=args.branch_ref,
            pr_number=args.pr_number,
        )
        for context, run_id in contexts:
            gh_api(
                args.github_token,
                args.repository,
                f"repos/{args.repository}/statuses/{head_sha}",
                method="POST",
                payload={
                    "state": "success",
                    "context": context,
                    "description": "Exact-head recovery passed; release policy attested.",
                    "target_url": args.target_url,
                },
            )
            print(f"promotion-status-attestation: published {context} from run {run_id}")
    except (AttestationError, RecoveryError, RunnerError, OSError, json.JSONDecodeError) as exc:
        print(f"promotion-status-attestation: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
