#!/usr/bin/env python3
"""Detect missing exact-SHA Actions checks and plan genuine recovery dispatches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from authoritative_checks import (
    EvidenceError,
    evaluate,
    flatten_check_runs,
    flatten_statuses,
    select_authoritative,
)

PROMOTION_REQUIRED_CONTEXTS: tuple[str, ...] = (
    "governance-policy",
    "validate",
    "ci / ci",
)

PROMOTION_WORKFLOW_CONTEXTS: dict[str, str] = {
    "governance-policy.yml": "governance-policy",
    "repository-governance.yml": "validate",
    "pipeline.yml": "ci / ci",
}

INTEGRATION_PUSH_WORKFLOWS: tuple[str, ...] = (
    "repository-governance.yml",
    "deploy-staging.yml",
)

STAGING_DEPLOY_PREFIXES: tuple[str, ...] = (
    "apps/",
    "packages/",
    "infra/",
    "tests/staging-e2e/",
)
STAGING_DEPLOY_EXACT_PATHS: frozenset[str] = frozenset(
    {
        ".github/workflows/deploy-staging.yml",
        "scripts/foundation/voc111-deploy-staging-paths.test.mjs",
    }
)

DEFAULT_TIMEOUT_SECONDS = 1800
POLL_INTERVAL_SECONDS = 30

SHA_RE = r"^[0-9a-f]{40}$"


class RecoveryError(ValueError):
    """Recovery cannot proceed safely."""


@dataclass(frozen=True)
class DispatchPlan:
    workflow_file: str
    ref: str
    inputs: Mapping[str, Any]


@dataclass(frozen=True)
class RecoveryDiagnostics:
    mode: str
    target_sha: str
    missing_contexts: tuple[str, ...]
    dispatched: tuple[str, ...]
    pending: int
    failed: int
    successful: int
    timed_out: bool


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RecoveryError(f"invalid {field}")
    return value


def validate_sha(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise RecoveryError(f"invalid {field}")
    lowered = value.lower()
    if not all(char in "0123456789abcdef" for char in lowered):
        raise RecoveryError(f"invalid {field}")
    return lowered


def validate_mode(mode: str) -> str:
    if mode not in {"integration_push", "promotion_pr"}:
        raise RecoveryError("invalid_recovery_mode")
    return mode


def required_contexts(mode: str) -> tuple[str, ...]:
    validate_mode(mode)
    if mode == "promotion_pr":
        return PROMOTION_REQUIRED_CONTEXTS
    return tuple()


def staging_deploy_required(changed_paths: Iterable[str]) -> bool:
    """Mirror the caller's VOC-111 staging push-path allowlist."""

    paths = list(changed_paths)
    if any(not isinstance(path, str) or not path for path in paths):
        raise RecoveryError("invalid_changed_path")
    return any(
        "/" not in path
        or path in STAGING_DEPLOY_EXACT_PATHS
        or path.startswith(STAGING_DEPLOY_PREFIXES)
        for path in paths
    )


def required_push_workflows(
    mode: str, *, integration_deploy_required: bool = True
) -> tuple[str, ...]:
    validate_mode(mode)
    if mode == "integration_push":
        return (
            INTEGRATION_PUSH_WORKFLOWS
            if integration_deploy_required
            else ("repository-governance.yml",)
        )
    return tuple()


def select_gate_evidence(
    check_runs_payload: Any,
    statuses_payload: Any,
    *,
    head_sha: str,
) -> dict[str, Any]:
    expected = {"head_sha": validate_sha(head_sha, "head_sha")}
    try:
        selected = select_authoritative(
            flatten_check_runs(check_runs_payload),
            flatten_statuses(statuses_payload),
            expected=expected,
        )
    except EvidenceError as exc:
        raise RecoveryError(str(exc)) from exc
    return evaluate(selected)


def missing_contexts(
    gate_summary: dict[str, Any],
    required: Sequence[str],
) -> list[str]:
    present = {
        item["name"]
        for item in gate_summary.get("checks", [])
        if item.get("state") == "SUCCESS"
        and item.get("kind") == "check_run"
        and item.get("workflow") == "github-actions"
        and item.get("conclusion") == "success"
    }
    return [name for name in required if name not in present]


def missing_push_workflow_runs(
    workflow_runs: Iterable[dict[str, Any]],
    *,
    head_sha: str,
    required_workflows: Sequence[str],
) -> list[str]:
    validated_sha = validate_sha(head_sha, "head_sha")
    bound = {
        run.get("path", "").replace(".github/workflows/", "")
        for run in workflow_runs
        if run.get("head_sha") == validated_sha
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    }
    return [name for name in required_workflows if name not in bound]


def plan_recovery_dispatches(
    *,
    mode: str,
    target_sha: str,
    branch_ref: str,
    pr_number: int | None = None,
    integration_deploy_required: bool = True,
) -> list[DispatchPlan]:
    validate_mode(mode)
    validate_sha(target_sha, "target_sha")
    if not branch_ref or not isinstance(branch_ref, str):
        raise RecoveryError("invalid_branch_ref")
    if mode == "promotion_pr":
        if pr_number is None:
            raise RecoveryError("missing_pr_number")
        pr_number = _positive_int(pr_number, "pr_number")
        pr_value = str(pr_number)
        return [
            DispatchPlan(
                workflow_file="governance-policy.yml",
                ref=branch_ref,
                inputs={"recovery_pr_number": pr_value},
            ),
            DispatchPlan(
                workflow_file="repository-governance.yml",
                ref=branch_ref,
                inputs={"recovery_pr_number": pr_value},
            ),
            DispatchPlan(
                workflow_file="pipeline.yml",
                ref=branch_ref,
                inputs={
                    "action": "recover-promotion-pr-checks",
                    "promotion_pr_number": pr_value,
                },
            ),
        ]
    plans = [
        DispatchPlan(
            workflow_file="repository-governance.yml",
            ref=branch_ref,
            inputs={"recovery_target_sha": target_sha},
        ),
    ]
    if integration_deploy_required:
        plans.append(
            DispatchPlan(
                workflow_file="deploy-staging.yml",
                ref=branch_ref,
                inputs={"recovery_target_sha": target_sha},
            )
        )
    return plans


def validate_promotion_target(
    pull_request: dict[str, Any],
    *,
    target_sha: str,
    branch_ref: str,
    pr_number: int,
) -> None:
    """Refuse recovery unless the requested PR owns the exact target branch tip."""
    validated_sha = validate_sha(target_sha, "target_sha")
    validated_pr = _positive_int(pr_number, "pr_number")
    head = pull_request.get("head") if isinstance(pull_request, dict) else None
    if (
        not isinstance(head, dict)
        or pull_request.get("number") != validated_pr
        or str(pull_request.get("state") or "").lower() != "open"
        or head.get("sha") != validated_sha
        or head.get("ref") != branch_ref
    ):
        raise RecoveryError("promotion_target_mismatch")


def suppress_active_or_successful_dispatches(
    plans: Sequence[DispatchPlan],
    workflow_runs: Iterable[dict[str, Any]],
    *,
    head_sha: str,
    gate_summary: dict[str, Any] | None = None,
) -> list[DispatchPlan]:
    """Avoid duplicate dispatches bound to the evidence each plan produces."""

    validated_sha = validate_sha(head_sha, "head_sha")
    already_running = {
        run.get("path", "").replace(".github/workflows/", "")
        for run in workflow_runs
        if run.get("head_sha") == validated_sha
        and (
            run.get("status") in {"queued", "in_progress", "pending"}
            or (
                run.get("status") == "completed"
                and run.get("conclusion") == "success"
            )
        )
    }
    context_states = {
        item.get("name"): item.get("state")
        for item in (gate_summary or {}).get("checks", [])
        if item.get("kind") == "check_run"
        and item.get("workflow") == "github-actions"
    }
    promotion_dispatch = any(plan.workflow_file == "pipeline.yml" for plan in plans)
    remaining: list[DispatchPlan] = []
    for plan in plans:
        context = PROMOTION_WORKFLOW_CONTEXTS.get(plan.workflow_file)
        if promotion_dispatch and context is not None:
            if context_states.get(context) not in {"SUCCESS", "PENDING"}:
                remaining.append(plan)
        elif plan.workflow_file not in already_running:
            remaining.append(plan)
    return remaining


def recovery_complete(
    *,
    mode: str,
    gate_summary: dict[str, Any],
    workflow_runs: Iterable[dict[str, Any]],
    head_sha: str,
    integration_deploy_required: bool = True,
) -> bool:
    validate_mode(mode)
    contexts = required_contexts(mode)
    if contexts and missing_contexts(gate_summary, contexts):
        return False
    push_workflows = required_push_workflows(
        mode, integration_deploy_required=integration_deploy_required
    )
    if push_workflows and missing_push_workflow_runs(
        workflow_runs, head_sha=head_sha, required_workflows=push_workflows
    ):
        return False
    return True


def format_timeout_diagnostics(
    *,
    mode: str,
    target_sha: str,
    pr_number: int | None,
    missing: Sequence[str],
    gate_summary: dict[str, Any],
    timeout_seconds: int,
) -> str:
    lines = [
        f"mode: {mode}",
        f"target_sha: {target_sha}",
        f"timeout_seconds: {timeout_seconds}",
        f"missing_checks: {', '.join(missing) if missing else 'none'}",
        f"pending: {gate_summary.get('pending', 0)}",
        f"failed: {gate_summary.get('failed', 0)}",
        f"successful: {gate_summary.get('successful', 0)}",
    ]
    if pr_number is not None:
        lines.insert(2, f"promotion_pr_number: {pr_number}")
    return "\n".join(lines)
