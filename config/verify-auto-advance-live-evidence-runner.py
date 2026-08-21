#!/usr/bin/env python3
"""Hosted read-only adapter for VOC-102 auto-advance proof."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from auto_advance_ownership import derive_evidence_relative_path
from live_evidence_reconcile import parse_contract_yaml, validate_contract
from verify_auto_advance_live_evidence import (
    verify_carrier_state,
    verify_issue_state,
    verify_source_jobs,
    verify_source_run,
)


CHANGE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
PACKAGE_PATH_RE = re.compile(r"^specs/changes/[A-Z][A-Z0-9]*-[0-9]+-[a-z0-9][a-z0-9-]*$")


class VerificationError(RuntimeError):
    """A sanitized, fail-closed verification refusal."""


class GitHubApi:
    def __init__(self, token: str, repository: str):
        self.token = token
        self.repository = repository

    def gh(self, args: list[str]) -> str:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.token
        env["GH_REPO"] = self.repository
        command = ["gh", *args]
        if not args or args[0] != "api":
            command.extend(["--repo", self.repository])
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if completed.returncode != 0:
            raise VerificationError("github_metadata_read_failed")
        return completed.stdout.strip()


def load_roster(
    *,
    package_root: Path,
    change_id: str,
    task_id: str,
) -> tuple[dict, dict]:
    try:
        roster = json.loads(
            (package_root / ".karsift/tasks.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError("invalid_roster") from exc
    if not isinstance(roster, list):
        raise VerificationError("invalid_roster")
    positions = [index for index, item in enumerate(roster) if item.get("task_id") == task_id]
    if len(positions) != 1 or positions[0] == 0:
        raise VerificationError("invalid_task_sequence")
    index = positions[0]
    current = roster[index]
    predecessor = roster[index - 1]
    if not str(predecessor.get("task_id") or "").startswith(f"{change_id}-T"):
        raise VerificationError("predecessor_change_mismatch")
    if not isinstance(current.get("issue"), int) or not isinstance(predecessor.get("issue"), int):
        raise VerificationError("invalid_roster_issue")
    return predecessor, current


def load_contract(package_root: Path, task_id: str) -> None:
    contract_path = package_root / ".karsift/live-evidence" / f"{task_id}.yaml"
    try:
        contract = validate_contract(
            parse_contract_yaml(contract_path.read_text(encoding="utf-8")),
            task_id,
        )
    except (OSError, ValueError) as exc:
        raise VerificationError("invalid_live_evidence_contract") from exc
    if contract.ownership not in {"operator", "live-actions"}:
        raise VerificationError("invalid_live_evidence_ownership")


def validate_local_inputs(
    *,
    repository_root: Path,
    package_path: str,
    change_id: str,
    task_id: str,
    current_ref: str,
) -> Path:
    if not CHANGE_ID_RE.fullmatch(change_id) or not task_id.startswith(f"{change_id}-T"):
        raise VerificationError("invalid_identity")
    if not PACKAGE_PATH_RE.fullmatch(package_path):
        raise VerificationError("invalid_package_path")
    if not Path(package_path).name.startswith(f"{change_id}-"):
        raise VerificationError("package_change_mismatch")
    package_root = (repository_root / package_path).resolve()
    changes_root = (repository_root / "specs/changes").resolve()
    if changes_root not in package_root.parents or not package_root.is_dir():
        raise VerificationError("package_not_found")
    if not re.fullmatch(r"[0-9a-f]{40}", current_ref):
        raise VerificationError("invalid_current_ref")
    return package_root


def require(result) -> None:
    if not result.ok:
        raise VerificationError(result.reason)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--source-run-id", type=int, required=True)
    parser.add_argument("--waiting-pr-number", type=int, required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--integration-branch", default="develop")
    parser.add_argument("--current-ref", default=os.environ.get("GITHUB_SHA", ""))
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if (
        not token
        or not args.repository
        or args.source_run_id <= 0
        or args.waiting_pr_number <= 0
    ):
        print("required read-only verification inputs are missing", file=sys.stderr)
        return 2

    try:
        repository_root = Path.cwd().resolve()
        package_root = validate_local_inputs(
            repository_root=repository_root,
            package_path=args.package_path,
            change_id=args.change_id,
            task_id=args.task_id,
            current_ref=args.current_ref,
        )
        load_contract(package_root, args.task_id)
        predecessor, current = load_roster(
            package_root=package_root,
            change_id=args.change_id,
            task_id=args.task_id,
        )

        api = GitHubApi(token, args.repository)
        repository = json.loads(
            api.gh(["api", f"/repos/{args.repository}"])
        )
        predecessor_issue = json.loads(
            api.gh(
                [
                    "issue",
                    "view",
                    str(predecessor["issue"]),
                    "--json",
                    "state,title,closedAt",
                ]
            )
        )
        require(
            verify_issue_state(
                predecessor_issue.get("state", ""),
                "CLOSED",
                "predecessor_issue_not_closed",
            )
        )

        run = json.loads(
            api.gh(
                [
                    "api",
                    f"/repos/{args.repository}/actions/runs/{args.source_run_id}",
                ]
            )
        )
        require(
            verify_source_run(
                run=run,
                repository=args.repository,
                default_branch=str(repository.get("default_branch") or ""),
                predecessor_title=str(predecessor_issue.get("title") or ""),
                predecessor_closed_at=str(predecessor_issue.get("closedAt") or ""),
            )
        )
        jobs_payload = json.loads(
            api.gh(
                [
                    "api",
                    f"/repos/{args.repository}/actions/runs/{args.source_run_id}/jobs?per_page=100",
                ]
            )
        )
        jobs = jobs_payload.get("jobs")
        if (
            not isinstance(jobs, list)
            or jobs_payload.get("total_count") != len(jobs)
            or len(jobs) > 100
        ):
            raise VerificationError("source_job_set_incomplete")
        require(verify_source_jobs(jobs))

        pr = json.loads(
            api.gh(
                [
                    "pr",
                    "view",
                    str(args.waiting_pr_number),
                    "--json",
                    "number,title,body,state,author,headRefName,headRefOid,baseRefName,isDraft",
                ]
            )
        )
        branch = str(pr.get("headRefName") or "")
        prs_on_branch = json.loads(
            api.gh(
                [
                    "pr",
                    "list",
                    "--head",
                    branch,
                    "--state",
                    "all",
                    "--json",
                    "number",
                ]
            )
        )
        issue = json.loads(
            api.gh(
                [
                    "issue",
                    "view",
                    str(current["issue"]),
                    "--json",
                    "state,title,comments",
                ]
            )
        )
        if issue.get("title") is None or not str(issue["title"]).startswith(
            f"{args.change_id}: {args.task_id} - "
        ):
            raise VerificationError("task_issue_mismatch")
        require(verify_issue_state(issue.get("state", ""), "OPEN", "task_issue_not_open"))

        evidence_path = package_root / derive_evidence_relative_path(args.task_id)
        evidence_text = (
            evidence_path.read_text(encoding="utf-8") if evidence_path.is_file() else None
        )
        require(
            verify_carrier_state(
                pr=pr,
                prs_on_branch=prs_on_branch,
                comments=issue.get("comments", []),
                change_id=args.change_id,
                task_id=args.task_id,
                package_path=args.package_path,
                issue_number=current["issue"],
                integration_branch=args.integration_branch,
                evidence_text=evidence_text,
                source_run_id=args.source_run_id,
                current_ref=args.current_ref,
            )
        )
    except (VerificationError, OSError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, VerificationError) else "verification_internal_error"
        print(f"verify failed: {reason}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "source_run_id": args.source_run_id,
                "waiting_pr_number": args.waiting_pr_number,
                "task_id": args.task_id,
                "carrier_head_sha": args.current_ref,
                "verify_result": "pass",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
