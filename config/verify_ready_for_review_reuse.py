#!/usr/bin/env python3
"""Pure read-only proof policy for VOC-104 ready_for_review reuse."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ready_for_review_reuse import (
    AGENT_PUBLISHER_JOB,
    PLAN_PUBLISHER_JOB,
    REQUIRED_CI_JOB,
    _job_conclusion,
    _job_name,
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""


def verify_ready_run(
    *,
    run: dict,
    repository: str,
    pr_number: int,
    expected_head_sha: str,
    expected_head_ref: str,
) -> VerificationResult:
    if run.get("repository", {}).get("full_name") != repository:
        return VerificationResult(False, "wrong_repository")
    if run.get("name") != "pipeline" or run.get("path") != ".github/workflows/pipeline.yml":
        return VerificationResult(False, "wrong_workflow")
    if run.get("event") != "pull_request":
        return VerificationResult(False, "wrong_event")
    if str(run.get("head_sha") or "").lower() != expected_head_sha.lower():
        return VerificationResult(False, "head_sha_mismatch")
    if str(run.get("head_branch") or "") != expected_head_ref:
        return VerificationResult(False, "head_branch_mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return VerificationResult(False, "ready_run_not_successful")
    pull_requests = run.get("pull_requests") or []
    if pull_requests and not any(pr.get("number") == pr_number for pr in pull_requests):
        return VerificationResult(False, "wrong_pull_request")
    return VerificationResult(True)


def verify_ready_jobs(
    *,
    jobs: list[dict],
    head_ref: str,
) -> VerificationResult:
    ci_matches = [job for job in jobs if _job_name(job) == REQUIRED_CI_JOB]
    publisher_name = (
        AGENT_PUBLISHER_JOB if head_ref.startswith("agent/") else PLAN_PUBLISHER_JOB
    )
    publisher_matches = [job for job in jobs if _job_name(job) == publisher_name]
    ci = ci_matches[0] if len(ci_matches) == 1 else None
    publisher = publisher_matches[0] if len(publisher_matches) == 1 else None
    merge_gate = [job for job in jobs if _job_name(job).startswith("merge-gate")]
    if ci is None or _job_conclusion(ci) != "skipped":
        return VerificationResult(False, "ci_not_skipped")
    if publisher is None or _job_conclusion(publisher) != "skipped":
        return VerificationResult(False, "review_not_skipped")
    if not merge_gate or any(_job_conclusion(job) != "success" for job in merge_gate):
        return VerificationResult(False, "merge_gate_not_successful")
    reuse_jobs = [
        job
        for job in jobs
        if "ready-for-review-reuse" in _job_name(job) and _job_name(job).endswith("/ decide")
    ]
    if len(reuse_jobs) != 1 or _job_conclusion(reuse_jobs[0]) != "success":
        return VerificationResult(False, "reuse_decision_job_missing")
    return VerificationResult(True)


def verify_prior_run(
    *,
    run: dict,
    repository: str,
    pr_number: int,
    expected_head_sha: str,
    expected_head_ref: str,
    prior_run_id: int,
    ready_run_id: int,
) -> VerificationResult:
    if int(run.get("id") or 0) != prior_run_id:
        return VerificationResult(False, "prior_run_id_mismatch")
    if prior_run_id >= ready_run_id:
        return VerificationResult(False, "prior_not_before_ready_run")
    if run.get("repository", {}).get("full_name") != repository:
        return VerificationResult(False, "prior_wrong_repository")
    if run.get("name") != "pipeline" or run.get("path") != ".github/workflows/pipeline.yml":
        return VerificationResult(False, "prior_wrong_workflow")
    if run.get("event") != "pull_request":
        return VerificationResult(False, "prior_wrong_event")
    if str(run.get("head_sha") or "").lower() != expected_head_sha.lower():
        return VerificationResult(False, "prior_head_sha_mismatch")
    if str(run.get("head_branch") or "") != expected_head_ref:
        return VerificationResult(False, "prior_head_branch_mismatch")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return VerificationResult(False, "prior_run_not_successful")
    pull_requests = run.get("pull_requests") or []
    if pull_requests and not any(pr.get("number") == pr_number for pr in pull_requests):
        return VerificationResult(False, "prior_wrong_pull_request")
    return VerificationResult(True)


def verify_prior_jobs(
    *,
    jobs: list[dict],
    head_ref: str,
) -> VerificationResult:
    ci_matches = [job for job in jobs if _job_name(job) == REQUIRED_CI_JOB]
    publisher_name = (
        AGENT_PUBLISHER_JOB if head_ref.startswith("agent/") else PLAN_PUBLISHER_JOB
    )
    publisher_matches = [job for job in jobs if _job_name(job) == publisher_name]
    ci = ci_matches[0] if len(ci_matches) == 1 else None
    publisher = publisher_matches[0] if len(publisher_matches) == 1 else None
    if ci is None or _job_conclusion(ci) != "success":
        return VerificationResult(False, "prior_ci_not_successful")
    if publisher is None or _job_conclusion(publisher) != "success":
        return VerificationResult(False, "prior_review_not_successful")
    return VerificationResult(True)


def verify_current_ref(*, current_ref: str, expected_head_sha: str) -> VerificationResult:
    if not SHA_RE.fullmatch(current_ref) or current_ref.lower() != expected_head_sha.lower():
        return VerificationResult(False, "current_ref_mismatch")
    return VerificationResult(True)
