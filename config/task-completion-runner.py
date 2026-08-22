#!/usr/bin/env python3
"""GitHub adapter for publishing and validating task completion markers."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import subprocess
from typing import Any

from task_completion import (
    BOT_LOGIN,
    CompletionError,
    HEADER,
    marker_body,
    parse_pr_authority,
    validate_review_authority,
    validate_roster_authority,
    validate_comments,
)


def gh(*args: str, input_data: str | None = None) -> Any:
    env = os.environ.copy()
    result = subprocess.run(
        ["gh", *args],
        input=input_data,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode:
        raise CompletionError("GitHub metadata request failed")
    return json.loads(result.stdout) if result.stdout.strip() else None


def comments(repository: str, issue: int) -> list[dict[str, Any]]:
    pages = gh(
        "api",
        "--paginate",
        "--slurp",
        f"repos/{repository}/issues/{issue}/comments?per_page=100",
    )
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise CompletionError("invalid comment pagination")
    return [comment for page in pages for comment in page]


def timeline(repository: str, issue: int) -> list[dict[str, Any]]:
    pages = gh(
        "api",
        "--header",
        "Accept: application/vnd.github+json",
        "--paginate",
        "--slurp",
        f"repos/{repository}/issues/{issue}/timeline?per_page=100",
    )
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise CompletionError("invalid issue timeline pagination")
    return [event for page in pages for event in page]


def has_post_marker_close(events: list[dict[str, Any]], marker: str) -> bool:
    positions = [
        index
        for index, event in enumerate(events)
        if event.get("event") == "commented"
        and event.get("body") == marker
        and (event.get("actor") or {}).get("login") == BOT_LOGIN
        and (event.get("actor") or {}).get("type") == "Bot"
    ]
    if len(positions) != 1:
        return False
    return any(
        index > positions[0] and event.get("event") == "closed"
        for index, event in enumerate(events)
    )


def issue(repository: str, number: int) -> dict[str, Any]:
    value = gh("api", f"repos/{repository}/issues/{number}")
    if not isinstance(value, dict):
        raise CompletionError("invalid issue metadata")
    return value


def pull(repository: str, number: int) -> dict[str, Any]:
    value = gh("api", f"repos/{repository}/pulls/{number}")
    if not isinstance(value, dict):
        raise CompletionError("invalid pull request metadata")
    return value


def roster(repository: str, package_path: str, ref: str) -> Any:
    value = gh(
        "api",
        f"repos/{repository}/contents/{package_path}/.karsift/tasks.json?ref={ref}",
    )
    if (
        not isinstance(value, dict)
        or value.get("encoding") != "base64"
        or not isinstance(value.get("content"), str)
    ):
        raise CompletionError("invalid adopted task roster metadata")
    try:
        encoded = "".join(value["content"].split())
        raw = base64.b64decode(encoded, validate=True)
        return json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompletionError("invalid adopted task roster content") from exc


def expected_for(entry: dict[str, Any], package_path: str, repository: str) -> dict[str, str]:
    return {
        "repository": repository,
        "authority_issue": str(entry["issue"]),
        "package_path": package_path,
        "task_id": str(entry["task_id"]),
    }


def validate_entry(repository: str, package_path: str, entry: dict[str, Any]) -> dict[str, str]:
    issue_number = int(entry["issue"])
    task_issue = issue(repository, issue_number)
    task_comments = comments(repository, issue_number)
    candidate = [comment for comment in task_comments if str(comment.get("body", "")).startswith(HEADER)]
    if len(candidate) != 1:
        raise CompletionError("completion marker count is not exactly one")
    from task_completion import parse_marker

    parsed = parse_marker(str(candidate[0]["body"]))
    if parsed is None or not parsed.get("pr_number", "").isdigit():
        raise CompletionError("completion marker pull request is invalid")
    return validate_comments(
        task_comments,
        expected=expected_for(entry, package_path, repository),
        pull_request=pull(repository, int(parsed["pr_number"])),
        issue_state=str(task_issue.get("state", "")).upper(),
    )


def publish_completion(
    *,
    repository: str,
    pr_number: int,
    reviewed_head_sha: str,
    reviewed_base_sha: str,
) -> None:
    """Publish completion from live, merged caller-PR authority metadata."""
    pr = pull(repository, pr_number)
    if pr.get("state") != "closed" or not pr.get("merged_at"):
        raise CompletionError("caller pull request is not merged")
    if (pr.get("head") or {}).get("sha") != reviewed_head_sha:
        raise CompletionError("caller pull request head changed before completion")
    if not isinstance(pr.get("merge_commit_sha"), str):
        raise CompletionError("caller pull request lacks merge identity")

    # The REST response above is the authority source. In particular, do not
    # accept identity fields copied from the workflow event: a rerun after an
    # authorized PR-body correction must observe the corrected live body.
    identity = parse_pr_authority(str(pr.get("body") or ""))
    validate_review_authority(
        comments(repository, pr_number),
        reviewed_head_sha=reviewed_head_sha,
        reviewed_base_sha=reviewed_base_sha,
        identity=identity,
    )
    validate_roster_authority(
        roster(repository, identity["package_path"], reviewed_base_sha),
        identity,
    )
    issue_number = int(identity["authority_issue"])
    body = marker_body(
        {
            "repository": repository,
            **identity,
            "pr_number": pr_number,
            "reviewed_head_sha": reviewed_head_sha,
            "merge_commit_sha": pr["merge_commit_sha"],
            "merged_at": pr["merged_at"],
        }
    )
    prior = comments(repository, issue_number)
    markers = [
        comment for comment in prior if str(comment.get("body", "")).startswith(HEADER)
    ]
    marker_created = False
    if not markers:
        gh(
            "api",
            "--method",
            "POST",
            f"repos/{repository}/issues/{issue_number}/comments",
            "--input",
            "-",
            input_data=json.dumps({"body": body}),
        )
        marker_created = True
    elif len(markers) != 1 or markers[0].get("body") != body or (
        (markers[0].get("user") or {}).get("login") != BOT_LOGIN
        or (markers[0].get("user") or {}).get("type") != "Bot"
    ):
        raise CompletionError("completion marker is conflicting or ambiguous")

    current_issue = issue(repository, issue_number)
    issue_state = current_issue.get("state")
    if issue_state not in {"open", "closed"}:
        raise CompletionError("task issue state is invalid")
    if not marker_created and issue_state == "closed":
        if has_post_marker_close(timeline(repository, issue_number), body):
            return
    # GitHub may have applied the local `Closes #N` reference before a newly
    # created marker existed, or a prior attempt may have stopped between the
    # marker POST and its close wake-up. Reopen then close in either case so
    # issues:closed follows the authority evidence. A timeline-proven complete
    # retry is a mutation-free no-op.
    if issue_state == "closed":
        gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{repository}/issues/{issue_number}",
            "--input",
            "-",
            input_data=json.dumps({"state": "open"}),
        )
    gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{repository}/issues/{issue_number}",
        "--input",
        "-",
        input_data=json.dumps({"state": "closed"}),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    publish = sub.add_parser("publish")
    validate_task = sub.add_parser("validate-task")
    validate_roster = sub.add_parser("validate-roster")
    for command in (publish, validate_task, validate_roster):
        command.add_argument("--repository", required=True)
    publish.add_argument("--pr-number", required=True, type=int)
    publish.add_argument("--reviewed-head-sha", required=True)
    publish.add_argument("--reviewed-base-sha", required=True)
    for command in (validate_task, validate_roster):
        command.add_argument("--package-path", required=True)
    validate_task.add_argument("--roster", required=True)
    validate_task.add_argument("--issue-number", required=True, type=int)
    validate_roster.add_argument("--roster", required=True)
    args = parser.parse_args()

    if args.command == "publish":
        publish_completion(
            repository=args.repository,
            pr_number=args.pr_number,
            reviewed_head_sha=args.reviewed_head_sha,
            reviewed_base_sha=args.reviewed_base_sha,
        )
        return 0

    roster = json.loads(open(args.roster, encoding="utf-8").read())
    if not isinstance(roster, list) or not roster:
        raise CompletionError("roster is empty or malformed")
    issues = [entry.get("issue") for entry in roster]
    if len(set(issues)) != len(issues):
        raise CompletionError("roster contains duplicate issues")
    if args.command == "validate-task":
        matches = [entry for entry in roster if entry.get("issue") == args.issue_number]
        if len(matches) != 1:
            raise CompletionError("closed issue is not a unique roster task")
        validate_entry(args.repository, args.package_path, matches[0])
        return 0
    for entry in roster:
        validate_entry(args.repository, args.package_path, entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
