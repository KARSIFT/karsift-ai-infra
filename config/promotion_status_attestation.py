#!/usr/bin/env python3
"""Validate the exact workflow evidence that may bridge promotion rulesets."""

from __future__ import annotations

from typing import Any

from actions_check_recovery import PROMOTION_REQUIRED_CONTEXTS
from required_check_satisfaction import missing_required_pr_contexts


EXPECTED_WORKFLOWS: dict[str, str] = {
    "governance-policy": ".github/workflows/governance-policy.yml",
    "validate": ".github/workflows/repository-governance.yml",
    "ci / ci": ".github/workflows/pipeline.yml",
}

PROMOTION_CI_RECOVERY_DISPLAY_MARKERS: frozenset[str] = frozenset(
    {"recover-promotion-pr-checks"}
)


class AttestationError(ValueError):
    """Promotion evidence cannot be attested safely."""


def verify_promotion_required_run_semantics(
    run_payload: Any,
    *,
    context: str,
) -> None:
    """Reject weaker same-head dispatches that cannot prove pr-validation."""

    if context != "ci / ci":
        return
    if not isinstance(run_payload, dict):
        raise AttestationError("invalid_ci_recovery_run_payload")
    event = str(run_payload.get("event") or "")
    if event == "pull_request":
        return
    if event != "workflow_dispatch":
        raise AttestationError("untrusted_ci_recovery_event")
    path = str(run_payload.get("path") or "").split("@", 1)[0]
    if path != EXPECTED_WORKFLOWS["ci / ci"]:
        raise AttestationError("untrusted_ci_recovery_workflow")
    display_title = str(run_payload.get("display_title") or "").lower()
    if not any(
        marker in display_title for marker in PROMOTION_CI_RECOVERY_DISPLAY_MARKERS
    ):
        raise AttestationError("untrusted_ci_recovery_semantics")


def attestable_contexts(
    summary: Any,
    *,
    pr_required_checks: list[dict[str, Any]] | None = None,
) -> tuple[tuple[str, int], ...]:
    """Return the required context/run pairs only when all are genuine passes."""

    if pr_required_checks is not None:
        missing = missing_required_pr_contexts(
            pr_required_checks, PROMOTION_REQUIRED_CONTEXTS
        )
        if missing:
            raise AttestationError(
                f"required_pr_contexts_unsatisfied:{','.join(missing)}"
            )

    if not isinstance(summary, dict) or not isinstance(summary.get("checks"), list):
        raise AttestationError("invalid_authoritative_summary")
    checks = summary["checks"]
    if any(not isinstance(item, dict) for item in checks):
        raise AttestationError("invalid_authoritative_check")

    selected: list[tuple[str, int]] = []
    for context in PROMOTION_REQUIRED_CONTEXTS:
        matches = [item for item in checks if item.get("name") == context]
        if len(matches) != 1:
            raise AttestationError(f"ambiguous_required_context:{context}")
        item = matches[0]
        run_id = item.get("run_id")
        if (
            item.get("state") != "SUCCESS"
            or item.get("kind") != "check_run"
            or item.get("workflow") != EXPECTED_WORKFLOWS[context]
            or item.get("conclusion") != "success"
            or not isinstance(run_id, int)
            or isinstance(run_id, bool)
            or run_id <= 0
        ):
            raise AttestationError(f"untrusted_required_context:{context}")
        selected.append((context, run_id))
    return tuple(selected)
