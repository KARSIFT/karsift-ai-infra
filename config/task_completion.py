#!/usr/bin/env python3
"""Strict caller-merge task-completion marker contract."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any


HEADER = "**KARSIFT task completion v1**"
BOT_LOGIN = "karsift-ai-infra-bot[bot]"
FIELDS = (
    "repository",
    "authority_issue",
    "package_path",
    "task_id",
    "pr_number",
    "reviewed_head_sha",
    "merge_commit_sha",
    "merged_at",
)


class CompletionError(ValueError):
    """Completion evidence is missing, ambiguous, forged, or stale."""


def marker_body(record: dict[str, Any]) -> str:
    missing = [field for field in FIELDS if field not in record]
    if missing:
        raise CompletionError(f"completion record misses {', '.join(missing)}")
    return "\n".join(
        [HEADER, *(f"{field}: `{record[field]}`" for field in FIELDS)]
    )


def parse_marker(body: str) -> dict[str, str] | None:
    lines = body.splitlines()
    if not lines or lines[0] != HEADER:
        return None
    if len(lines) != len(FIELDS) + 1:
        raise CompletionError("completion marker has an unexpected shape")
    result: dict[str, str] = {}
    for field, line in zip(FIELDS, lines[1:], strict=True):
        match = re.fullmatch(rf"{re.escape(field)}: `([^`]+)`", line)
        if not match:
            raise CompletionError(f"completion marker has invalid {field}")
        result[field] = match.group(1)
    return result


def _iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompletionError("completion time is malformed") from exc


def validate_comments(
    comments: list[dict[str, Any]],
    *,
    expected: dict[str, str],
    pull_request: dict[str, Any],
    issue_state: str,
) -> dict[str, str]:
    markers: list[tuple[dict[str, str], dict[str, Any]]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or not body.startswith(HEADER):
            continue
        user = comment.get("user") or {}
        if user.get("login") != BOT_LOGIN or user.get("type") != "Bot":
            raise CompletionError("completion marker is not App-authored")
        parsed = parse_marker(body)
        if parsed is not None:
            markers.append((parsed, comment))
    if len(markers) != 1:
        raise CompletionError("completion marker count is not exactly one")
    marker, comment = markers[0]
    for field, value in expected.items():
        if marker.get(field) != str(value):
            raise CompletionError(f"completion marker mismatches {field}")
    if issue_state != "CLOSED":
        raise CompletionError("task issue is not closed")
    if pull_request.get("state") != "closed" or not pull_request.get("merged_at"):
        raise CompletionError("caller pull request is not merged")
    pr_number = pull_request.get("number")
    if str(pr_number) != marker["pr_number"]:
        raise CompletionError("completion marker references another pull request")
    head = (pull_request.get("head") or {}).get("sha")
    if head != marker["reviewed_head_sha"]:
        raise CompletionError("caller pull request head is stale")
    if pull_request.get("merge_commit_sha") != marker["merge_commit_sha"]:
        raise CompletionError("caller merge identity mismatches marker")
    if pull_request.get("merged_at") != marker["merged_at"]:
        raise CompletionError("caller merge time mismatches marker")
    created_at = comment.get("created_at")
    if not isinstance(created_at, str) or _iso(created_at) < _iso(marker["merged_at"]):
        raise CompletionError("completion marker predates merge proof")
    return marker

