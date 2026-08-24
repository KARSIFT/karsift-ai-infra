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

INTEGRATION_PUSH_WORKFLOWS: tuple[str, ...] = (
    "repository-governance.yml",
    "deploy-staging.yml",
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


def required_push_workflows(mode: str) -> tuple[str, ...]:
    validate_mode(mode)
    if mode == "integration_push":
        return INTEGRATION_PUSH_WORKFLOWS
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
        and run.get("conclusion") in {"success", "neutral"}
    }
    return [name for name in required_workflows if name not in bound]


def plan_recovery_dispatches(
    *,
    mode: str,
    target_sha: str,
    branch_ref: str,
    pr_number: int | None = None,
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
    return [
        DispatchPlan(
            workflow_file="repository-governance.yml",
            ref=branch_ref,
            inputs={},
        ),
        DispatchPlan(
            workflow_file="deploy-staging.yml",
            ref=branch_ref,
            inputs={},
        ),
    ]


def recovery_complete(
    *,
    mode: str,
    gate_summary: dict[str, Any],
    workflow_runs: Iterable[dict[str, Any]],
    head_sha: str,
) -> bool:
    validate_mode(mode)
    contexts = required_contexts(mode)
    if contexts and missing_contexts(gate_summary, contexts):
        return False
    push_workflows = required_push_workflows(mode)
    if push_workflows and missing_push_workflow_runs(
        workflow_runs, head_sha=head_sha, required_workflows=push_workflows
    ):
        return False
    if gate_summary.get("pending", 0) > 0:
        return False
    if gate_summary.get("failed", 0) > 0:
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
