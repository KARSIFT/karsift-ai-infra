#!/usr/bin/env python3
"""Validate and apply exact-SHA integration convergence without raw errors."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
from typing import Any

from branch_sync import (
    BranchSyncError,
    BranchSyncPlan,
    governed_main_only_sync_plan,
    promotion_sync_plan,
)
from task_completion import (
    BOT_LOGIN,
    HEADER,
    parse_marker,
    validate_comments,
    validate_roster_authority,
)


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def gh_json(endpoint: str) -> Any:
    result = _run(["gh", "api", endpoint])
    if result.returncode:
        raise BranchSyncError("github_metadata_read_failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BranchSyncError("github_metadata_invalid") from exc


def paginated_comments(repository: str, issue_number: int) -> list[dict[str, Any]]:
    result = _run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/issues/{issue_number}/comments?per_page=100",
        ]
    )
    if result.returncode:
        raise BranchSyncError("github_comments_read_failed")
    try:
        pages = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BranchSyncError("github_comments_invalid") from exc
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise BranchSyncError("github_comments_invalid")
    return [comment for page in pages for comment in page]


def ref_sha(repository: str, branch: str) -> str | None:
    result = _run(
        ["gh", "api", "--include", f"repos/{repository}/git/ref/heads/{branch}"]
    )
    status = re.search(r"^HTTP/\S+\s+(\d{3})\b", result.stdout, re.MULTILINE)
    if status is None:
        raise BranchSyncError("branch_ref_read_failed")
    if status.group(1) == "404":
        return None
    if status.group(1) != "200" or result.returncode:
        raise BranchSyncError("branch_ref_read_failed")
    body = result.stdout.split("\n\n", 1)
    if len(body) != 2:
        body = result.stdout.split("\r\n\r\n", 1)
    if len(body) != 2:
        raise BranchSyncError("branch_ref_invalid")
    try:
        value = json.loads(body[1])
        return str(value["object"]["sha"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BranchSyncError("branch_ref_invalid") from exc


def compare(repository: str, base: str | None, head: str) -> dict[str, Any] | None:
    if base is None or base == head:
        return None
    value = gh_json(f"repos/{repository}/compare/{base}...{head}")
    if not isinstance(value, dict):
        raise BranchSyncError("branch_compare_invalid")
    return value


def roster(repository: str, package_path: str, ref: str) -> Any:
    value = gh_json(
        f"repos/{repository}/contents/{package_path}/.karsift/tasks.json?ref={ref}"
    )
    if (
        not isinstance(value, dict)
        or value.get("encoding") != "base64"
        or not isinstance(value.get("content"), str)
    ):
        raise BranchSyncError("adopted_roster_invalid")
    try:
        encoded = "".join(value["content"].split())
        return json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BranchSyncError("adopted_roster_invalid") from exc


CompletionMetadata = tuple[
    dict[str, Any], list[dict[str, Any]], dict[str, str], dict[str, Any]
]


def completion_metadata(repository: str, issue_number: int) -> CompletionMetadata:
    """Resolve enough App-authored identity to classify the carrier branch."""
    issue = gh_json(f"repos/{repository}/issues/{issue_number}")
    comments = paginated_comments(repository, issue_number)
    candidates = [
        comment
        for comment in comments
        if str(comment.get("body") or "").startswith(HEADER)
    ]
    if not candidates:
        raise BranchSyncError("completion_marker_missing")
    if len(candidates) != 1 or (candidates[0].get("user") or {}).get("login") != BOT_LOGIN:
        raise BranchSyncError("completion_marker_invalid")
    try:
        parsed = parse_marker(str(candidates[0]["body"]))
    except ValueError as exc:
        raise BranchSyncError("completion_marker_invalid") from exc
    if parsed is None or parsed.get("repository") != repository or parsed.get(
        "authority_issue"
    ) != str(issue_number):
        raise BranchSyncError("completion_marker_invalid")
    try:
        pr_number = int(parsed["pr_number"])
    except (KeyError, ValueError) as exc:
        raise BranchSyncError("completion_marker_invalid") from exc
    pull_request = gh_json(f"repos/{repository}/pulls/{pr_number}")
    if not isinstance(pull_request, dict) or not isinstance(issue, dict):
        raise BranchSyncError("completion_metadata_invalid")
    return issue, comments, parsed, pull_request


def governed_marker(
    repository: str,
    issue_number: int,
    production_sha: str,
    *,
    metadata: CompletionMetadata | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Fully validate an eligible production-target task and adopted roster."""
    issue, comments, parsed, pull_request = metadata or completion_metadata(
        repository, issue_number
    )
    expected = {
        field: parsed[field]
        for field in ("repository", "authority_issue", "package_path", "task_id")
    }
    try:
        marker = validate_comments(
            comments,
            expected=expected,
            pull_request=pull_request,
            issue_state=str(issue.get("state") or "").upper(),
        )
        validate_roster_authority(
            roster(repository, marker["package_path"], production_sha), marker
        )
    except ValueError as exc:
        raise BranchSyncError("completion_authority_invalid") from exc
    return marker, pull_request


def resolve(args: argparse.Namespace) -> BranchSyncPlan:
    integration = ref_sha(args.repository, args.integration_branch)
    production = ref_sha(args.repository, args.production_branch)
    if production is None:
        raise BranchSyncError("production_ref_missing")
    if args.mode == "promotion":
        pull_request = gh_json(f"repos/{args.repository}/pulls/{args.pr_number}")
        if not isinstance(pull_request, dict):
            raise BranchSyncError("promotion_pr_metadata_invalid")
        target = pull_request.get("merge_commit_sha")
        merge_commit = gh_json(f"repos/{args.repository}/commits/{target}")
        return promotion_sync_plan(
            repository=args.repository,
            pr_number=args.pr_number,
            pull_request=pull_request,
            merge_commit=merge_commit,
            integration_branch=args.integration_branch,
            production_branch=args.production_branch,
            expected_head_sha=args.expected_head_sha,
            expected_base_sha=args.expected_base_sha,
            integration_sha=integration,
            production_sha=production,
            comparison=compare(args.repository, integration, production),
        )
    try:
        metadata = completion_metadata(
            args.repository, args.authority_issue_number
        )
    except BranchSyncError as exc:
        if args.skip_ineligible and str(exc) == "completion_marker_missing":
            return BranchSyncPlan("ineligible", "", production)
        raise
    pull_request = metadata[3]
    if (pull_request.get("base") or {}).get("ref") != args.production_branch:
        if args.skip_ineligible:
            return BranchSyncPlan("ineligible", "", production)
        raise BranchSyncError("production_task_pr_identity_invalid")
    marker, pull_request = governed_marker(
        args.repository,
        args.authority_issue_number,
        production,
        metadata=metadata,
    )
    merge_commit = gh_json(
        f"repos/{args.repository}/commits/{marker['merge_commit_sha']}"
    )
    return governed_main_only_sync_plan(
        repository=args.repository,
        marker=marker,
        pull_request=pull_request,
        merge_commit=merge_commit,
        integration_branch=args.integration_branch,
        production_branch=args.production_branch,
        integration_sha=integration,
        production_sha=production,
        comparison=compare(args.repository, integration, production),
    )


def git_env() -> dict[str, str]:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise BranchSyncError("app_token_missing")
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def git(*args: str, env: dict[str, str]) -> str:
    result = _run(["git", *args], env=env)
    if result.returncode:
        raise BranchSyncError("git_sync_command_failed")
    return result.stdout.strip()


def apply(plan: BranchSyncPlan, args: argparse.Namespace) -> bool:
    if plan.action in ("noop", "ineligible"):
        return True
    env = git_env()
    git(
        "fetch",
        "--no-tags",
        "origin",
        f"+refs/heads/{args.production_branch}:refs/remotes/origin/{args.production_branch}",
        env=env,
    )
    if plan.expected_integration_sha:
        git(
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/heads/{args.integration_branch}:refs/remotes/origin/{args.integration_branch}",
            env=env,
        )
    if git("rev-parse", f"origin/{args.production_branch}", env=env) != plan.target_sha:
        raise BranchSyncError("production_ref_moved_before_push")
    if plan.expected_integration_sha and git(
        "rev-parse", f"origin/{args.integration_branch}", env=env
    ) != plan.expected_integration_sha:
        raise BranchSyncError("integration_ref_moved_before_push")
    tree_base = plan.expected_integration_sha or getattr(
        args, "expected_head_sha", ""
    )
    equivalent = False
    if tree_base:
        diff = _run(
            ["git", "diff", "--quiet", tree_base, plan.target_sha], env=env
        )
        if diff.returncode not in (0, 1):
            raise BranchSyncError("tree_equivalence_check_failed")
        equivalent = diff.returncode == 0
    # Re-read every GitHub boundary immediately before the lease-protected push.
    if resolve(args) != plan:
        raise BranchSyncError("branch_state_changed_before_push")
    lease = f"refs/heads/{args.integration_branch}:{plan.expected_integration_sha}"
    git(
        "push",
        f"--force-with-lease={lease}",
        "origin",
        f"{plan.target_sha}:refs/heads/{args.integration_branch}",
        env=env,
    )
    final = resolve(args)
    if final.action != "noop" or final.target_sha != plan.target_sha:
        raise BranchSyncError("branch_sync_postcondition_failed")
    return equivalent


def write_output(path: str, *, plan: BranchSyncPlan, tree_equivalent: bool) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as output:
        output.write(f"initial_action={plan.action}\n")
        output.write(f"target_sha={plan.target_sha}\n")
        output.write(f"tree_equivalent={'true' if tree_equivalent else 'false'}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("promotion", "governed-main-only"), required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--integration-branch", required=True)
    parser.add_argument("--production-branch", required=True)
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--expected-head-sha")
    parser.add_argument("--expected-base-sha")
    parser.add_argument("--authority-issue-number", type=int)
    parser.add_argument("--skip-ineligible", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()
    if args.mode == "promotion" and (
        not args.pr_number or not args.expected_head_sha or not args.expected_base_sha
    ):
        raise BranchSyncError("promotion_inputs_missing")
    if args.mode == "governed-main-only" and not args.authority_issue_number:
        raise BranchSyncError("authority_issue_missing")
    plan = resolve(args)
    equivalent = plan.action in ("noop", "ineligible")
    if args.apply:
        equivalent = apply(plan, args)
    write_output(args.output, plan=plan, tree_equivalent=equivalent)
    print(
        "branch-sync: ok "
        f"mode={args.mode} action={plan.action} target={plan.target_sha} "
        f"tree_equivalent={'true' if equivalent else 'false'}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BranchSyncError as exc:
        print(f"branch-sync: {exc}", file=sys.stderr)
        raise SystemExit(1)
