#!/usr/bin/env python3
"""GitHub PR required-check satisfaction aligned with ruleset merge evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


PASSING_PR_CHECK_STATES = frozenset({"SUCCESS"})
PENDING_PR_CHECK_STATES = frozenset(
    {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "EXPECTED"}
)
FAILED_PR_CHECK_STATES = frozenset(
    {
        "FAILURE",
        "ERROR",
        "CANCELLED",
        "TIMED_OUT",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
        "STALE",
    }
)
EXPECTED_REQUIRED_WORKFLOWS = {
    "governance-policy": "Governance policy",
    "validate": "Repository Governance",
    "ci / ci": "pipeline",
}


class SatisfactionError(ValueError):
    """Required-check probe payload is incomplete or malformed."""


@dataclass(frozen=True)
class SelectedRequiredCheckRun:
    """One ruleset-selected failed Actions run that must be rerun in place."""

    context: str
    run_id: int
    workflow: str
    state: str


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
        normalized_item = {"name": name, "state": state}
        for field in ("bucket", "event", "link", "workflow"):
            value = item.get(field)
            if value is not None and not isinstance(value, str):
                raise SatisfactionError(f"invalid_pr_check_{field}")
            if value is not None:
                normalized_item[field] = value
        normalized.append(normalized_item)
    return normalized


def plan_required_check_recovery(
    required_checks: Sequence[Mapping[str, Any]],
    required_names: Sequence[str],
    *,
    repository: str,
) -> tuple[list[SelectedRequiredCheckRun], list[str]]:
    """Plan exact failed-run reruns and missing-context workflow dispatches.

    A failed ruleset-selected Actions row is rerun by its exact run ID. A truly
    absent context may use the existing workflow-dispatch bootstrap. Pending
    rows are left to finish. Ambiguous or non-Actions failures fail closed.
    """

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise SatisfactionError("invalid_repository")
    if any(name not in EXPECTED_REQUIRED_WORKFLOWS for name in required_names):
        raise SatisfactionError("unsupported_required_context")

    rows_by_name: dict[str, list[Mapping[str, Any]]] = {}
    for item in required_checks:
        if not isinstance(item, Mapping):
            raise SatisfactionError("invalid_required_check_entry")
        rows_by_name.setdefault(str(item.get("name") or ""), []).append(item)

    reruns: list[SelectedRequiredCheckRun] = []
    dispatch_contexts: list[str] = []
    link_pattern = re.compile(
        rf"^https://github\.com/{re.escape(repository)}/actions/runs/([1-9][0-9]*)"
        r"(?:/job/[1-9][0-9]*)?$"
    )
    for name in required_names:
        rows = rows_by_name.get(name, [])
        if not rows:
            dispatch_contexts.append(name)
            continue
        states = [str(row.get("state") or "") for row in rows]
        if states and all(state in PASSING_PR_CHECK_STATES for state in states):
            continue
        if any(state in PENDING_PR_CHECK_STATES for state in states):
            continue
        failed_rows = [
            row for row in rows if str(row.get("state") or "") in FAILED_PR_CHECK_STATES
        ]
        if not failed_rows or any(
            state not in PASSING_PR_CHECK_STATES | FAILED_PR_CHECK_STATES
            for state in states
        ):
            raise SatisfactionError(f"unsupported_required_check_state:{name}")

        candidates: dict[int, SelectedRequiredCheckRun] = {}
        for row in failed_rows:
            if (
                row.get("event") != "pull_request"
                or row.get("workflow") != EXPECTED_REQUIRED_WORKFLOWS[name]
            ):
                raise SatisfactionError(f"unrerunnable_required_check:{name}")
            match = link_pattern.fullmatch(str(row.get("link") or ""))
            if match is None:
                raise SatisfactionError(f"invalid_required_check_link:{name}")
            run_id = int(match.group(1))
            candidates[run_id] = SelectedRequiredCheckRun(
                context=name,
                run_id=run_id,
                workflow=EXPECTED_REQUIRED_WORKFLOWS[name],
                state=str(row.get("state")),
            )
        if len(candidates) != 1:
            raise SatisfactionError(f"ambiguous_required_check_run:{name}")
        reruns.append(next(iter(candidates.values())))
    return reruns, dispatch_contexts
