#!/usr/bin/env python3
"""Pure read-only proof policy for VOC-102 auto-advance live evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from auto_advance_ownership import (
    WAITING_MARKER_PREFIX,
    branch_name,
    derive_evidence_relative_path,
    is_valid_carrier_pr,
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TRUSTED_BOT_LOGINS = {"app/karsift-ai-infra-bot", "karsift-ai-infra-bot"}


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def verify_source_run(
    *,
    run: dict,
    repository: str,
    default_branch: str,
    predecessor_title: str,
    predecessor_closed_at: str,
) -> VerificationResult:
    if run.get("repository", {}).get("full_name") != repository:
        return VerificationResult(False, "wrong_repository")
    if run.get("name") != "pipeline" or run.get("path") != ".github/workflows/pipeline.yml":
        return VerificationResult(False, "wrong_workflow")
    if run.get("display_title") != predecessor_title:
        return VerificationResult(False, "wrong_source_issue")
    if run.get("event") != "issues":
        return VerificationResult(False, "wrong_event")
    if run.get("head_branch") != default_branch:
        return VerificationResult(False, "wrong_default_branch")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return VerificationResult(False, "source_run_not_successful")

    created = _parse_timestamp(run.get("created_at"))
    closed = _parse_timestamp(predecessor_closed_at)
    if created is None or closed is None:
        return VerificationResult(False, "missing_source_timestamp")
    delta = (created - closed).total_seconds()
    if delta < -30 or delta > 600:
        return VerificationResult(False, "source_run_not_bound_to_close")
    return VerificationResult(True)


def _matching_jobs(jobs: list[dict], suffix: str) -> list[dict]:
    return [job for job in jobs if str(job.get("name") or "").endswith(suffix)]


def verify_source_jobs(jobs: list[dict]) -> VerificationResult:
    required = {
        "auto-advance / advance": "success",
        "auto-advance / prepare-live-evidence": "success",
        "auto-advance / fail-closed": "skipped",
    }
    for suffix, conclusion in required.items():
        matches = _matching_jobs(jobs, suffix)
        if len(matches) != 1 or matches[0].get("conclusion") != conclusion:
            return VerificationResult(False, "source_job_mismatch")

    implement_jobs = [
        job
        for job in jobs
        if str(job.get("name") or "") == "auto-advance / implement"
        or str(job.get("name") or "").startswith("auto-advance / implement / ")
    ]
    if not implement_jobs:
        return VerificationResult(False, "implement_skip_not_observed")
    if any(job.get("conclusion") != "skipped" for job in implement_jobs):
        return VerificationResult(False, "implement_job_executed")
    return VerificationResult(True)


def verify_issue_state(state: str, expected: str, reason: str) -> VerificationResult:
    if state != expected:
        return VerificationResult(False, reason)
    return VerificationResult(True)


def verify_carrier_state(
    *,
    pr: dict,
    prs_on_branch: list[dict],
    comments: list[dict],
    change_id: str,
    task_id: str,
    package_path: str,
    issue_number: int,
    integration_branch: str,
    evidence_text: str | None,
    source_run_id: int,
    current_ref: str,
    risk: str,
) -> VerificationResult:
    branch = branch_name(change_id, task_id)
    expected_relative = derive_evidence_relative_path(task_id)
    if not SHA_RE.fullmatch(current_ref):
        return VerificationResult(False, "invalid_current_ref")
    if (
        pr.get("state") != "OPEN"
        or pr.get("isDraft") is not True
        or pr.get("headRefName") != branch
        or pr.get("baseRefName") != integration_branch
        or pr.get("headRefOid") != current_ref
    ):
        return VerificationResult(False, "stale_or_invalid_carrier")
    if (pr.get("author") or {}).get("login") not in TRUSTED_BOT_LOGINS:
        return VerificationResult(False, "untrusted_carrier_author")
    if len(prs_on_branch) != 1 or prs_on_branch[0].get("number") != pr.get("number"):
        return VerificationResult(False, "duplicate_carrier")
    if not is_valid_carrier_pr(
        pr_title=str(pr.get("title") or ""),
        pr_body=str(pr.get("body") or ""),
        change_id=change_id,
        task_id=task_id,
        package_path=package_path,
        issue_number=issue_number,
        evidence_relative_path=expected_relative,
        risk=risk,
    ):
        return VerificationResult(False, "untrusted_carrier")

    marker_comments = [
        comment
        for comment in comments
        if WAITING_MARKER_PREFIX in str(comment.get("body") or "")
    ]
    if len(marker_comments) != 1:
        return VerificationResult(False, "duplicate_or_missing_marker")
    if (marker_comments[0].get("author") or {}).get("login") not in TRUSTED_BOT_LOGINS:
        return VerificationResult(False, "untrusted_waiting_marker")
    if evidence_text is None:
        return VerificationResult(False, "missing_evidence_file")
    if f"source_run_id: `{source_run_id}`" not in evidence_text:
        return VerificationResult(False, "source_run_not_recorded")
    return VerificationResult(True)
