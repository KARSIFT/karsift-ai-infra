#!/usr/bin/env python3
"""Pure exact-SHA branch-convergence policy for governed releases."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BranchSyncError(ValueError):
    """Branch convergence metadata is stale, ambiguous, or unsafe."""


@dataclass(frozen=True)
class BranchSyncPlan:
    action: str
    expected_integration_sha: str
    target_sha: str


def _sha(value: Any, error: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise BranchSyncError(error)
    return value


def _common_plan(
    *,
    integration_sha: str | None,
    production_sha: str,
    target_sha: str,
    expected_integration_sha: str | None,
    comparison: dict[str, Any] | None,
) -> BranchSyncPlan:
    production = _sha(production_sha, "production_ref_invalid")
    target = _sha(target_sha, "target_sha_invalid")
    if production != target:
        raise BranchSyncError("production_ref_moved")
    if integration_sha is None:
        return BranchSyncPlan("create", "", target)
    integration = _sha(integration_sha, "integration_ref_invalid")
    if integration == target:
        return BranchSyncPlan("noop", integration, target)
    if expected_integration_sha is not None and integration != _sha(
        expected_integration_sha, "expected_integration_sha_invalid"
    ):
        raise BranchSyncError("integration_ref_moved")
    if not isinstance(comparison, dict):
        raise BranchSyncError("integration_ancestry_missing")
    merge_base = comparison.get("merge_base_commit") or {}
    if merge_base.get("sha") != integration:
        raise BranchSyncError("integration_has_unique_commits")
    if comparison.get("behind_by") != 0 or not isinstance(
        comparison.get("ahead_by"), int
    ) or comparison["ahead_by"] <= 0:
        raise BranchSyncError("integration_ancestry_invalid")
    return BranchSyncPlan("update", integration, target)


def promotion_sync_plan(
    *,
    repository: str,
    pr_number: int,
    pull_request: dict[str, Any],
    merge_commit: dict[str, Any],
    integration_branch: str,
    production_branch: str,
    expected_head_sha: str,
    expected_base_sha: str,
    integration_sha: str | None,
    production_sha: str,
    comparison: dict[str, Any] | None,
) -> BranchSyncPlan:
    """Authorize only the exact merge result of the checked promotion PR."""
    head = pull_request.get("head") or {}
    base = pull_request.get("base") or {}
    if (
        pull_request.get("number") != pr_number
        or pull_request.get("state") != "closed"
        or not pull_request.get("merged_at")
        or head.get("ref") != integration_branch
        or base.get("ref") != production_branch
        or (head.get("repo") or {}).get("full_name") != repository
    ):
        raise BranchSyncError("promotion_pr_identity_invalid")
    checked_head = _sha(expected_head_sha, "expected_head_sha_invalid")
    checked_base = _sha(expected_base_sha, "expected_base_sha_invalid")
    if head.get("sha") != checked_head or base.get("sha") != checked_base:
        raise BranchSyncError("promotion_pr_revision_mismatch")
    target = _sha(
        pull_request.get("merge_commit_sha"), "promotion_merge_sha_invalid"
    )
    if merge_commit.get("sha") != target:
        raise BranchSyncError("promotion_merge_commit_mismatch")
    parents = [parent.get("sha") for parent in merge_commit.get("parents") or []]
    if parents != [checked_base, checked_head]:
        raise BranchSyncError("promotion_merge_parents_mismatch")
    return _common_plan(
        integration_sha=integration_sha,
        production_sha=production_sha,
        target_sha=target,
        expected_integration_sha=checked_head,
        comparison=comparison,
    )


def governed_main_only_sync_plan(
    *,
    repository: str,
    marker: dict[str, str],
    pull_request: dict[str, Any],
    merge_commit: dict[str, Any],
    integration_branch: str,
    production_branch: str,
    integration_sha: str | None,
    production_sha: str,
    comparison: dict[str, Any] | None,
) -> BranchSyncPlan:
    """Authorize a reviewed, completed production-target task back to integration."""
    head = pull_request.get("head") or {}
    base = pull_request.get("base") or {}
    if (
        pull_request.get("state") != "closed"
        or not pull_request.get("merged_at")
        or base.get("ref") != production_branch
        or not str(head.get("ref") or "").startswith("agent/")
        or (head.get("repo") or {}).get("full_name") != repository
    ):
        raise BranchSyncError("production_task_pr_identity_invalid")
    target = _sha(marker.get("merge_commit_sha"), "task_merge_sha_invalid")
    if (
        str(pull_request.get("number")) != marker.get("pr_number")
        or head.get("sha") != marker.get("reviewed_head_sha")
        or pull_request.get("merge_commit_sha") != target
        or merge_commit.get("sha") != target
    ):
        raise BranchSyncError("production_task_marker_mismatch")
    parents = [parent.get("sha") for parent in merge_commit.get("parents") or []]
    if parents != [base.get("sha"), marker.get("reviewed_head_sha")]:
        raise BranchSyncError("production_task_merge_parents_mismatch")
    return _common_plan(
        integration_sha=integration_sha,
        production_sha=production_sha,
        target_sha=target,
        expected_integration_sha=None,
        comparison=comparison,
    )
