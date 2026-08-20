#!/usr/bin/env python3
"""GitHub API adapter for the operator-owned live-evidence reconciler.

Only Actions run/job metadata is read. Logs, artifacts, step output, and user
identity fields are neither requested nor copied into governed evidence.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import PurePosixPath
import re
import subprocess
import sys
from typing import Any
from urllib.parse import quote

from live_evidence_reconcile import (
    Contract,
    ContractError,
    SHA_RE,
    evidence_json,
    parse_contract_yaml,
    parse_time,
    qualify_run,
    review_state,
    timed_out,
    validate_contract,
)


PACKAGE_RE = re.compile(r"Package path: `([^`]+)`")
TASK_RE = re.compile(r"Implements task `([^`]+)`")
ISSUE_RE = re.compile(r"Closes #([0-9]+)")
WAITING_PREFIX = "**Independent verification"
TRUSTED_REVIEW_AUTHOR = "github-actions[bot]"
TRUSTED_REVIEW_CHECK = "review / review"
TRUSTED_REVIEW_APP = "github-actions"
TRUSTED_RECONCILE_AUTHOR = "karsift-ai-infra-bot[bot]"


class ApiError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repository: str, token: str):
        self.repository = repository
        self.token = token

    def _run(self, args: list[str], payload: dict[str, Any] | None = None) -> Any:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.token
        command = ["gh", "api"] + args
        completed = subprocess.run(
            command,
            input=None if payload is None else json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise ApiError("github_api_request_failed")
        if not completed.stdout.strip():
            return None
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ApiError("github_api_response_invalid") from exc

    def get(self, endpoint: str) -> Any:
        return self._run([endpoint])

    def get_all(self, endpoint: str, key: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        separator = "&" if "?" in endpoint else "?"
        for page in range(1, 11):
            response = self.get(f"{endpoint}{separator}per_page=100&page={page}")
            page_items = response.get(key) if key and isinstance(response, dict) else response
            if not isinstance(page_items, list):
                raise ApiError("github_api_page_invalid")
            items.extend(item for item in page_items if isinstance(item, dict))
            if len(page_items) < 100:
                return items
        raise ApiError("github_api_pagination_limit")

    def get_optional(self, endpoint: str) -> Any | None:
        env = os.environ.copy()
        env["GH_TOKEN"] = self.token
        completed = subprocess.run(
            ["gh", "api", endpoint],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            if "HTTP 404" in completed.stderr:
                return None
            raise ApiError("github_api_request_failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ApiError("github_api_response_invalid") from exc

    def mutate(self, method: str, endpoint: str, payload: dict[str, Any]) -> Any:
        return self._run(["--method", method, endpoint, "--input", "-"], payload)


@dataclass(frozen=True)
class WaitingTask:
    pr_number: int
    issue_number: int
    task_id: str
    package_path: str
    contract_path: str
    result_path: str
    head_sha: str
    head_ref: str
    waiting_since: datetime
    contract: Contract


def safe_package_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) < 3
        or path.parts[:2] != ("specs", "changes")
    ):
        raise ContractError("invalid_package_path")
    return str(path)


def read_repository_file(api: GitHub, path: str, ref: str) -> str | None:
    encoded_path = quote(path, safe="/")
    response = api.get_optional(
        f"repos/{api.repository}/contents/{encoded_path}?ref={quote(ref, safe='')}"
    )
    if response is None or not isinstance(response, dict):
        return None
    content = response.get("content")
    if response.get("encoding") != "base64" or not isinstance(content, str):
        raise ContractError("invalid_repository_file")
    try:
        return base64.b64decode(content, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ContractError("invalid_repository_file") from exc


def trusted_waiting_review(
    api: GitHub,
    head_sha: str,
    comments: list[dict[str, Any]],
) -> tuple[dict[str, Any], datetime] | None:
    binding = f"bound to commit `{head_sha}`"
    reviews = [
        comment
        for comment in comments
        if isinstance(comment.get("body"), str)
        and comment["body"].startswith(WAITING_PREFIX)
        and binding in comment["body"]
        and (comment.get("user") or {}).get("login") == TRUSTED_REVIEW_AUTHOR
        and (comment.get("user") or {}).get("type") == "Bot"
    ]
    if not reviews:
        return None
    latest = max(reviews, key=lambda item: item.get("created_at") or "")
    if review_state(latest["body"]) != "WAITING":
        return None
    waiting_since = parse_time(latest.get("created_at"))
    check_runs = api.get_all(
        f"repos/{api.repository}/commits/{head_sha}/check-runs",
        key="check_runs",
    )
    trusted_checks = [
        check
        for check in check_runs
        if check.get("name") == TRUSTED_REVIEW_CHECK
        and check.get("conclusion") == "success"
        and (check.get("app") or {}).get("slug") == TRUSTED_REVIEW_APP
    ]
    if not any(
        parse_time(check.get("started_at")) <= waiting_since <= parse_time(check.get("completed_at"))
        for check in trusted_checks
    ):
        raise ContractError("untrusted_waiting_marker")
    return latest, waiting_since


def current_waiting_task(api: GitHub, pr: dict[str, Any]) -> WaitingTask | None:
    number = pr.get("number")
    body = pr.get("body") or ""
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    head_sha = head.get("sha")
    head_ref = head.get("ref")
    if (
        not isinstance(number, int)
        or head_repo.get("full_name") != api.repository
        or not isinstance(head_sha, str)
        or not SHA_RE.fullmatch(head_sha)
        or not isinstance(head_ref, str)
        or not head_ref.startswith("agent/")
    ):
        return None
    package_match = PACKAGE_RE.search(body)
    task_match = TASK_RE.search(body)
    issue_match = ISSUE_RE.search(body)
    if not package_match or not task_match or not issue_match:
        return None
    package_path = safe_package_path(package_match.group(1))
    task_id = task_match.group(1)

    comments_response = api.get_all(
        f"repos/{api.repository}/issues/{number}/comments"
    )
    trusted_review = trusted_waiting_review(api, head_sha, comments_response)
    if trusted_review is None:
        return None
    _, waiting_since = trusted_review

    contract_path = f"{package_path}/.karsift/live-evidence/{task_id}.yaml"
    contract_text = read_repository_file(api, contract_path, head_sha)
    if contract_text is None:
        raise ContractError("missing_contract")
    contract = validate_contract(parse_contract_yaml(contract_text), task_id)
    result_path = f"{package_path}/.karsift/live-evidence/{task_id}.result.json"
    return WaitingTask(
        pr_number=number,
        issue_number=int(issue_match.group(1)),
        task_id=task_id,
        package_path=package_path,
        contract_path=contract_path,
        result_path=result_path,
        head_sha=head_sha.lower(),
        head_ref=head_ref,
        waiting_since=waiting_since,
        contract=contract,
    )


def waiting_tasks(api: GitHub, target_pr: int | None) -> list[WaitingTask]:
    if target_pr is not None:
        candidates = [api.get(f"repos/{api.repository}/pulls/{target_pr}")]
    else:
        candidates = api.get_all(f"repos/{api.repository}/pulls?state=open")
    if not isinstance(candidates, list):
        raise ApiError("invalid_pull_response")
    result = []
    for pr in candidates:
        if not isinstance(pr, dict):
            continue
        try:
            task = current_waiting_task(api, pr)
        except ContractError as exc:
            print(f"live-evidence: rejected pr={pr.get('number', 0)} reason={exc.code}")
            continue
        if task is not None:
            result.append(task)
    return result


def result_already_present(api: GitHub, task: WaitingTask) -> bool:
    raw = read_repository_file(api, task.result_path, task.head_sha)
    if raw is None:
        return False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ContractError("malformed_result_record")
    if not isinstance(parsed, dict):
        raise ContractError("malformed_result_record")
    if parsed.get("state") != "qualified":
        raise ContractError("malformed_result_record")
    comments = api.get_all(
        f"repos/{api.repository}/issues/{task.pr_number}/comments"
    )
    binding = f"result_head_sha: `{task.head_sha}`"
    return any(
        (comment.get("user") or {}).get("login") == TRUSTED_RECONCILE_AUTHOR
        and (comment.get("body") or "").startswith("**Live-evidence reconcile — qualified**")
        and binding in (comment.get("body") or "")
        for comment in comments
    )


def run_jobs(api: GitHub, run_id: int) -> list[dict[str, Any]]:
    response = api.get(
        f"repos/{api.repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"
    )
    jobs = response.get("jobs") if isinstance(response, dict) else None
    if not isinstance(jobs, list) or response.get("total_count", len(jobs)) > 100:
        raise ContractError("ambiguous_job_set")
    return jobs


def comparison_proof(api: GitHub, task: WaitingTask, run_sha: str) -> tuple[bool, bool]:
    branch = api.get(
        f"repos/{api.repository}/branches/{quote(task.contract.branch, safe='')}"
    )
    branch_sha = ((branch or {}).get("commit") or {}).get("sha")
    if not isinstance(branch_sha, str) or not SHA_RE.fullmatch(branch_sha):
        return False, False
    pr_to_run = api.get(
        f"repos/{api.repository}/compare/{task.head_sha}...{run_sha}"
    )
    run_to_branch = api.get(
        f"repos/{api.repository}/compare/{run_sha}...{branch_sha}"
    )
    return (
        pr_to_run.get("status") in {"ahead", "identical"},
        run_to_branch.get("status") in {"ahead", "identical"},
    )


def candidate_runs(api: GitHub, task: WaitingTask) -> list[dict[str, Any]]:
    contract = task.contract
    if contract.workflow_id is not None:
        identity = str(contract.workflow_id)
        endpoint = f"repos/{api.repository}/actions/workflows/{identity}/runs"
    elif contract.workflow_file is not None:
        identity = quote(contract.workflow_file, safe="")
        endpoint = f"repos/{api.repository}/actions/workflows/{identity}/runs"
    else:
        workflows = api.get_all(
            f"repos/{api.repository}/actions/workflows",
            key="workflows",
        )
        matches = [workflow for workflow in workflows if workflow.get("name") == contract.workflow_name]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), int):
            raise ContractError("ambiguous_workflow_identity")
        endpoint = f"repos/{api.repository}/actions/workflows/{matches[0]['id']}/runs"
    response = api.get(
        f"{endpoint}?branch={quote(contract.branch, safe='')}&per_page=30"
    )
    runs = response.get("workflow_runs") if isinstance(response, dict) else None
    if not isinstance(runs, list):
        raise ApiError("invalid_runs_response")
    return sorted(runs, key=lambda run: run.get("updated_at") or "", reverse=True)


def qualify(api: GitHub, task: WaitingTask, run: dict[str, Any], now: datetime) -> dict[str, Any]:
    run_id = run.get("id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ContractError("invalid_run_id")
    if task.contract.workflow_file is None and task.contract.workflow_id is None:
        workflows = api.get_all(
            f"repos/{api.repository}/actions/workflows",
            key="workflows",
        )
        matches = [
            workflow for workflow in workflows
            if workflow.get("name") == task.contract.workflow_name
        ]
        if len(matches) != 1 or run.get("workflow_id") != matches[0].get("id"):
            raise ContractError("ambiguous_workflow_identity")
    contains_pr = contains_run = None
    if task.contract.lineage_mode == "integration_contains_pr_head":
        run_sha = run.get("head_sha")
        if not isinstance(run_sha, str) or not SHA_RE.fullmatch(run_sha):
            raise ContractError("invalid_run_sha")
        contains_pr, contains_run = comparison_proof(api, task, run_sha)
    return qualify_run(
        task.contract,
        run,
        run_jobs(api, run_id),
        pr_head_sha=task.head_sha,
        now=now,
        completed_by=task.waiting_since + timedelta(hours=72),
        integration_contains_pr=contains_pr,
        integration_contains_run=contains_run,
    )


def append_result_commit(read_api: GitHub, write_api: GitHub, task: WaitingTask, evidence: dict[str, Any]) -> str:
    live_pr = read_api.get(f"repos/{read_api.repository}/pulls/{task.pr_number}")
    live_head = ((live_pr or {}).get("head") or {}).get("sha")
    live_ref = ((live_pr or {}).get("head") or {}).get("ref")
    if live_head != task.head_sha or live_ref != task.head_ref:
        raise ContractError("stale_pr_head")
    encoded_ref = quote(f"heads/{task.head_ref}", safe="/")
    ref = read_api.get(f"repos/{read_api.repository}/git/ref/{encoded_ref}")
    if ((ref or {}).get("object") or {}).get("sha") != task.head_sha:
        raise ContractError("stale_pr_head")
    commit = read_api.get(f"repos/{read_api.repository}/git/commits/{task.head_sha}")
    base_tree = ((commit or {}).get("tree") or {}).get("sha")
    if not isinstance(base_tree, str) or not SHA_RE.fullmatch(base_tree):
        raise ApiError("invalid_git_tree")
    tree = write_api.mutate(
        "POST",
        f"repos/{write_api.repository}/git/trees",
        {
            "base_tree": base_tree,
            "tree": [{
                "path": task.result_path,
                "mode": "100644",
                "type": "blob",
                "content": evidence_json(evidence),
            }],
        },
    )
    tree_sha = (tree or {}).get("sha")
    if not isinstance(tree_sha, str):
        raise ApiError("tree_creation_failed")
    created = write_api.mutate(
        "POST",
        f"repos/{write_api.repository}/git/commits",
        {
            "message": f"{task.task_id}: record qualified live evidence",
            "tree": tree_sha,
            "parents": [task.head_sha],
        },
    )
    new_sha = (created or {}).get("sha")
    if not isinstance(new_sha, str) or not SHA_RE.fullmatch(new_sha):
        raise ApiError("commit_creation_failed")
    return new_sha.lower()


def advance_result_ref(
    read_api: GitHub,
    write_api: GitHub,
    task: WaitingTask,
    new_sha: str,
) -> None:
    """Advance the PR branch only after its exact future head is attested.

    Creating a Git commit object does not change the branch or trigger the PR
    pipeline. The App can therefore attest that exact immutable object first,
    then perform a second stale-head check before the non-force ref update. A
    fast synchronize run can never observe the result commit before its trusted
    attestation exists.
    """
    live_pr = read_api.get(f"repos/{read_api.repository}/pulls/{task.pr_number}")
    live_head = ((live_pr or {}).get("head") or {}).get("sha")
    live_ref = ((live_pr or {}).get("head") or {}).get("ref")
    if live_head != task.head_sha or live_ref != task.head_ref:
        raise ContractError("stale_pr_head")
    encoded_ref = quote(f"heads/{task.head_ref}", safe="/")
    ref = read_api.get(f"repos/{read_api.repository}/git/ref/{encoded_ref}")
    if ((ref or {}).get("object") or {}).get("sha") != task.head_sha:
        raise ContractError("stale_pr_head")
    write_api.mutate(
        "PATCH",
        f"repos/{write_api.repository}/git/refs/{encoded_ref}",
        {"sha": new_sha, "force": False},
    )


def post_qualified_comment(write_api: GitHub, task: WaitingTask, evidence: dict[str, Any], new_sha: str) -> None:
    workflow_identity = (
        evidence.get("workflow_file")
        or evidence.get("workflow_name")
        or str(evidence.get("workflow_id"))
    )
    body = "\n".join([
        "**Live-evidence reconcile — qualified**",
        "",
        "LIVE_EVIDENCE: READY FOR RE-REVIEW",
        f"task_id: `{task.task_id}`",
        f"workflow: `{workflow_identity}`",
        f"event: `{evidence['event']}`",
        f"branch: `{evidence['branch']}`",
        f"run_id: `{evidence['run_id']}`",
        f"job_count: `{len(evidence['job_ids'])}`",
        f"result_head_sha: `{new_sha}`",
        "",
        "Only allowlisted Actions metadata was recorded. The new PR head must receive a fresh exact-SHA independent review.",
    ])
    write_api.mutate(
        "POST",
        f"repos/{write_api.repository}/issues/{task.pr_number}/comments",
        {"body": body},
    )


def comment_exists(api: GitHub, issue_number: int, marker: str) -> bool:
    comments = api.get_all(
        f"repos/{api.repository}/issues/{issue_number}/comments"
    )
    return any(marker in (comment.get("body") or "") for comment in comments)


def timeout_once(read_api: GitHub, write_api: GitHub, task: WaitingTask, now: datetime) -> None:
    if not timed_out(task.waiting_since, now):
        return
    marker = f"<!-- karsift-live-evidence-timeout task={task.task_id} head={task.head_sha} -->"
    if comment_exists(read_api, task.issue_number, marker):
        return
    body = "\n".join([
        "**Live-evidence reconcile — timeout escalation**",
        "",
        marker,
        f"Task `{task.task_id}` has remained in operator-owned live-evidence waiting for the bounded 72-hour window.",
        "No implementation retry or operational-failure issue was started. Operator reconciliation is required.",
    ])
    write_api.mutate(
        "POST",
        f"repos/{write_api.repository}/issues/{task.issue_number}/comments",
        {"body": body},
    )
    print(f"live-evidence: timeout task={task.task_id} pr={task.pr_number}")


def dispatch_once(read_api: GitHub, write_api: GitHub, task: WaitingTask) -> None:
    dispatch = task.contract.dispatch
    if dispatch is None:
        raise ContractError("dispatch_not_declared")
    marker = f"<!-- karsift-live-evidence-dispatch task={task.task_id} head={task.head_sha} -->"
    if comment_exists(read_api, task.pr_number, marker):
        print(f"live-evidence: dispatch already requested task={task.task_id}")
        return
    if dispatch.workflow_file == "pipeline.yml":
        raise ContractError("dispatch_workflow_forbidden")
    repository = read_api.get(f"repos/{read_api.repository}")
    default_branch = (repository or {}).get("default_branch")
    if not isinstance(default_branch, str):
        raise ContractError("default_branch_unavailable")
    target_branch = read_api.get(
        f"repos/{read_api.repository}/branches/{quote(task.contract.branch, safe='')}"
    )
    if (target_branch or {}).get("protected") is not True:
        raise ContractError("dispatch_branch_unprotected")
    workflow_path = f".github/workflows/{dispatch.workflow_file}"
    target_workflow = read_api.get_optional(
        f"repos/{read_api.repository}/contents/{quote(workflow_path, safe='/')}?ref={quote(task.contract.branch, safe='')}"
    )
    default_workflow = read_api.get_optional(
        f"repos/{read_api.repository}/contents/{quote(workflow_path, safe='/')}?ref={quote(default_branch, safe='')}"
    )
    if (
        not isinstance(target_workflow, dict)
        or not isinstance(default_workflow, dict)
        or target_workflow.get("sha") != default_workflow.get("sha")
    ):
        raise ContractError("dispatch_workflow_not_trusted")
    write_api.mutate(
        "POST",
        f"repos/{write_api.repository}/actions/workflows/{quote(dispatch.workflow_file, safe='')}/dispatches",
        {"ref": task.contract.branch, "inputs": dispatch.inputs},
    )
    write_api.mutate(
        "POST",
        f"repos/{write_api.repository}/issues/{task.pr_number}/comments",
        {
            "body": "\n".join([
                "**Live-evidence reconcile — declared dispatch requested**",
                "",
                marker,
                f"task_id: `{task.task_id}`",
                f"workflow: `{dispatch.workflow_file}`",
                f"branch: `{task.contract.branch}`",
                "Declared inputs were passed without copying their values into this comment.",
            ])
        },
    )
    print(f"live-evidence: declared dispatch requested task={task.task_id}")


def reconcile_task(
    read_api: GitHub,
    write_api: GitHub,
    task: WaitingTask,
    now: datetime,
    explicit_run: dict[str, Any] | None,
) -> bool:
    if result_already_present(read_api, task):
        return False
    candidates = [explicit_run] if explicit_run is not None else candidate_runs(read_api, task)
    for run in candidates:
        try:
            evidence = qualify(read_api, task, run, now)
        except ContractError:
            continue
        new_sha = append_result_commit(read_api, write_api, task, evidence)
        post_qualified_comment(write_api, task, evidence, new_sha)
        advance_result_ref(read_api, write_api, task, new_sha)
        print(
            f"live-evidence: qualified task={task.task_id} pr={task.pr_number} run_id={evidence['run_id']}"
        )
        return True
    timeout_once(read_api, write_api, task, now)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("reconcile", "observe", "dispatch"), required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--pr-number", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    read_token = os.environ.get("GITHUB_TOKEN", "")
    write_token = os.environ.get("KARSIFT_APP_TOKEN", "")
    if not read_token or not write_token or not re.fullmatch(r"[^/\s]+/[^/\s]+", args.repository):
        print("live-evidence: required repository credentials or identity missing", file=sys.stderr)
        return 2
    if args.mode == "observe" and (args.run_id is None or args.run_id <= 0):
        print("live-evidence: observe mode requires a positive run id", file=sys.stderr)
        return 2
    if args.mode == "dispatch" and (args.pr_number is None or args.pr_number <= 0):
        print("live-evidence: dispatch mode requires a positive PR number", file=sys.stderr)
        return 2

    read_api = GitHub(args.repository, read_token)
    write_api = GitHub(args.repository, write_token)
    try:
        tasks = waiting_tasks(read_api, args.pr_number)
        if args.mode == "dispatch":
            if len(tasks) != 1:
                raise ContractError("waiting_task_not_found")
            dispatch_once(read_api, write_api, tasks[0])
            return 0
        explicit_run = None
        if args.mode == "observe":
            explicit_run = read_api.get(
                f"repos/{args.repository}/actions/runs/{args.run_id}"
            )
        now = datetime.now(timezone.utc)
        wake_count = 0
        for task in tasks:
            wake_count += int(
                reconcile_task(read_api, write_api, task, now, explicit_run)
            )
        print(f"live-evidence: waiting_count={len(tasks)} wake_count={wake_count}")
        return 0
    except (ApiError, ContractError) as exc:
        code = exc.code if isinstance(exc, ContractError) else str(exc)
        print(f"live-evidence: refused reason={code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
