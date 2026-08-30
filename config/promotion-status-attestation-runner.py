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
from promotion_status_attestation import (
    AttestationError,
    attestable_contexts,
    verify_promotion_required_run_semantics,
)
from required_check_satisfaction import SatisfactionError, parse_gh_pr_checks_json


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


def gh_api_paginated_jobs(
    token: str, repository: str, run_id: int
) -> list[dict]:
    command = [
        "gh",
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100",
    ]
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if completed.returncode:
        raise RunnerError("github_api_failed")
    pages = json.loads(completed.stdout or "null")
    if not isinstance(pages, list) or any(
        not isinstance(page, dict)
        or not isinstance(page.get("jobs"), list)
        or any(not isinstance(job, dict) for job in page["jobs"])
        for page in pages
    ):
        raise RunnerError("invalid_workflow_jobs_payload")
    return [job for page in pages for job in page["jobs"]]


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
        env = os.environ.copy()
        env["GH_TOKEN"] = args.github_token
        env["GH_REPO"] = args.repository
        completed = subprocess.run(
            [
                "gh",
                "pr",
                "checks",
                str(args.pr_number),
                "--required",
                "--json",
                "name,state",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if not completed.stdout.strip():
            raise RunnerError("required_pr_checks_read_failed")
        pr_required_checks = parse_gh_pr_checks_json(
            json.loads(completed.stdout)
        )
        contexts = attestable_contexts(summary, pr_required_checks=pr_required_checks)
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
        base = pull_request.get("base") if isinstance(pull_request, dict) else None
        head = pull_request.get("head") if isinstance(pull_request, dict) else None
        if (
            not isinstance(base, dict)
            or not isinstance(head, dict)
            or base.get("ref") != "main"
            or head.get("ref") != "develop"
            or (head.get("repo") or {}).get("full_name") != args.repository
            or (base.get("repo") or {}).get("full_name") != args.repository
        ):
            raise RunnerError("promotion_pair_mismatch")
        base_sha = validate_sha(str(base.get("sha") or ""), "base_sha")
        for context, run_id in contexts:
            run_payload = gh_api(
                args.github_token,
                args.repository,
                f"repos/{args.repository}/actions/runs/{run_id}",
            )
            jobs = (
                gh_api_paginated_jobs(
                    args.github_token, args.repository, run_id
                )
                if context == "ci / ci"
                else None
            )
            verify_promotion_required_run_semantics(
                run_payload,
                context=context,
                run_id=run_id,
                repository=args.repository,
                pr_number=args.pr_number,
                base_sha=base_sha,
                head_sha=head_sha,
                base_ref="main",
                head_ref="develop",
                jobs=jobs,
            )
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
    except (
        AttestationError,
        RecoveryError,
        RunnerError,
        SatisfactionError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"promotion-status-attestation: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
