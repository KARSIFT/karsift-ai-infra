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
    PROMOTION_WORKFLOW_CONTEXTS,
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
    validate_promotion_target,
    validate_sha,
    staging_deploy_required,
)
from required_check_satisfaction import (
    SatisfactionError,
    SelectedRequiredCheckRun,
    parse_gh_pr_checks_json,
    plan_required_check_recovery,
)

CHECK_RUNS_READ_FAILED = "check_runs_read_failed"
WORKFLOW_RUNS_READ_FAILED = "workflow_runs_read_failed"
COMMIT_METADATA_READ_FAILED = "commit_metadata_read_failed"
REQUIRED_WORKFLOW_PATHS = {
    "governance-policy": ".github/workflows/governance-policy.yml",
    "validate": ".github/workflows/repository-governance.yml",
    "ci / ci": ".github/workflows/pipeline.yml",
}
CI_CI_CONTEXT = "ci / ci"


class RunnerError(RuntimeError):
    """Sanitized fail-closed runner refusal."""


def gh(
    args: list[str],
    *,
    token: str,
    repository: str,
    read_failure: str,
) -> str:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    # `gh api` has no `--repo` flag. Endpoints already contain the explicit
    # repository and GH_REPO keeps the CLI context deterministic.
    command = ["gh", *args]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    if completed.returncode != 0:
        raise RunnerError(read_failure)
    return completed.stdout.strip()


def gh_api(token: str, repository: str, path: str, *, read_failure: str) -> Any:
    return json.loads(
        gh(
            ["api", path],
            token=token,
            repository=repository,
            read_failure=read_failure,
        )
    )


def gh_api_paginate(
    token: str,
    repository: str,
    path: str,
    *,
    read_failure: str,
) -> list[dict]:
    payload = json.loads(
        gh(
            ["api", "--paginate", "--slurp", path],
            token=token,
            repository=repository,
            read_failure=read_failure,
        )
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
        read_failure=CHECK_RUNS_READ_FAILED,
    )
    statuses_pages = json.loads(
        gh(
            ["api", "--paginate", "--slurp", f"repos/{repository}/commits/{head_sha}/status?per_page=100"],
            token=token,
            repository=repository,
            read_failure=CHECK_RUNS_READ_FAILED,
        )
    )
    return select_gate_evidence(
        [{"check_runs": check_runs, "total_count": len(check_runs)}],
        statuses_pages,
        head_sha=head_sha,
    )


def load_workflow_runs(token: str, repository: str, head_sha: str) -> list[dict]:
    return gh_api_paginate(
        token,
        repository,
        f"repos/{repository}/actions/runs?head_sha={head_sha}&per_page=100",
        read_failure=WORKFLOW_RUNS_READ_FAILED,
    )


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
            read_failure=COMMIT_METADATA_READ_FAILED,
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


def load_required_pr_checks(token: str, repository: str, pr_number: int) -> list[dict[str, Any]]:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    env["GH_REPO"] = repository
    completed = subprocess.run(
        [
            "gh",
            "pr",
            "checks",
            str(pr_number),
            "--required",
            "--json",
            "bucket,event,link,name,state,workflow",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if not completed.stdout.strip():
        raise RunnerError(CHECK_RUNS_READ_FAILED)
    try:
        return parse_gh_pr_checks_json(json.loads(completed.stdout))
    except (json.JSONDecodeError, SatisfactionError) as exc:
        raise RunnerError(CHECK_RUNS_READ_FAILED) from exc


def load_selected_workflow_run(
    token: str,
    repository: str,
    run_id: int,
) -> dict[str, Any]:
    payload = gh_api(
        token,
        repository,
        f"repos/{repository}/actions/runs/{run_id}",
        read_failure=WORKFLOW_RUNS_READ_FAILED,
    )
    if not isinstance(payload, dict):
        raise RunnerError(WORKFLOW_RUNS_READ_FAILED)
    return payload


def validate_selected_workflow_run(
    payload: dict[str, Any],
    plan: SelectedRequiredCheckRun,
    *,
    target_sha: str,
    branch_ref: str,
    pr_number: int,
) -> None:
    pull_requests = payload.get("pull_requests")
    selected_prs = {
        item.get("number")
        for item in pull_requests
        if isinstance(item, dict)
    } if isinstance(pull_requests, list) else set()
    path = str(payload.get("path") or "").split("@", 1)[0]
    conclusion = str(payload.get("conclusion") or "").upper()
    conclusion_matches = (
        conclusion == plan.state
        or (
            plan.state in {"FAILURE", "ERROR"}
            and conclusion
            in {
                "FAILURE",
                "CANCELLED",
                "TIMED_OUT",
                "ACTION_REQUIRED",
                "STARTUP_FAILURE",
                "STALE",
            }
        )
    )
    if (
        payload.get("id") != plan.run_id
        or payload.get("status") != "completed"
        or payload.get("run_attempt") != 1
        or not conclusion_matches
        or payload.get("event") != "pull_request"
        or payload.get("head_sha") != validate_sha(target_sha, "target_sha")
        or payload.get("head_branch") != branch_ref
        or payload.get("name") != plan.workflow
        or path != REQUIRED_WORKFLOW_PATHS.get(plan.context)
        or pr_number not in selected_prs
    ):
        raise RunnerError("selected_required_run_mismatch")


def rerun_selected_workflow(
    token: str,
    repository: str,
    run_id: int,
) -> None:
    gh(
        [
            "api",
            "--method",
            "POST",
            f"repos/{repository}/actions/runs/{run_id}/rerun",
        ],
        token=token,
        repository=repository,
        read_failure="workflow_rerun_failed",
    )


def load_promotion_target(
    token: str,
    repository: str,
    pr_number: int,
    *,
    target_sha: str,
    branch_ref: str,
) -> None:
    pull_request = gh_api(
        token,
        repository,
        f"repos/{repository}/pulls/{pr_number}",
        read_failure=COMMIT_METADATA_READ_FAILED,
    )
    validate_promotion_target(
        pull_request,
        target_sha=target_sha,
        branch_ref=branch_ref,
        pr_number=pr_number,
    )


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
    pr_required_checks: list[dict[str, Any]] | None = None,
) -> list[str]:
    missing: list[str] = []
    contexts = required_contexts(mode)
    if contexts:
        missing.extend(
            missing_contexts(
                gate_summary,
                contexts,
                pr_required_checks=pr_required_checks,
            )
        )
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


def promotion_ci_selected_run_is_rerun_proof(
    plan: SelectedRequiredCheckRun,
) -> bool:
    """PR-bound ci / ci failures need dispatch recovery, not doomed reruns."""

    return plan.context == CI_CI_CONTEXT


def apply_promotion_pr_recovery_plan(
    *,
    token: str,
    repository: str,
    target_sha: str,
    branch_ref: str,
    pr_number: int,
    pr_required_checks: list[dict[str, Any]],
    rerun_ids: set[int],
    dispatched_contexts: set[str],
    dispatched: list[str],
) -> None:
    """Plan and apply promotion recovery for GitHub's current required PR view."""

    rerun_plans, dispatch_contexts = plan_required_check_recovery(
        pr_required_checks,
        required_contexts("promotion_pr"),
        repository=repository,
    )
    filtered_reruns: list[SelectedRequiredCheckRun] = []
    for plan in rerun_plans:
        if promotion_ci_selected_run_is_rerun_proof(plan):
            if CI_CI_CONTEXT not in dispatch_contexts:
                dispatch_contexts.append(CI_CI_CONTEXT)
            continue
        filtered_reruns.append(plan)
    rerun_plans = filtered_reruns
    for rerun_plan in rerun_plans:
        if rerun_plan.run_id in rerun_ids:
            continue
        run_payload = load_selected_workflow_run(
            token,
            repository,
            rerun_plan.run_id,
        )
        validate_selected_workflow_run(
            run_payload,
            rerun_plan,
            target_sha=target_sha,
            branch_ref=branch_ref,
            pr_number=pr_number,
        )
        rerun_selected_workflow(
            token,
            repository,
            rerun_plan.run_id,
        )
        rerun_ids.add(rerun_plan.run_id)
        dispatched.append(f"rerun:{rerun_plan.run_id}")

    plans = plan_recovery_dispatches(
        mode="promotion_pr",
        target_sha=target_sha,
        branch_ref=branch_ref,
        pr_number=pr_number,
        integration_deploy_required=True,
    )
    # These contexts are absent from GitHub's required PR view.
    # A same-head successful workflow run did not create the required row,
    # so it must not suppress the bootstrap dispatch.
    plans = [
        plan
        for plan in plans
        if PROMOTION_WORKFLOW_CONTEXTS.get(plan.workflow_file) in dispatch_contexts
    ]
    for plan in plans:
        context = PROMOTION_WORKFLOW_CONTEXTS.get(plan.workflow_file)
        if context is None or context in dispatched_contexts:
            continue
        dispatch_workflow(
            token,
            repository,
            plan.workflow_file,
            plan.ref,
            dict(plan.inputs),
        )
        dispatched_contexts.add(context)
        dispatched.append(plan.workflow_file)


def apply_integration_push_recovery_plan(
    *,
    token: str,
    repository: str,
    target_sha: str,
    branch_ref: str,
    pr_number: int | None,
    integration_deploy_required: bool,
    gate_summary: dict,
    workflow_runs: list[dict],
    pr_required_checks: list[dict[str, Any]] | None,
    dispatched: list[str],
) -> None:
    """Plan and apply integration_push recovery from the current snapshot only."""

    plans = plan_recovery_dispatches(
        mode="integration_push",
        target_sha=target_sha,
        branch_ref=branch_ref,
        pr_number=pr_number,
        integration_deploy_required=integration_deploy_required,
    )
    plans = suppress_active_or_successful_dispatches(
        plans,
        workflow_runs,
        head_sha=target_sha,
        gate_summary=gate_summary,
        pr_required_checks=pr_required_checks,
    )
    for plan in plans:
        dispatch_workflow(
            token,
            repository,
            plan.workflow_file,
            plan.ref,
            dict(plan.inputs),
        )
        dispatched.append(plan.workflow_file)


def run_metadata_phase(
    *,
    mode: str,
    token: str,
    repository: str,
    target_sha: str,
    branch_ref: str,
    pr_number: int | None,
) -> tuple[bool, dict, list[dict], list[dict[str, Any]] | None]:
    """Read exact-SHA metadata; fail closed before recovery planning."""

    pr_required_checks: list[dict[str, Any]] | None = None
    integration_deploy_required = True
    if mode == "integration_push":
        integration_deploy_required = staging_deploy_required(
            load_changed_paths(token, repository, target_sha)
        )
    else:
        if pr_number is None:
            raise RecoveryError("missing_pr_number")
        load_promotion_target(
            token,
            repository,
            pr_number,
            target_sha=target_sha,
            branch_ref=branch_ref,
        )
        pr_required_checks = load_required_pr_checks(token, repository, pr_number)
    gate_summary = load_gate_summary(token, repository, target_sha)
    workflow_runs = load_workflow_runs(token, repository, target_sha)
    return integration_deploy_required, gate_summary, workflow_runs, pr_required_checks


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
    rerun_ids: set[int] = set()
    dispatched_contexts: set[str] = set()
    try:
        integration_deploy_required, initial_gate_summary, initial_workflow_runs, pr_required_checks = (
            run_metadata_phase(
                mode=mode,
                token=token,
                repository=args.repository,
                target_sha=target_sha,
                branch_ref=args.branch_ref,
                pr_number=pr_number,
            )
        )
        if not recovery_complete(
            mode=mode,
            gate_summary=initial_gate_summary,
            workflow_runs=initial_workflow_runs,
            head_sha=target_sha,
            integration_deploy_required=integration_deploy_required,
            pr_required_checks=pr_required_checks,
        ):
            if mode == "promotion_pr":
                if pr_number is None or pr_required_checks is None:
                    raise RecoveryError("missing_pr_required_checks")
                apply_promotion_pr_recovery_plan(
                    token=token,
                    repository=args.repository,
                    target_sha=target_sha,
                    branch_ref=args.branch_ref,
                    pr_number=pr_number,
                    pr_required_checks=pr_required_checks,
                    rerun_ids=rerun_ids,
                    dispatched_contexts=dispatched_contexts,
                    dispatched=dispatched,
                )
            else:
                apply_integration_push_recovery_plan(
                    token=token,
                    repository=args.repository,
                    target_sha=target_sha,
                    branch_ref=args.branch_ref,
                    pr_number=pr_number,
                    integration_deploy_required=integration_deploy_required,
                    gate_summary=initial_gate_summary,
                    workflow_runs=initial_workflow_runs,
                    pr_required_checks=pr_required_checks,
                    dispatched=dispatched,
                )
    except (RunnerError, RecoveryError, SatisfactionError) as exc:
        print(f"actions-check-recovery: {exc}", file=sys.stderr)
        return 1

    deadline = time.time() + timeout_seconds
    gate_summary = initial_gate_summary
    workflow_runs = initial_workflow_runs
    while time.time() < deadline:
        try:
            integration_deploy_required, gate_summary, workflow_runs, pr_required_checks = run_metadata_phase(
                mode=mode,
                token=token,
                repository=args.repository,
                target_sha=target_sha,
                branch_ref=args.branch_ref,
                pr_number=pr_number,
            )
        except (RunnerError, RecoveryError, SatisfactionError) as exc:
            print(f"actions-check-recovery: {exc}", file=sys.stderr)
            return 1
        if recovery_complete(
            mode=mode,
            gate_summary=gate_summary,
            workflow_runs=workflow_runs,
            head_sha=target_sha,
            integration_deploy_required=integration_deploy_required,
            pr_required_checks=pr_required_checks,
        ):
            print(
                "actions-check-recovery: complete "
                f"mode={mode} target_sha={target_sha} dispatched={','.join(dispatched) or 'none'}"
            )
            return 0
        if mode == "promotion_pr":
            try:
                if pr_number is None or pr_required_checks is None:
                    raise RecoveryError("missing_pr_required_checks")
                apply_promotion_pr_recovery_plan(
                    token=token,
                    repository=args.repository,
                    target_sha=target_sha,
                    branch_ref=args.branch_ref,
                    pr_number=pr_number,
                    pr_required_checks=pr_required_checks,
                    rerun_ids=rerun_ids,
                    dispatched_contexts=dispatched_contexts,
                    dispatched=dispatched,
                )
            except (RunnerError, RecoveryError, SatisfactionError) as exc:
                print(f"actions-check-recovery: {exc}", file=sys.stderr)
                return 1
        time.sleep(POLL_INTERVAL_SECONDS)

    missing = collect_missing(
        mode,
        gate_summary,
        workflow_runs,
        target_sha,
        integration_deploy_required=integration_deploy_required,
        pr_required_checks=pr_required_checks,
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
