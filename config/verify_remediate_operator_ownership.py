#!/usr/bin/env python3
"""Pure read-only proof policy for VOC-106 remediate operator ownership."""

from __future__ import annotations

from dataclasses import dataclass
import re

from remediate_ownership import OPERATOR_ESCALATION_MARKER_PREFIX


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TRUSTED_BOT_LOGINS = {
    "app/karsift-ai-infra-bot",
    "karsift-ai-infra-bot",
    "github-actions[bot]",
}


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""


def verify_source_run(
    *,
    run: dict,
    repository: str,
    pr_number: int,
    expected_head_sha: str,
    expected_base_sha: str,
) -> VerificationResult:
    if run.get("repository", {}).get("full_name") != repository:
        return VerificationResult(False, "wrong_repository")
    if run.get("name") != "pipeline" or run.get("path") != ".github/workflows/pipeline.yml":
        return VerificationResult(False, "wrong_workflow")
    if run.get("event") != "pull_request":
        return VerificationResult(False, "wrong_event")
    if run.get("status") != "completed":
        return VerificationResult(False, "source_run_not_completed")
    pull_requests = run.get("pull_requests") or []
    if not isinstance(pull_requests, list) or len(pull_requests) != 1:
        return VerificationResult(False, "source_pr_mismatch")
    source_pr = pull_requests[0]
    if not isinstance(source_pr, dict):
        return VerificationResult(False, "source_pr_mismatch")
    if source_pr.get("number") != pr_number:
        return VerificationResult(False, "source_pr_mismatch")
    head = source_pr.get("head")
    base = source_pr.get("base")
    if not isinstance(head, dict) or head.get("sha") != expected_head_sha:
        return VerificationResult(False, "source_head_mismatch")
    if not isinstance(base, dict) or base.get("sha") != expected_base_sha:
        return VerificationResult(False, "source_base_mismatch")
    return VerificationResult(True)


def verify_source_to_carrier_lineage(
    *,
    comparison: dict,
    source_head_sha: str,
    carrier_head_sha: str,
) -> VerificationResult:
    if not SHA_RE.fullmatch(source_head_sha) or not SHA_RE.fullmatch(carrier_head_sha):
        return VerificationResult(False, "invalid_head_lineage")
    if source_head_sha == carrier_head_sha:
        return VerificationResult(False, "source_and_carrier_head_not_distinct")
    if (
        comparison.get("status") != "ahead"
        or comparison.get("merge_base_commit", {}).get("sha") != source_head_sha
        or comparison.get("base_commit", {}).get("sha") != source_head_sha
    ):
        return VerificationResult(False, "source_not_carrier_ancestor")
    commits = comparison.get("commits")
    if not isinstance(commits, list) or not commits:
        return VerificationResult(False, "source_not_carrier_ancestor")
    if commits[-1].get("sha") != carrier_head_sha:
        return VerificationResult(False, "carrier_head_mismatch")
    return VerificationResult(True)


def verify_source_evidence(
    evidence_text: str,
    *,
    source_run_id: int,
    source_head_sha: str,
) -> VerificationResult:
    required = [
        "gate_status: source-proof-complete",
        f"source_run_id: `{source_run_id}`",
        f"source_head_sha: `{source_head_sha}`",
        "source_pipeline_conclusion: `success`",
        "should_retry: `false`",
        "implementer_job: `skipped`",
        "operator_escalation_marker: `present`",
        "ordinary_retry_fixture: `passed`",
    ]
    lines = evidence_text.splitlines()
    if any(lines.count(item) != 1 for item in required):
        return VerificationResult(False, "source_evidence_incomplete")
    return VerificationResult(True)


def _matching_jobs(jobs: list[dict], suffix: str) -> list[dict]:
    return [job for job in jobs if str(job.get("name") or "").endswith(suffix)]


def verify_source_jobs(jobs: list[dict]) -> VerificationResult:
    decide_jobs = _matching_jobs(jobs, "remediate / decide")
    if len(decide_jobs) != 1 or decide_jobs[0].get("conclusion") != "success":
        return VerificationResult(False, "remediate_decide_not_observed")

    retry_jobs = [
        job
        for job in jobs
        if str(job.get("name") or "") == "remediate / retry"
        or str(job.get("name") or "").startswith("remediate / retry / ")
    ]
    if not retry_jobs:
        return VerificationResult(False, "remediate_retry_skip_not_observed")
    if any(job.get("conclusion") != "skipped" for job in retry_jobs):
        return VerificationResult(False, "implement_job_executed")

    implement_jobs = [
        job
        for job in jobs
        if "implement" in str(job.get("name") or "").lower()
        and str(job.get("name") or "").startswith("remediate / retry")
    ]
    if any(job.get("conclusion") not in {None, "skipped"} for job in implement_jobs):
        return VerificationResult(False, "implement_job_executed")
    return VerificationResult(True)


def verify_escalation_marker(
    comments: list[dict],
    *,
    task_id: str,
    package_path: str,
    pr_number: int,
    source_run_id: int,
    source_head_sha: str,
) -> VerificationResult:
    prefix = f"{OPERATOR_ESCALATION_MARKER_PREFIX} `{task_id}`"
    source_run_token = f"run_id: `{source_run_id}`"
    source_head_token = f"head_sha: `{source_head_sha}`"
    matches = [
        comment
        for comment in comments
        if prefix in str(comment.get("body") or "")
        and source_run_token in str(comment.get("body") or "")
        and source_head_token in str(comment.get("body") or "")
    ]
    if len(matches) != 1:
        return VerificationResult(False, "missing_or_duplicate_escalation_marker")
    if (matches[0].get("author") or {}).get("login") not in TRUSTED_BOT_LOGINS:
        return VerificationResult(False, "untrusted_escalation_marker")
    body = str(matches[0].get("body") or "")
    required = [
        "should_retry: `false`",
        f"task_id: `{task_id}`",
        f"package_path: `{package_path}`",
        f"pr_number: `{pr_number}`",
        source_run_token,
        source_head_token,
    ]
    if any(token not in body for token in required):
        return VerificationResult(False, "escalation_metadata_incomplete")
    return VerificationResult(True)


def verify_carrier_state(
    *,
    pr: dict,
    task_id: str,
    package_path: str,
    issue_number: int,
    integration_branch: str,
    current_ref: str,
    comments: list[dict],
    source_run_id: int,
    source_head_sha: str,
) -> VerificationResult:
    if not SHA_RE.fullmatch(current_ref):
        return VerificationResult(False, "invalid_current_ref")
    if (
        pr.get("state") != "OPEN"
        or pr.get("headRefOid") != current_ref
        or pr.get("baseRefName") != integration_branch
    ):
        return VerificationResult(False, "stale_or_invalid_carrier")
    body = str(pr.get("body") or "")
    if f"Implements task `{task_id}`" not in body:
        return VerificationResult(False, "task_identity_mismatch")
    if f"Package path: `{package_path}`" not in body:
        return VerificationResult(False, "package_path_mismatch")
    if f"Closes #{issue_number}" not in body:
        return VerificationResult(False, "authority_issue_mismatch")
    return verify_escalation_marker(
        comments,
        task_id=task_id,
        package_path=package_path,
        pr_number=int(pr.get("number") or 0),
        source_run_id=source_run_id,
        source_head_sha=source_head_sha,
    )
