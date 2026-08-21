#!/usr/bin/env python3
"""Deterministic live-evidence carrier publisher for auto-advance (VOC-102)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import textwrap

from auto_advance_ownership import (
    TASK_ID_RE,
    WAITING_MARKER_PREFIX,
    branch_name,
    carrier_pr_body,
    derive_evidence_relative_path,
    is_valid_carrier_pr,
    parse_package_risk,
    pending_evidence_body,
)


CHANGE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
PACKAGE_PATH_RE = re.compile(r"^specs/changes/[A-Z][A-Z0-9]*-[0-9]+-[a-z0-9][a-z0-9-]*$")
TRUSTED_BOT_LOGINS = {"app/karsift-ai-infra-bot", "karsift-ai-infra-bot"}


class PublisherError(RuntimeError):
    """A safe, stable publication refusal."""


def run_gh(args: list[str], *, token: str, repo: str) -> str:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repo
    completed = subprocess.run(
        ["gh", *args, "--repo", repo],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        raise PublisherError("github_api_failure")
    return completed.stdout.strip()


def _author_login(value: dict) -> str:
    author = value.get("author") or {}
    return str(author.get("login") or "")


def _matching_marker_comments(comments: list[dict], prefix: str) -> list[dict]:
    return [comment for comment in comments if prefix in str(comment.get("body") or "")]


def post_deduplicated_comment(
    repo: str,
    issue_number: int,
    token: str,
    body: str,
    prefix: str,
) -> None:
    comments = json.loads(
        run_gh(
            ["issue", "view", str(issue_number), "--json", "comments"],
            token=token,
            repo=repo,
        )
    )["comments"]
    matches = _matching_marker_comments(comments, prefix)
    if any(_author_login(comment) not in TRUSTED_BOT_LOGINS for comment in matches):
        raise PublisherError("untrusted_waiting_marker")
    if len(matches) > 1:
        raise PublisherError("duplicate_waiting_marker")
    if matches:
        return
    run_gh(
        ["issue", "comment", str(issue_number), "--body", body],
        token=token,
        repo=repo,
    )


def find_pr_for_branch(repo: str, branch: str, token: str) -> dict | None:
    items = json.loads(
        run_gh(
            [
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "all",
                "--json",
                "number,title,body,isDraft,headRefName,headRefOid,baseRefName,state,author",
            ],
            token=token,
            repo=repo,
        )
    )
    if len(items) > 1:
        raise PublisherError("duplicate_carrier_pr")
    return items[0] if items else None


def run_git(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        raise PublisherError("git_operation_failed")
    return completed.stdout.strip()


def validate_carrier_changed_paths(changed_paths: set[str], evidence_path: str) -> None:
    if changed_paths - {evidence_path}:
        raise PublisherError("carrier_contains_unexpected_paths")


def read_package_risk(package_root: Path) -> str:
    change_path = package_root / "change.yaml"
    try:
        text = change_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PublisherError("invalid_package_risk") from exc
    try:
        return parse_package_risk(text)
    except ValueError as exc:
        raise PublisherError("invalid_package_risk") from exc


def evidence_file_action(
    existing_text: str | None,
    *,
    has_trusted_pr: bool,
    pending_body: str,
) -> str:
    if existing_text is None:
        return "create"
    if not has_trusted_pr and existing_text != pending_body:
        raise PublisherError("untrusted_orphan_carrier")
    return "preserve"


def validate_inputs(
    *,
    change_id: str,
    package_path: str,
    task_id: str,
    issue_number: int,
    evidence_relative_path: str,
) -> str:
    if not CHANGE_ID_RE.fullmatch(change_id) or not TASK_ID_RE.fullmatch(task_id):
        raise PublisherError("invalid_identity")
    if not PACKAGE_PATH_RE.fullmatch(package_path):
        raise PublisherError("invalid_package_path")
    if PurePosixPath(package_path).name.split("-", 2)[:2] != change_id.split("-", 1):
        raise PublisherError("package_change_mismatch")
    expected = derive_evidence_relative_path(task_id)
    if evidence_relative_path != expected or "/" in evidence_relative_path:
        raise PublisherError("invalid_evidence_path")
    if issue_number <= 0:
        raise PublisherError("invalid_issue_number")
    return f"{package_path}/{expected}"


def validate_issue_and_roster(
    *,
    repo: str,
    token: str,
    package_root: Path,
    change_id: str,
    task_id: str,
    issue_number: int,
) -> None:
    issue = json.loads(
        run_gh(
            ["issue", "view", str(issue_number), "--json", "state,title"],
            token=token,
            repo=repo,
        )
    )
    if issue.get("state") != "OPEN":
        raise PublisherError("task_issue_not_open")
    if not str(issue.get("title") or "").startswith(f"{change_id}: {task_id} - "):
        raise PublisherError("task_issue_mismatch")
    roster_path = package_root / ".karsift/tasks.json"
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublisherError("invalid_roster") from exc
    matches = [
        item
        for item in roster
        if item.get("task_id") == task_id and item.get("issue") == issue_number
    ]
    if len(matches) != 1:
        raise PublisherError("task_roster_mismatch")


def validate_existing_pr(
    *,
    pr: dict,
    branch: str,
    integration_branch: str,
    change_id: str,
    task_id: str,
    package_path: str,
    issue_number: int,
    evidence_relative_path: str,
    risk: str,
) -> None:
    if (
        pr.get("state") != "OPEN"
        or pr.get("isDraft") is not True
        or pr.get("headRefName") != branch
        or pr.get("baseRefName") != integration_branch
        or _author_login(pr) not in TRUSTED_BOT_LOGINS
        or not is_valid_carrier_pr(
            pr_title=str(pr.get("title") or ""),
            pr_body=str(pr.get("body") or ""),
            change_id=change_id,
            task_id=task_id,
            package_path=package_path,
            issue_number=issue_number,
            evidence_relative_path=evidence_relative_path,
            risk=risk,
        )
    ):
        raise PublisherError("conflicting_existing_pr")


def ensure_carrier(
    *,
    repo: str,
    token: str,
    integration_branch: str,
    change_id: str,
    task_id: str,
    package_path: str,
    issue_number: int,
    evidence_relative_path: str,
) -> None:
    evidence_path = validate_inputs(
        change_id=change_id,
        package_path=package_path,
        task_id=task_id,
        issue_number=issue_number,
        evidence_relative_path=evidence_relative_path,
    )
    branch = branch_name(change_id, task_id)
    existing = find_pr_for_branch(repo, branch, token)
    runner_temp = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    workdir = Path(tempfile.mkdtemp(prefix="auto-advance-carrier-", dir=runner_temp))
    clone_dir = workdir / "repo"
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    subprocess.run(
        [
            "gh",
            "repo",
            "clone",
            repo,
            str(clone_dir),
            "--",
            "--branch",
            integration_branch,
            "--single-branch",
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    run_git(["config", "user.name", "karsift-ai-infra-bot"], cwd=clone_dir)
    run_git(
        ["config", "user.email", "actions@users.noreply.github.com"],
        cwd=clone_dir,
    )
    package_root = clone_dir / package_path
    if not package_root.is_dir():
        raise PublisherError("package_not_found")
    risk = read_package_risk(package_root)
    if existing is not None:
        validate_existing_pr(
            pr=existing,
            branch=branch,
            integration_branch=integration_branch,
            change_id=change_id,
            task_id=task_id,
            package_path=package_path,
            issue_number=issue_number,
            evidence_relative_path=evidence_relative_path,
            risk=risk,
        )
    validate_issue_and_roster(
        repo=repo,
        token=token,
        package_root=package_root,
        change_id=change_id,
        task_id=task_id,
        issue_number=issue_number,
    )

    remote_line = run_git(["ls-remote", "--heads", "origin", branch], cwd=clone_dir)
    remote_branch_exists = bool(remote_line)
    if remote_branch_exists:
        run_git(["fetch", "origin", branch], cwd=clone_dir)
        run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=clone_dir)
        run_git(["merge-base", f"origin/{integration_branch}", "HEAD"], cwd=clone_dir)
        changed_paths = set(
            filter(
                None,
                run_git(
                    ["diff", "--name-only", f"origin/{integration_branch}...HEAD"],
                    cwd=clone_dir,
                ).splitlines(),
            )
        )
        validate_carrier_changed_paths(changed_paths, evidence_path)
    else:
        run_git(["checkout", "-B", branch], cwd=clone_dir)

    target = clone_dir / evidence_path
    target.parent.mkdir(parents=True, exist_ok=True)
    pending_body = pending_evidence_body(task_id, change_id, package_path)
    existing_text = target.read_text(encoding="utf-8") if target.is_file() else None
    action = evidence_file_action(
        existing_text,
        has_trusted_pr=existing is not None,
        pending_body=pending_body,
    )
    if action == "create":
        target.write_text(pending_body, encoding="utf-8")
        run_git(["add", evidence_path], cwd=clone_dir)
        run_git(
            ["commit", "-m", f"{task_id}: pending operator live-evidence carrier"],
            cwd=clone_dir,
        )

    remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    run_git(["remote", "set-url", "origin", remote_url], cwd=clone_dir)
    run_git(["push", "-u", "origin", branch], cwd=clone_dir, env=env)

    pr_body = carrier_pr_body(
        change_id=change_id,
        task_id=task_id,
        package_path=package_path,
        issue_number=issue_number,
        evidence_relative_path=evidence_relative_path,
        risk=risk,
    )
    if existing is None:
        run_gh(
            [
                "pr",
                "create",
                "--base",
                integration_branch,
                "--head",
                branch,
                "--title",
                f"{change_id}: {task_id}",
                "--body",
                pr_body,
                "--draft",
            ],
            token=token,
            repo=repo,
        )

    waiting_body = textwrap.dedent(
        f"""\
        {WAITING_MARKER_PREFIX}

        Task `{task_id}` is operator-owned live evidence. Auto-advance created or reused
        the deterministic evidence-carrier PR on `{branch}` and did **not** execute
        `implement.yml`.

        Pending evidence path: `{evidence_path}`
        """
    )
    post_deduplicated_comment(
        repo,
        issue_number,
        token,
        waiting_body,
        WAITING_MARKER_PREFIX,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("KARSIFT_APP_TOKEN", ""))
    parser.add_argument("--integration-branch", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--evidence-relative-path", required=True)
    args = parser.parse_args()

    if not args.repository or not args.token:
        print("repository and token are required", file=sys.stderr)
        return 2

    try:
        ensure_carrier(
            repo=args.repository,
            token=args.token,
            integration_branch=args.integration_branch,
            change_id=args.change_id,
            task_id=args.task_id,
            package_path=args.package_path,
            issue_number=args.issue_number,
            evidence_relative_path=args.evidence_relative_path,
        )
    except (PublisherError, subprocess.SubprocessError, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, PublisherError) else "publisher_internal_error"
        print(f"carrier publisher failed: {reason}", file=sys.stderr)
        return 1
    print(f"carrier publisher succeeded for {args.task_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
