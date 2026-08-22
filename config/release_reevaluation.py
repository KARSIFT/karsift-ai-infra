#!/usr/bin/env python3
"""Classify whether a terminal check event should wake cheap release evaluation."""

from __future__ import annotations

from typing import Any


def should_reevaluate(
    event: dict[str, Any], *, repository: str, integration_branch: str, production_branch: str
) -> bool:
    check = event.get("check_run")
    repo = event.get("repository") or {}
    if not isinstance(check, dict) or check.get("status") != "completed":
        return False
    if repo.get("full_name") != repository:
        return False
    pull_requests = check.get("pull_requests")
    if not isinstance(pull_requests, list) or len(pull_requests) != 1:
        return False
    pr = pull_requests[0]
    return (
        (pr.get("head") or {}).get("ref") == integration_branch
        and (pr.get("base") or {}).get("ref") == production_branch
        and (pr.get("head") or {}).get("sha") == check.get("head_sha")
    )
