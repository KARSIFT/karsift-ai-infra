#!/usr/bin/env python3
"""Roster PR wait completeness for ruleset-required check sets (VOC-142)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from required_check_satisfaction import (
    PASSING_PR_CHECK_STATES,
    PENDING_PR_CHECK_STATES,
)


ROSTER_REQUIRED_CONTEXTS: tuple[str, ...] = (
    "ci / ci",
    "governance-policy",
    "validate",
)

_AUTHORITATIVE_TO_PR_STATE = {
    "SUCCESS": "SUCCESS",
    "PENDING": "IN_PROGRESS",
    "FAILURE": "FAILURE",
    "SKIPPED": "FAILURE",
}


class RosterWaitError(ValueError):
    """Roster wait payload is incomplete, ambiguous, or mis-bound."""


def authoritative_state_to_pr_state(state: Any) -> str:
    """Map authoritative gate state to ruleset PR-check vocabulary."""

    if not isinstance(state, str) or state not in _AUTHORITATIVE_TO_PR_STATE:
        raise RosterWaitError("invalid_authoritative_gate_state")
    return _AUTHORITATIVE_TO_PR_STATE[state]


def missing_required_roster_contexts(
    selected_checks: Sequence[Mapping[str, Any]],
    required_names: Sequence[str] = ROSTER_REQUIRED_CONTEXTS,
) -> list[str]:
    """Return required logical contexts that are absent or not yet SUCCESS."""

    if not isinstance(selected_checks, Sequence):
        raise RosterWaitError("invalid_selected_checks_payload")
    states_by_name: dict[str, list[str]] = {}
    for item in selected_checks:
        if not isinstance(item, Mapping):
            raise RosterWaitError("invalid_selected_check_entry")
        name = str(item.get("name") or "")
        if not name:
            raise RosterWaitError("invalid_selected_check_name")
        states_by_name.setdefault(name, []).append(
            authoritative_state_to_pr_state(item.get("state"))
        )

    missing: list[str] = []
    for name in required_names:
        states = states_by_name.get(name)
        if not states:
            missing.append(name)
            continue
        if any(state in PENDING_PR_CHECK_STATES for state in states):
            missing.append(name)
            continue
        if not all(state in PASSING_PR_CHECK_STATES for state in states):
            missing.append(name)
    return missing


def roster_required_set_complete(
    evaluate_result: Mapping[str, Any],
    *,
    required_names: Sequence[str] = ROSTER_REQUIRED_CONTEXTS,
) -> bool:
    """True only when every required context is registered and SUCCESS."""

    checks = evaluate_result.get("checks")
    if not isinstance(checks, list):
        raise RosterWaitError("invalid_evaluate_result")
    return not missing_required_roster_contexts(checks, required_names)


def roster_wait_snapshot(
    evaluate_result: Mapping[str, Any],
    *,
    required_names: Sequence[str] = ROSTER_REQUIRED_CONTEXTS,
) -> dict[str, Any]:
    """Augment authoritative evaluation with roster wait completeness."""

    missing = missing_required_roster_contexts(
        evaluate_result.get("checks") or [],
        required_names,
    )
    required_complete = not missing
    return {
        **dict(evaluate_result),
        "required_complete": required_complete,
        "missing_required": missing,
        "required_contexts": list(required_names),
    }
