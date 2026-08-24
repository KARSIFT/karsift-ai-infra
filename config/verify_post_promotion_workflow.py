#!/usr/bin/env python3
"""Read-only proof that post-promotion workflows succeeded for a merge-result SHA."""

from __future__ import annotations

from dataclasses import dataclass
import re

POST_PROMOTION_WORKFLOW = ".github/workflows/deploy-production.yml"
POST_PROMOTION_WORKFLOW_FILE = "deploy-production.yml"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""


def verify_promotion_merged(pr: dict, *, repository: str, pr_number: int) -> VerificationResult:
    if pr.get("state") != "closed" or pr.get("merged") is not True:
        return VerificationResult(False, "promotion_pr_not_merged")
    if pr.get("number") != pr_number:
        return VerificationResult(False, "wrong_pr_number")
    sha = pr.get("merge_commit_sha")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        return VerificationResult(False, "missing_merge_commit")
    base = pr.get("base") or {}
    if base.get("ref") != "main":
        return VerificationResult(False, "wrong_base_branch")
    head = pr.get("head") or {}
    if head.get("ref") != "develop":
        return VerificationResult(False, "wrong_head_branch")
    if (head.get("repo") or {}).get("full_name") != repository:
        return VerificationResult(False, "wrong_head_repository")
    return VerificationResult(True)


def verify_post_promotion_run(
    runs: list[dict],
    *,
    repository: str,
    merge_sha: str,
) -> VerificationResult:
    if not SHA_RE.fullmatch(merge_sha):
        return VerificationResult(False, "invalid_merge_sha")
    matches = [
        run
        for run in runs
        if run.get("head_sha") == merge_sha
        and run.get("path") == POST_PROMOTION_WORKFLOW
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and (run.get("repository") or {}).get("full_name") == repository
    ]
    if len(matches) != 1:
        return VerificationResult(False, "post_promotion_run_missing_or_ambiguous")
    return VerificationResult(True)


def verify_current_ref(current_ref: str, expected_head_sha: str) -> VerificationResult:
    if not SHA_RE.fullmatch(current_ref):
        return VerificationResult(False, "invalid_current_ref")
    if current_ref != expected_head_sha:
        return VerificationResult(False, "current_ref_mismatch")
    return VerificationResult(True)
