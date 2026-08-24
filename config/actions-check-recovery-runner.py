#!/usr/bin/env python3
"""Hosted adapter for recover-actions-checks reusable workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any
from urllib.parse import quote

from actions_check_recovery import (
    DEFAULT_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    RecoveryError,
    format_timeout_diagnostics,
    missing_contexts,
    missing_push_workflow_runs,
    plan_recovery_dispatches,
    recovery_complete,
    required_contexts,
    required_push_workflows,
    select_gate_evidence,
    suppress_active_or_successful_dispatches,
    validate_mode,
    validate_sha,
    staging_deploy_required,
)


class RunnerError(RuntimeError):
    """Sanitized fail-closed runner refusal."""


def gh(args: list[str], *, token: str, repository: str) -> str:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    command = ["gh", *args, "--repo", repository]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    if completed.returncode != 0:
        raise RunnerError("github_metadata_read_failed")
    return completed.stdout.strip()


def gh_api(token: str, repository: str, path: str) -> Any:
    return json.loads(gh(["api", path], token=token, repository=repository))


def gh_api_paginate(token: str, repository: str, path: str) -> list[dict]:
    payload = json.loads(
        gh(["api", "--paginate", "--slurp", path], token=token, repository=repository)
    )
    if not isinstance(payload, list):
        raise RunnerError("invalid_paginated_payload")
    items: list[dict] = []
    for page in payload:
        if not isinstance(page, dict):
            raise RunnerError("invalid_paginated_page")
        batch = page.get("check_runs") or page.get("workflow_runs") or page.get("statuses")
        if isinstance(batch, list):
            items.extend(batch)
    return items


def load_gate_summary(token: str, repository: str, head_sha: str) -> dict:
    check_runs = gh_api_paginate(
        token,
        repository,
        f"repos/{repository}/commits/{head_sha}/check-runs?per_page=100",
    )
    statuses_pages = json.loads(
        gh(
            ["api", "--paginate", "--slurp", f"repos/{repository}/commits/{head_sha}/status?per_page=100"],
            token=token,
            repository=repository,
        )
    )
    return select_gate_evidence(
        [{"check_runs": check_runs, "total_count": len(check_runs)}],
        statuses_pages,
        head_sha=head_sha,
    )


def load_workflow_runs(token: str, repository: str, head_sha: str) -> list[dict]:
    runs = gh_api_paginate(
        token,
        repository,
        f"repos/{repository}/actions/runs?head_sha={head_sha}&per_page=100",
    )
    return runs


def load_changed_paths(token: str, repository: str, head_sha: str) -> list[str]:
    payload = json.loads(
        gh(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/commits/{head_sha}?per_page=100",
            ],
            token=token,
            repository=repository,
        )
    )
    if not isinstance(payload, list) or not payload:
        raise RunnerError("invalid_commit_payload")
    paths: list[str] = []
    for page in payload:
        if not isinstance(page, dict) or not isinstance(page.get("files"), list):
            raise RunnerError("invalid_commit_payload")
        for item in page["files"]:
            path = item.get("filename") if isinstance(item, dict) else None
            if not isinstance(path, str) or not path:
                raise RunnerError("invalid_commit_path")
            paths.append(path)
    return paths


def dispatch_workflow(
    token: str,
    repository: str,
    workflow_file: str,
    ref: str,
    inputs: dict[str, Any],
) -> None:
    encoded = quote(workflow_file, safe="")
    body: dict[str, object] = {"ref": ref}
    if inputs:
        body["inputs"] = inputs
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    completed = subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/actions/workflows/{encoded}/dispatches",
            "--input",
            "-",
        ],
        input=json.dumps(body),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise RunnerError("workflow_dispatch_failed")


def collect_missing(
    mode: str,
    gate_summary: dict,
    workflow_runs: list[dict],
    head_sha: str,
    integration_deploy_required: bool = True,
) -> list[str]:
    missing: list[str] = []
    contexts = required_contexts(mode)
    if contexts:
        missing.extend(missing_contexts(gate_summary, contexts))
    push_workflows = required_push_workflows(
        mode, integration_deploy_required=integration_deploy_required
    )
    if push_workflows:
        missing.extend(
            missing_push_workflow_runs(
                workflow_runs, head_sha=head_sha, required_workflows=push_workflows
            )
        )
    if gate_summary.get("pending", 0) > 0:
        missing.append("pending_gate_evidence")
    if gate_summary.get("failed", 0) > 0:
        missing.append("failed_gate_evidence")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--branch-ref", required=True)
    parser.add_argument("--pr-number", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--github-token", default=os.environ.get("GITHUB_TOKEN", ""))
    args = parser.parse_args()

    token = args.github_token
    if not token:
        print("actions-check-recovery: missing token", file=sys.stderr)
        return 1

    try:
        mode = validate_mode(args.mode)
        target_sha = validate_sha(args.target_sha, "target_sha")
        pr_number = args.pr_number if args.pr_number > 0 else None
        timeout_seconds = args.timeout_seconds
        if timeout_seconds <= 0:
            raise RecoveryError("invalid_timeout")
    except RecoveryError as exc:
        print(f"actions-check-recovery: {exc}", file=sys.stderr)
        return 1

    dispatched: list[str] = []
    try:
        integration_deploy_required = True
        if mode == "integration_push":
            integration_deploy_required = staging_deploy_required(
                load_changed_paths(token, args.repository, target_sha)
            )
        initial_gate_summary = load_gate_summary(token, args.repository, target_sha)
        initial_workflow_runs = load_workflow_runs(
            token, args.repository, target_sha
        )
        if not recovery_complete(
            mode=mode,
            gate_summary=initial_gate_summary,
            workflow_runs=initial_workflow_runs,
            head_sha=target_sha,
            integration_deploy_required=integration_deploy_required,
        ):
            plans = suppress_active_or_successful_dispatches(
                plan_recovery_dispatches(
                    mode=mode,
                    target_sha=target_sha,
                    branch_ref=args.branch_ref,
                    pr_number=pr_number,
                    integration_deploy_required=integration_deploy_required,
                ),
                initial_workflow_runs,
                head_sha=target_sha,
            )
            for plan in plans:
                dispatch_workflow(
                    token,
                    args.repository,
                    plan.workflow_file,
                    plan.ref,
                    dict(plan.inputs),
                )
                dispatched.append(plan.workflow_file)
    except (RunnerError, RecoveryError) as exc:
        print(f"actions-check-recovery: {exc}", file=sys.stderr)
        return 1

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        gate_summary = load_gate_summary(token, args.repository, target_sha)
        workflow_runs = load_workflow_runs(token, args.repository, target_sha)
        if recovery_complete(
            mode=mode,
            gate_summary=gate_summary,
            workflow_runs=workflow_runs,
            head_sha=target_sha,
            integration_deploy_required=integration_deploy_required,
        ):
            print(
                "actions-check-recovery: complete "
                f"mode={mode} target_sha={target_sha} dispatched={','.join(dispatched) or 'none'}"
            )
            return 0
        time.sleep(POLL_INTERVAL_SECONDS)

    gate_summary = load_gate_summary(token, args.repository, target_sha)
    workflow_runs = load_workflow_runs(token, args.repository, target_sha)
    missing = collect_missing(
        mode,
        gate_summary,
        workflow_runs,
        target_sha,
        integration_deploy_required=integration_deploy_required,
    )
    print(
        format_timeout_diagnostics(
            mode=mode,
            target_sha=target_sha,
            pr_number=pr_number,
            missing=missing,
            gate_summary=gate_summary,
            timeout_seconds=timeout_seconds,
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
