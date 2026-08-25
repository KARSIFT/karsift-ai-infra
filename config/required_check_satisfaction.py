#!/usr/bin/env python3
"""GitHub PR required-check satisfaction aligned with ruleset merge evaluation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


PASSING_PR_CHECK_STATES = frozenset({"SUCCESS"})


class SatisfactionError(ValueError):
    """Required-check probe payload is incomplete or malformed."""


def missing_required_pr_contexts(
    required_checks: Sequence[Mapping[str, Any]],
    required_names: Sequence[str],
) -> list[str]:
    """Return required context names GitHub still reports unsatisfied on the PR."""

    if not isinstance(required_checks, Sequence):
        raise SatisfactionError("invalid_required_checks_payload")
    for item in required_checks:
        if not isinstance(item, Mapping):
            raise SatisfactionError("invalid_required_check_entry")
    states_by_name: dict[str, list[Any]] = {}
    for item in required_checks:
        states_by_name.setdefault(str(item.get("name") or ""), []).append(
            item.get("state")
        )
    return [
        name
        for name in required_names
        if not states_by_name.get(name)
        or any(
            state not in PASSING_PR_CHECK_STATES
            for state in states_by_name[name]
        )
    ]


def parse_gh_pr_checks_json(payload: Any) -> list[dict[str, Any]]:
    """Normalize ``gh pr checks --required --json`` output."""

    if not isinstance(payload, list):
        raise SatisfactionError("invalid_pr_checks_payload")
    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise SatisfactionError("invalid_pr_check_entry")
        name = item.get("name")
        state = item.get("state")
        if not isinstance(name, str) or not name.strip():
            raise SatisfactionError("invalid_pr_check_name")
        if not isinstance(state, str) or not state.strip():
            raise SatisfactionError("invalid_pr_check_state")
        normalized.append({"name": name, "state": state})
    return normalized
