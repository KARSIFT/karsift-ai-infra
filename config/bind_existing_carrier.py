"""Fail-closed existing-carrier recovery identity for implement.yml (VOC-125)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from auto_advance_ownership import branch_name

SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def verify(expected_sha: str, current_sha: str) -> str:
    if not expected_sha or not SHA_RE.fullmatch(expected_sha):
        return "INVALID_EXPECTED_SHA"
    if not current_sha or not SHA_RE.fullmatch(current_sha):
        return "INVALID_CURRENT_SHA"
    if expected_sha.lower() != current_sha.lower():
        return "STALE"
    return "CURRENT"


REVIEW_BOT_LOGIN = "karsift-ai-infra-bot[bot]"
REVIEW_HEADER_PREFIX = "**Independent verification - bound to commit `"


@dataclass(frozen=True)
class BindResult:
    expected_head_sha: str
    expected_base_sha: str


@dataclass(frozen=True)
class BindFailure:
    code: str
    detail: str = ""


def _normalize_sha(value: str) -> str:
    return value.strip().lower()


def _valid_sha(value: str) -> bool:
    return bool(value and SHA_RE.fullmatch(value))


def _parse_closes_issue(body: str) -> str | None:
    matches = re.findall(r"^Closes #([1-9][0-9]*)\.?$", body, re.MULTILINE)
    if len(matches) != 1:
        return None
    return matches[0]


def _parse_package_path(body: str) -> str | None:
    matches = re.findall(r"^Package path: `([^`]+)`$", body, re.MULTILINE)
    if len(matches) != 1:
        return None
    return matches[0]


def _parse_task_line(body: str) -> str | None:
    match = re.search(
        r"^Implements task `([^`]+)` from `([^`]+)` \(`([^`]+)`\)\.",
        body,
        re.MULTILINE,
    )
    if not match:
        return None
    return match.group(1)


def validate_implement_pr_metadata(
    *,
    pr_title: str,
    pr_body: str,
    change_id: str,
    task_id: str,
    package_path: str,
    issue_number: str,
) -> bool:
    expected_title = f"{change_id}: {task_id}"
    if pr_title.strip() != expected_title:
        return False
    parsed_task = _parse_task_line(pr_body)
    if parsed_task != task_id:
        return False
    if f"from `{change_id}`" not in pr_body:
        return False
    if f"(`{package_path}`)" not in pr_body:
        return False
    closes = _parse_closes_issue(pr_body)
    if closes != str(issue_number):
        return False
    package_line = _parse_package_path(pr_body)
    if package_line != package_path:
        return False
    return True


def _review_comment_matches(
    body: str,
    *,
    head_sha: str,
    base_sha: str,
    task_id: str,
    package_path: str,
    issue_number: str,
) -> bool:
    header = f"{REVIEW_HEADER_PREFIX}{head_sha}`**"
    if not body.startswith(header):
        return False
    task_line = f"task_id: `{task_id}`"
    package_line = f"package_path: `{package_path}`"
    issue_line = f"authority_issue: `{issue_number}`"
    base_line = f"base_sha: `{base_sha}`"
    lines = body.split("\n")
    return (
        header in lines
        and task_line in lines
        and package_line in lines
        and issue_line in lines
        and base_line in lines
    )


def validate_review_comments(
    comments: list[dict[str, Any]],
    *,
    head_sha: str,
    base_sha: str,
    task_id: str,
    package_path: str,
    issue_number: str,
) -> str:
    """Return OK, ABSENT, or FOREIGN_REVIEW."""
    review_comments = []
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != REVIEW_BOT_LOGIN or user.get("type") != "Bot":
            continue
        body = str(comment.get("body") or "")
        if REVIEW_HEADER_PREFIX not in body:
            continue
        review_comments.append(body)

    if not review_comments:
        return "ABSENT"

    matching = [
        body
        for body in review_comments
        if _review_comment_matches(
            body,
            head_sha=head_sha,
            base_sha=base_sha,
            task_id=task_id,
            package_path=package_path,
            issue_number=issue_number,
        )
    ]
    if matching:
        return "OK"

    # Any bot review comment bound to this head but malformed is foreign.
    for body in review_comments:
        header_match = re.search(
            r"\*\*Independent verification - bound to commit `([0-9a-fA-F]{40})`\*\*",
            body,
        )
        if header_match and _normalize_sha(header_match.group(1)) == _normalize_sha(head_sha):
            return "FOREIGN_REVIEW"

    return "ABSENT"


def bind_existing_carrier(
    *,
    attempt: int,
    change_id: str,
    package_path: str,
    task_id: str,
    issue_number: str,
    integration_branch: str,
    repository: str,
    existing_pr_number: str,
    expected_head_sha: str,
    expected_base_sha: str,
    issue_state: str,
    remote_branch_head: str | None,
    has_remote_branch: bool,
    open_pr_number: str | None,
    pr_data: dict[str, Any] | None,
    review_comments: list[dict[str, Any]] | None,
) -> BindResult | BindFailure:
    existing_pr_number = existing_pr_number.strip()
    expected_head_sha = expected_head_sha.strip()
    expected_base_sha = expected_base_sha.strip()
    branch = branch_name(change_id, task_id)

    if attempt not in (1, 2):
        return BindFailure("INVALID_ATTEMPT")

    if attempt == 1:
        if existing_pr_number:
            return BindFailure("ATTEMPT1_WITH_EXISTING_PR_NUMBER")
        if expected_head_sha or expected_base_sha:
            return BindFailure("ATTEMPT1_WITH_SHA_INPUTS")
        if has_remote_branch or open_pr_number:
            return BindFailure("ATTEMPT1_EXISTING_CARRIER")
        return BindResult(expected_head_sha="", expected_base_sha="")

    # attempt == 2
    if issue_state != "OPEN":
        return BindFailure("CLOSED_TASK")

    supplied_head = expected_head_sha
    supplied_base = expected_base_sha
    has_supplied_shas = bool(supplied_head or supplied_base)

    if not existing_pr_number and not has_supplied_shas:
        return BindFailure("EMPTY_BINDING")

    if has_supplied_shas and (
        not _valid_sha(supplied_head) or not _valid_sha(supplied_base)
    ):
        return BindFailure("MALFORMED_SHA")

    if existing_pr_number:
        if not pr_data:
            return BindFailure("WRONG_PR")
        pr_repo = str(pr_data.get("repository") or repository)
        if pr_repo != repository:
            return BindFailure("WRONG_PR")
        if str(pr_data.get("number") or "") != existing_pr_number:
            return BindFailure("WRONG_PR")
        active_pr = pr_data
    elif has_supplied_shas:
        if not pr_data:
            return BindFailure("WRONG_PR")
        active_pr = pr_data
    else:
        return BindFailure("EMPTY_BINDING")

    state = str(active_pr.get("state") or "").upper()
    if state != "OPEN":
        return BindFailure("WRONG_PR")
    head_ref = str(active_pr.get("headRefName") or "")
    base_ref = str(active_pr.get("baseRefName") or "")
    if head_ref != branch:
        return BindFailure("WRONG_BRANCH")
    if base_ref != integration_branch:
        return BindFailure("WRONG_BRANCH")
    live_head = str(active_pr.get("headRefOid") or "")
    live_base = str(active_pr.get("baseRefOid") or "")
    if not _valid_sha(live_head) or not _valid_sha(live_base):
        return BindFailure("MALFORMED_SHA")
    if not validate_implement_pr_metadata(
        pr_title=str(active_pr.get("title") or ""),
        pr_body=str(active_pr.get("body") or ""),
        change_id=change_id,
        task_id=task_id,
        package_path=package_path,
        issue_number=issue_number,
    ):
        return BindFailure("METADATA_MISMATCH")

    if has_supplied_shas:
        if (
            _normalize_sha(supplied_head) != _normalize_sha(live_head)
            or _normalize_sha(supplied_base) != _normalize_sha(live_base)
        ):
            return BindFailure("SHA_PR_DISAGREEMENT")
        bound_head = supplied_head
        bound_base = supplied_base
    else:
        bound_head = live_head
        bound_base = live_base

    if not _valid_sha(bound_head) or not _valid_sha(bound_base):
        return BindFailure("MALFORMED_SHA")

    if not has_remote_branch:
        return BindFailure("STALE_HEAD")

    if remote_branch_head is None or not _valid_sha(remote_branch_head):
        return BindFailure("STALE_HEAD")

    head_state = verify(bound_head, remote_branch_head)
    if head_state != "CURRENT":
        return BindFailure("STALE_HEAD", head_state)

    if review_comments is not None:
        review_state = validate_review_comments(
            review_comments,
            head_sha=bound_head,
            base_sha=bound_base,
            task_id=task_id,
            package_path=package_path,
            issue_number=issue_number,
        )
        if review_state == "FOREIGN_REVIEW":
            return BindFailure("FOREIGN_REVIEW")

    return BindResult(
        expected_head_sha=bound_head,
        expected_base_sha=bound_base,
    )
