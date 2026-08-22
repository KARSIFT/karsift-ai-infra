#!/usr/bin/env python3
"""Strict caller-merge task-completion marker contract."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any


HEADER = "**KARSIFT task completion v1**"
BOT_LOGIN = "karsift-ai-infra-bot[bot]"
PACKAGE_RE = re.compile(
    r"^specs/changes/([A-Z][A-Z0-9]*-[0-9]+)-[a-z0-9][a-z0-9-]*$"
)
TASK_RE = re.compile(r"^([A-Z][A-Z0-9]*-[0-9]+)-T[0-9]+[a-z]?$")
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


def parse_pr_authority(body: str) -> dict[str, str]:
    """Extract one canonical task identity from the current caller PR body."""
    tasks = re.findall(r"Implements task `([^`\r\n]+)`", body)
    packages = re.findall(r"Package path: `([^`\r\n]+)`", body)
    issues = re.findall(r"Closes #([0-9]+)\b", body)
    if len(tasks) != 1 or len(packages) != 1 or len(issues) != 1:
        raise CompletionError("caller pull request has ambiguous completion identity")

    task_match = TASK_RE.fullmatch(tasks[0])
    package_match = PACKAGE_RE.fullmatch(packages[0])
    issue_number = int(issues[0])
    if task_match is None or package_match is None or issue_number <= 0:
        raise CompletionError("caller pull request has invalid completion identity")
    if task_match.group(1) != package_match.group(1):
        raise CompletionError("caller pull request task and package do not match")
    return {
        "authority_issue": str(issue_number),
        "package_path": packages[0],
        "task_id": tasks[0],
    }


def validate_review_authority(
    comments: list[dict[str, Any]],
    *,
    reviewed_head_sha: str,
    identity: dict[str, str],
) -> None:
    """Bind live PR identity to the newest App-signed exact-head PASS review."""
    header = f"**Independent verification - bound to commit `{reviewed_head_sha}`**"
    required = (
        f"task_id: `{identity['task_id']}`",
        f"package_path: `{identity['package_path']}`",
        f"authority_issue: `{identity['authority_issue']}`",
    )
    candidates: list[dict[str, Any]] = []
    for comment in comments:
        body = comment.get("body")
        user = comment.get("user") or {}
        if (
            not isinstance(body, str)
            or not body.startswith(header)
            or user.get("login") != BOT_LOGIN
            or user.get("type") != "Bot"
        ):
            continue
        lines = body.splitlines()
        if all(lines.count(line) == 1 for line in (header, *required)):
            candidates.append(comment)
    if not candidates:
        raise CompletionError("live completion identity lacks an App-signed exact-head review")

    selected = max(
        candidates,
        key=lambda comment: (str(comment.get("created_at") or ""), int(comment.get("id") or 0)),
    )
    final_line = next(
        (line.strip() for line in reversed(str(selected["body"]).splitlines()) if line.strip()),
        "",
    )
    if final_line not in {
        "VERDICT: PASS",
        "VERDICT: PASS WITH NON-BLOCKING FINDINGS",
    }:
        raise CompletionError("newest live-identity review is not a PASS verdict")


def validate_roster_authority(roster: Any, identity: dict[str, str]) -> None:
    """Require the live identity to name one adopted task-roster entry."""
    if not isinstance(roster, list) or not roster:
        raise CompletionError("adopted task roster is empty or malformed")
    entries: list[tuple[str, int]] = []
    for entry in roster:
        if not isinstance(entry, dict):
            raise CompletionError("adopted task roster is malformed")
        task_id = entry.get("task_id")
        issue_number = entry.get("issue")
        if (
            not isinstance(task_id, str)
            or TASK_RE.fullmatch(task_id) is None
            or not isinstance(issue_number, int)
            or isinstance(issue_number, bool)
            or issue_number <= 0
        ):
            raise CompletionError("adopted task roster is malformed")
        entries.append((task_id, issue_number))
    task_ids = [task_id for task_id, _ in entries]
    issue_numbers = [issue_number for _, issue_number in entries]
    if (
        len(task_ids) != len(set(task_ids))
        or len(issue_numbers) != len(set(issue_numbers))
    ):
        raise CompletionError("adopted task roster contains duplicate entries")
    expected = (identity["task_id"], int(identity["authority_issue"]))
    if entries.count(expected) != 1:
        raise CompletionError("live completion identity does not match the adopted task roster")


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
