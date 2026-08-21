#!/usr/bin/env python3
"""GitHub metadata adapter for VOC-104 ready_for_review reuse eligibility."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from ready_for_review_reuse import (
    PACKAGE_RE,
    TASK_RE,
    PipelineRunSummary,
    ReuseDecision,
    evaluate_reuse_eligibility,
    shared_policy_sha,
)


class MetadataError(RuntimeError):
    """Sanitized metadata read or shape failure."""


class GitHubApi:
    def __init__(self, token: str, repository: str):
        self.token = token
        self.repository = repository

    def gh(self, args: list[str], accepted_codes: tuple[int, ...] = (0,)) -> str:
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
        if completed.returncode not in accepted_codes:
            raise MetadataError("github_metadata_read_failed")
        return completed.stdout.strip()


def write_output(decision: ReuseDecision) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [
        f"outcome={decision.outcome}",
        f"reason={decision.reason}",
    ]
    prior_run_id = "" if decision.prior_run_id is None else str(decision.prior_run_id)
    lines.append(f"prior_run_id={prior_run_id}")
    payload = "\n".join(lines) + "\n"
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        sys.stdout.write(payload)


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
    comments = [item for page in payload for item in page]
    if not isinstance(comments, list):
        raise MetadataError("invalid_comment_payload")
    return comments


def load_pipeline_runs(
    api: GitHubApi,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> list[PipelineRunSummary]:
    payload = json.loads(
        api.gh(
            [
                "api",
                f"/repos/{api.repository}/actions/runs?event=pull_request&head_sha={head_sha}&per_page=100",
            ]
        )
    )
    if not isinstance(payload, dict):
        raise MetadataError("invalid_run_payload")
    runs = payload.get("workflow_runs")
    if (
        not isinstance(runs, list)
        or int(payload.get("total_count") or 0) > len(runs)
        or len(runs) > 100
    ):
        raise MetadataError("invalid_run_payload")
    summaries: list[PipelineRunSummary] = []
    for run in runs:
        if str(run.get("name") or "") != "pipeline":
            continue
        associations = [
            pr
            for pr in run.get("pull_requests") or []
            if pr.get("number") == pr_number
        ]
        if len(associations) != 1:
            continue
        associated_base = str((associations[0].get("base") or {}).get("sha") or "").lower()
        if associated_base != base_sha.lower():
            continue
        run_id = int(run.get("id") or 0)
        if run_id <= 0:
            continue
        jobs_payload = json.loads(
            api.gh(
                [
                    "api",
                    f"/repos/{api.repository}/actions/runs/{run_id}/jobs?per_page=100",
                ]
            )
        )
        jobs = jobs_payload.get("jobs")
        if not isinstance(jobs, list):
            raise MetadataError("invalid_job_payload")
        if jobs_payload.get("total_count") != len(jobs) or len(jobs) > 100:
            raise MetadataError("incomplete_job_set")
        summaries.append(
            PipelineRunSummary(
                run_id=run_id,
                workflow_path=str(run.get("path") or ""),
                event=str(run.get("event") or ""),
                head_sha=str(run.get("head_sha") or "").lower(),
                head_branch=str(run.get("head_branch") or ""),
                base_sha=associated_base,
                status=str(run.get("status") or ""),
                conclusion=str(run.get("conclusion") or "") or None,
                jobs=tuple(jobs),
                policy_sha=shared_policy_sha(run),
            )
        )
    return summaries


def load_current_policy_sha(api: GitHubApi, current_run_id: int) -> str:
    run = json.loads(
        api.gh(
            [
                "api",
                f"/repos/{api.repository}/actions/runs/{current_run_id}",
            ]
        )
    )
    if not isinstance(run, dict) or int(run.get("id") or 0) != current_run_id:
        raise MetadataError("invalid_current_run_payload")
    policy_sha = shared_policy_sha(run)
    if not policy_sha:
        raise MetadataError("invalid_current_policy_revision")
    return policy_sha


def load_pr_checks(api: GitHubApi, pr_number: int) -> list[dict]:
    payload = json.loads(
        api.gh(
            ["pr", "checks", str(pr_number), "--json", "name,state"],
            accepted_codes=(0, 8),
        )
    )
    if not isinstance(payload, list) or not payload:
        raise MetadataError("invalid_pr_check_payload")
    return payload


def result_path_exists(
    *,
    repository_root: Path,
    package_path: str,
    task_id: str,
) -> bool:
    if not PACKAGE_RE.fullmatch(package_path) or not TASK_RE.fullmatch(task_id):
        return False
    result_path = repository_root / package_path / ".karsift/live-evidence" / f"{task_id}.result.json"
    return result_path.is_file()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--expected-base-sha", required=True)
    parser.add_argument("--event-action", required=True)
    parser.add_argument("--current-run-id", type=int, required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or not args.repository or args.pr_number <= 0:
        write_output(
            ReuseDecision("fail-closed-to-full-path", "missing_required_inputs")
        )
        return 0

    try:
        api = GitHubApi(token, args.repository)
        pr = json.loads(
            api.gh(
                [
                    "pr",
                    "view",
                    str(args.pr_number),
                    "--json",
                    "body,headRefName,headRefOid,baseRefOid,isDraft",
                ]
            )
        )
        is_draft = pr.get("isDraft")
        if not isinstance(is_draft, bool):
            raise MetadataError("invalid_is_draft")
        comments = load_comments(api, args.pr_number)
        live_head = str(pr.get("headRefOid") or "").lower()
        live_base = str(pr.get("baseRefOid") or "").lower()
        pipeline_runs = load_pipeline_runs(
            api,
            args.pr_number,
            live_head,
            live_base,
        )
        current_policy_sha = load_current_policy_sha(api, args.current_run_id)
        pr_checks = load_pr_checks(api, args.pr_number)
        head_ref = str(pr.get("headRefName") or "")
        body = str(pr.get("body") or "")
        task_match = __import__("re").findall(r"(?<=Implements task `)[^`]+", body)
        package_match = __import__("re").findall(r"(?<=Package path: `)[^`]+", body)
        task_id = task_match[0] if len(task_match) == 1 else ""
        package_path = package_match[0] if len(package_match) == 1 else ""
        if head_ref.startswith("plan/"):
            package_match = __import__("re").findall(
                r"(?<=New package directory: `)[^`]+", body
            )
            package_path = package_match[0] if len(package_match) == 1 else ""
        result_exists = result_path_exists(
            repository_root=Path.cwd().resolve(),
            package_path=package_path,
            task_id=task_id,
        )
        decision = evaluate_reuse_eligibility(
            event_action=args.event_action,
            expected_head_sha=args.expected_head_sha,
            expected_base_sha=args.expected_base_sha,
            live_head_sha=str(pr.get("headRefOid") or ""),
            live_base_sha=str(pr.get("baseRefOid") or ""),
            is_draft=is_draft,
            head_ref=head_ref,
            pr_body=body,
            comments=comments,
            pipeline_runs=pipeline_runs,
            pr_checks=pr_checks,
            current_run_id=args.current_run_id,
            current_policy_sha=current_policy_sha,
            result_path_exists=result_exists,
        )
    except MetadataError as exc:
        decision = ReuseDecision("fail-closed-to-full-path", str(exc))
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        decision = ReuseDecision("fail-closed-to-full-path", "evaluation_internal_error")

    write_output(decision)
    print(
        json.dumps(
            {
                "outcome": decision.outcome,
                "reason": decision.reason,
                "prior_run_id": decision.prior_run_id,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
