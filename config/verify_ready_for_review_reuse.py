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


def verify_source_pr(
    *,
    pr: dict,
    repository: str,
    pr_number: int,
    expected_head_sha: str,
    expected_base_sha: str,
    expected_head_ref: str,
) -> VerificationResult:
    if int(pr.get("number") or 0) != pr_number:
        return VerificationResult(False, "source_pr_number_mismatch")
    # A successful auto-merge job is necessary, but only the authenticated
    # pull-request object proves that GitHub actually recorded the merge.
    if (
        pr.get("state") != "closed"
        or pr.get("merged") is not True
        or not isinstance(pr.get("merged_at"), str)
        or not pr.get("merged_at")
    ):
        return VerificationResult(False, "source_pr_not_merged")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    if str((head.get("repo") or {}).get("full_name") or "") != repository:
        return VerificationResult(False, "source_pr_head_repository_mismatch")
    if str((base.get("repo") or {}).get("full_name") or "") != repository:
        return VerificationResult(False, "source_pr_base_repository_mismatch")
    if str(head.get("sha") or "").lower() != expected_head_sha.lower():
        return VerificationResult(False, "source_pr_head_mismatch")
    if str(head.get("ref") or "") != expected_head_ref:
        return VerificationResult(False, "source_pr_head_ref_mismatch")
    if str(base.get("sha") or "").lower() != expected_base_sha.lower():
        return VerificationResult(False, "source_pr_base_mismatch")
    return VerificationResult(True)


def verify_ready_run(
    *,
    run: dict,
    repository: str,
    pr_number: int,
    expected_head_sha: str,
    expected_base_sha: str,
    expected_head_ref: str,
    source_pr: dict,
) -> VerificationResult:
    source_binding = verify_source_pr(
        pr=source_pr,
        repository=repository,
        pr_number=pr_number,
        expected_head_sha=expected_head_sha,
        expected_base_sha=expected_base_sha,
        expected_head_ref=expected_head_ref,
    )
    if not source_binding.ok:
        return source_binding
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
    if pull_requests:
        matches = [
            pr
            for pr in pull_requests
            if pr.get("number") == pr_number
            and str((pr.get("base") or {}).get("sha") or "").lower()
            == expected_base_sha.lower()
            and str((pr.get("head") or {}).get("sha") or "").lower()
            == expected_head_sha.lower()
        ]
        if len(matches) != 1:
            return VerificationResult(False, "wrong_pull_request")
    # GitHub clears workflow-run PR associations after some PRs close. The
    # exact REST PR object above is the authenticated fallback binding in that
    # state; it must still match repository, number, base, head, and head ref.
    return VerificationResult(True)


def verify_ready_jobs(
    *,
    jobs: list[dict],
    head_ref: str,
) -> VerificationResult:
    ci_matches = [job for job in jobs if _job_name(job) == REQUIRED_CI_JOB]
    if head_ref.startswith("agent/"):
        publisher_name = "review"
    elif head_ref.startswith("plan/"):
        publisher_name = "plan-review"
    else:
        return VerificationResult(False, "unsupported_head_ref")
    publisher_matches = [job for job in jobs if _job_name(job) == publisher_name]
    ci = ci_matches[0] if len(ci_matches) == 1 else None
    publisher = publisher_matches[0] if len(publisher_matches) == 1 else None
    merge_report = [
        job for job in jobs if _job_name(job) == "merge-gate / report-status"
    ]
    merge_auto = [job for job in jobs if _job_name(job) == "merge-gate / auto-merge"]
    if ci is None or _job_conclusion(ci) != "success":
        return VerificationResult(False, "ci_reuse_context_not_successful")
    ci_steps = ci.get("steps") or []
    if not isinstance(ci_steps, list):
        return VerificationResult(False, "ci_steps_malformed")
    reuse_markers = [
        step
        for step in ci_steps
        if isinstance(step, dict)
        and str(step.get("name") or "") == "Record exact-SHA CI evidence reuse"
    ]
    full_checks = [
        step
        for step in ci_steps
        if isinstance(step, dict)
        and str(step.get("name") or "") == "Detect and run pnpm checks"
    ]
    if len(reuse_markers) != 1 or _job_conclusion(reuse_markers[0]) != "success":
        return VerificationResult(False, "ci_reuse_marker_not_successful")
    if len(full_checks) != 1 or _job_conclusion(full_checks[0]) != "skipped":
        return VerificationResult(False, "ci_full_validation_not_skipped")
    if publisher is None or _job_conclusion(publisher) != "skipped":
        return VerificationResult(False, "review_not_skipped")
    if len(merge_report) != 1 or _job_conclusion(merge_report[0]) != "success":
        return VerificationResult(False, "merge_gate_not_successful")
    # report-status succeeds after posting either a passing or blocked status;
    # only a successful auto-merge job proves that the optimized transition
    # actually cleared merge-gate and completed the intended outcome.
    if len(merge_auto) != 1 or _job_conclusion(merge_auto[0]) != "success":
        return VerificationResult(False, "merge_gate_auto_not_successful")
    reuse_jobs = [
        job
        for job in jobs
        if _job_name(job)
        == "ready-for-review-reuse / decide (ready_for_review)"
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
    expected_base_sha: str,
    expected_head_ref: str,
    prior_run_id: int,
    ready_run_id: int,
    source_pr: dict,
) -> VerificationResult:
    source_binding = verify_source_pr(
        pr=source_pr,
        repository=repository,
        pr_number=pr_number,
        expected_head_sha=expected_head_sha,
        expected_base_sha=expected_base_sha,
        expected_head_ref=expected_head_ref,
    )
    if not source_binding.ok:
        return source_binding
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
    if pull_requests:
        matches = [
            pr
            for pr in pull_requests
            if pr.get("number") == pr_number
            and str((pr.get("base") or {}).get("sha") or "").lower()
            == expected_base_sha.lower()
            and str((pr.get("head") or {}).get("sha") or "").lower()
            == expected_head_sha.lower()
        ]
        if len(matches) != 1:
            return VerificationResult(False, "prior_wrong_pull_request")
    return VerificationResult(True)


def verify_prior_jobs(
    *,
    jobs: list[dict],
    head_ref: str,
) -> VerificationResult:
    ci_matches = [job for job in jobs if _job_name(job) == REQUIRED_CI_JOB]
    if head_ref.startswith("agent/"):
        publisher_name = AGENT_PUBLISHER_JOB
    elif head_ref.startswith("plan/"):
        publisher_name = PLAN_PUBLISHER_JOB
    else:
        return VerificationResult(False, "unsupported_head_ref")
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
