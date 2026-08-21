#!/usr/bin/env python3
"""Hosted read-only adapter for VOC-104 ready_for_review reuse proof."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

from verify_ready_for_review_reuse import (
    VerificationResult,
    verify_current_ref,
    verify_prior_jobs,
    verify_prior_run,
    verify_ready_jobs,
    verify_ready_run,
)
from ready_for_review_reuse import (
    classify_verdict,
    parse_identity_lines,
    trusted_review_comment,
)


class VerificationError(RuntimeError):
    """Sanitized fail-closed verification refusal."""


class GitHubApi:
    def __init__(self, token: str, repository: str):
        self.token = token
        self.repository = repository

    def gh(self, args: list[str]) -> str:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.token
        env["GH_REPO"] = self.repository
        command = ["gh", *args]
        if not args or args[0] != "api":
            command.extend(["--repo", self.repository])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            raise VerificationError("github_metadata_read_failed")
        return completed.stdout.strip()


def require(result: VerificationResult) -> None:
    if not result.ok:
        raise VerificationError(result.reason)


def load_jobs(api: GitHubApi, run_id: int) -> list[dict]:
    payload = json.loads(
        api.gh(
            [
                "api",
                f"/repos/{api.repository}/actions/runs/{run_id}/jobs?per_page=100",
            ]
        )
    )
    jobs = payload.get("jobs")
    if (
        not isinstance(jobs, list)
        or payload.get("total_count") != len(jobs)
        or len(jobs) > 100
    ):
        raise VerificationError("job_set_incomplete")
    return jobs


def load_comments(api: GitHubApi, pr_number: int) -> list[dict]:
    payload = json.loads(
        api.gh(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{api.repository}/issues/{pr_number}/comments?per_page=100",
            ]
        )
    )
    if not isinstance(payload, list) or any(not isinstance(page, list) for page in payload):
        raise VerificationError("comment_set_invalid")
    return [comment for page in payload for comment in page]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--ready-run-id", type=int, required=True)
    parser.add_argument("--prior-run-id", type=int, required=True)
    parser.add_argument("--source-pr-number", type=int, required=True)
    parser.add_argument("--expected-source-head-sha", required=True)
    parser.add_argument("--expected-source-base-sha", required=True)
    parser.add_argument("--expected-proof-head-sha", required=True)
    parser.add_argument("--current-ref", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if (
        not token
        or not args.repository
        or args.ready_run_id <= 0
        or args.prior_run_id <= 0
        or args.source_pr_number <= 0
    ):
        print("required read-only verification inputs are missing", file=sys.stderr)
        return 2

    try:
        if not re.fullmatch(r"[0-9a-f]{40}", args.expected_source_head_sha):
            raise VerificationError("invalid_source_head_sha")
        if not re.fullmatch(r"[0-9a-f]{40}", args.expected_source_base_sha):
            raise VerificationError("invalid_source_base_sha")
        if not re.fullmatch(r"[0-9a-f]{40}", args.expected_proof_head_sha):
            raise VerificationError("invalid_proof_head_sha")

        api = GitHubApi(token, args.repository)
        source_pr = json.loads(
            api.gh(
                [
                    "api",
                    f"/repos/{args.repository}/pulls/{args.source_pr_number}",
                ]
            )
        )
        head_ref = str((source_pr.get("head") or {}).get("ref") or "")
        source_head = str((source_pr.get("head") or {}).get("sha") or "").lower()
        source_base = str((source_pr.get("base") or {}).get("sha") or "").lower()
        if source_head != args.expected_source_head_sha.lower():
            raise VerificationError("source_head_mismatch")
        if source_base != args.expected_source_base_sha.lower():
            raise VerificationError("source_base_mismatch")

        require(
            verify_current_ref(
                current_ref=args.current_ref,
                expected_head_sha=args.expected_proof_head_sha.lower(),
            )
        )

        ready_run = json.loads(
            api.gh(
                [
                    "api",
                    f"/repos/{args.repository}/actions/runs/{args.ready_run_id}",
                ]
            )
        )
        require(
            verify_ready_run(
                run=ready_run,
                repository=args.repository,
                pr_number=args.source_pr_number,
                expected_head_sha=source_head,
                expected_base_sha=source_base,
                expected_head_ref=head_ref,
                source_pr=source_pr,
            )
        )
        require(
            verify_ready_jobs(
                jobs=load_jobs(api, args.ready_run_id),
                head_ref=head_ref,
            )
        )

        prior_run = json.loads(
            api.gh(
                [
                    "api",
                    f"/repos/{args.repository}/actions/runs/{args.prior_run_id}",
                ]
            )
        )
        require(
            verify_prior_run(
                run=prior_run,
                repository=args.repository,
                pr_number=args.source_pr_number,
                expected_head_sha=source_head,
                expected_base_sha=source_base,
                expected_head_ref=head_ref,
                prior_run_id=args.prior_run_id,
                ready_run_id=args.ready_run_id,
                source_pr=source_pr,
            )
        )
        require(
            verify_prior_jobs(
                jobs=load_jobs(api, args.prior_run_id),
                head_ref=head_ref,
            )
        )
        identity = parse_identity_lines(
            body=str(source_pr.get("body") or ""),
            head_ref=head_ref,
        )
        if identity is None:
            raise VerificationError("identity_metadata_mismatch")
        task_id, package_path, authority_issue, _ = identity
        verdict_body = trusted_review_comment(
            comments=load_comments(api, args.source_pr_number),
            head_sha=source_head,
            base_sha=source_base,
            task_id=task_id,
            package_path=package_path,
            authority_issue=authority_issue,
            pipeline_run_id=args.prior_run_id,
        )
        if not verdict_body:
            raise VerificationError("prior_review_binding_missing")
        if classify_verdict(verdict_body) not in {"PASS", "PASS_WITH_FINDINGS"}:
            raise VerificationError("prior_review_not_passing")
    except (
        VerificationError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
    ) as exc:
        reason = str(exc) if isinstance(exc, VerificationError) else "verification_internal_error"
        print(f"verify failed: {reason}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ready_run_id": args.ready_run_id,
                "prior_run_id": args.prior_run_id,
                "source_pr_number": args.source_pr_number,
                "source_head_sha": args.expected_source_head_sha.lower(),
                "source_base_sha": args.expected_source_base_sha.lower(),
                "proof_head_sha": args.expected_proof_head_sha.lower(),
                "verify_result": "pass",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
