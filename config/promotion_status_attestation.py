#!/usr/bin/env python3
"""Validate the exact workflow evidence that may bridge promotion rulesets."""

from __future__ import annotations

from typing import Any

from actions_check_recovery import PROMOTION_REQUIRED_CONTEXTS


EXPECTED_WORKFLOWS: dict[str, str] = {
    "governance-policy": ".github/workflows/governance-policy.yml",
    "validate": ".github/workflows/repository-governance.yml",
    "ci / ci": ".github/workflows/pipeline.yml",
}


class AttestationError(ValueError):
    """Promotion evidence cannot be attested safely."""


def attestable_contexts(summary: Any) -> tuple[tuple[str, int], ...]:
    """Return the required context/run pairs only when all are genuine passes."""

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
