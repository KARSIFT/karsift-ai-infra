#!/usr/bin/env python3
"""Classify whether a terminal check event should wake cheap release evaluation."""

from __future__ import annotations

from typing import Any


def should_reevaluate(
    event: dict[str, Any], *, repository: str, integration_branch: str,
    production_branch: str, promotion_pr: dict[str, Any] | None = None
) -> bool:
    check = event.get("check_run")
    repo = event.get("repository") or {}
    if not isinstance(check, dict) or check.get("status") != "completed":
        return False
    if repo.get("full_name") != repository:
        return False
    pull_requests = check.get("pull_requests")
    if not isinstance(pull_requests, list):
        return False
    if len(pull_requests) == 1:
        pr = pull_requests[0]
        return (
            (pr.get("head") or {}).get("ref") == integration_branch
            and (pr.get("base") or {}).get("ref") == production_branch
            and (pr.get("head") or {}).get("sha") == check.get("head_sha")
        )
    if pull_requests or not isinstance(promotion_pr, dict):
        return False
    return (
        promotion_pr.get("state") == "OPEN"
        and promotion_pr.get("headRefName") == integration_branch
        and promotion_pr.get("baseRefName") == production_branch
        and promotion_pr.get("headRefOid") == check.get("head_sha")
    )
