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

class AttestationError(ValueError):
    """Promotion evidence cannot be attested safely."""


def _repository_matches(payload: Any, repository: str) -> bool:
    if not isinstance(payload, dict):
        return False
    if (
        payload.get("full_name") == repository
        or payload.get("nameWithOwner") == repository
    ):
        return True
    return str(payload.get("url") or "").rstrip("/") == (
        f"https://api.github.com/repos/{repository}"
    )


def _pull_request_binding_matches(
    payload: Any,
    *,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    base_ref: str,
    head_ref: str,
) -> bool:
    if not isinstance(payload, dict) or payload.get("number") != pr_number:
        return False
    base = payload.get("base")
    head = payload.get("head")
    return bool(
        isinstance(base, dict)
        and isinstance(head, dict)
        and base.get("sha") == base_sha
        and base.get("ref") == base_ref
        and _repository_matches(base.get("repo"), repository)
        and head.get("sha") == head_sha
        and head.get("ref") == head_ref
        and _repository_matches(head.get("repo"), repository)
    )


def verify_promotion_required_run_semantics(
    run_payload: Any,
    *,
    context: str,
    run_id: int,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    base_ref: str,
    head_ref: str,
) -> None:
    """Bind promotion CI to the exact PR and code-enforced validation path."""

    if not isinstance(run_payload, dict):
        raise AttestationError("invalid_ci_recovery_run_payload")
    event = str(run_payload.get("event") or "")
    if event not in {"pull_request", "workflow_dispatch"}:
        raise AttestationError("untrusted_ci_recovery_event")
    path = str(run_payload.get("path") or "").split("@", 1)[0]
    if path != EXPECTED_WORKFLOWS.get(context):
        raise AttestationError("untrusted_ci_recovery_workflow")
    if (
        run_payload.get("id") != run_id
        or run_payload.get("status") != "completed"
        or run_payload.get("conclusion") != "success"
        or run_payload.get("head_sha") != head_sha
        or run_payload.get("head_branch") != head_ref
        or not _repository_matches(run_payload.get("repository"), repository)
    ):
        raise AttestationError("untrusted_ci_recovery_identity")
    pull_requests = run_payload.get("pull_requests")
    if not isinstance(pull_requests, list) or not any(
        _pull_request_binding_matches(
            item,
            repository=repository,
            pr_number=pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            base_ref=base_ref,
            head_ref=head_ref,
        )
        for item in pull_requests
    ):
        raise AttestationError("untrusted_ci_recovery_pr_binding")


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
