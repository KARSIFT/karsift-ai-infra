#!/usr/bin/env python3
"""Fail-closed selection of the newest exact-revision GitHub gate evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


PASSING_CHECK_CONCLUSIONS = {"success", "neutral"}
PASSING_STATUS_STATES = {"success"}


class EvidenceError(ValueError):
    """The supplied gate history is incomplete, ambiguous, or mis-bound."""


def _pages(payload: Any, list_key: str) -> list[dict[str, Any]]:
    pages = payload if isinstance(payload, list) else [payload]
    if not pages or any(not isinstance(page, dict) for page in pages):
        raise EvidenceError(f"invalid {list_key} pagination payload")
    totals = {page.get("total_count") for page in pages}
    if len(totals) != 1 or not all(isinstance(value, int) for value in totals):
        raise EvidenceError(f"missing or inconsistent {list_key} total_count")
    items: list[dict[str, Any]] = []
    for page in pages:
        batch = page.get(list_key)
        if not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch):
            raise EvidenceError(f"invalid {list_key} page")
        items.extend(batch)
    if len(items) != next(iter(totals)):
        raise EvidenceError(f"truncated {list_key} history")
    return items


def flatten_check_runs(payload: Any) -> list[dict[str, Any]]:
    return _pages(payload, "check_runs")


def flatten_statuses(payload: Any) -> list[dict[str, Any]]:
    return _pages(payload, "statuses")


def _timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value:
        raise EvidenceError("gate evidence lacks a timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ).timestamp()
    except ValueError as exc:
        raise EvidenceError("gate evidence has a malformed timestamp") from exc


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EvidenceError(f"gate evidence has invalid {field}")
    return value


def _bound(item: dict[str, Any], expected: dict[str, Any]) -> None:
    for field, expected_value in expected.items():
        if expected_value in (None, ""):
            continue
        if item.get(field) != expected_value:
            raise EvidenceError(f"gate evidence is not bound to expected {field}")


def _check_record(item: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    _bound(item, expected)
    name = item.get("name")
    status = item.get("status")
    conclusion = item.get("conclusion")
    if not isinstance(name, str) or not name.strip():
        raise EvidenceError("check run lacks a logical gate name")
    if status not in {"queued", "in_progress", "completed", "pending"}:
        raise EvidenceError("check run has an unknown status")
    if status == "completed" and not isinstance(conclusion, str):
        raise EvidenceError("completed check run lacks a conclusion")
    if status != "completed" and conclusion not in (None, ""):
        raise EvidenceError("non-terminal check run has a conclusion")
    return {
        "kind": "check_run",
        "name": name,
        "workflow": item.get("workflow") or (item.get("app") or {}).get("slug") or "",
        "run_id": item.get("run_id") or 0,
        "id": _positive_int(item.get("id"), "id"),
        # Attempt creation/start orders retries. Completion time is not an
        # attempt identity: an older long-running check can finish after a
        # newer retry and must not become authoritative again.
        "time": _timestamp(item.get("started_at") or item.get("created_at")),
        "state": (
            "SUCCESS"
            if status == "completed" and conclusion in PASSING_CHECK_CONCLUSIONS
            else "SKIPPED"
            if status == "completed" and conclusion == "skipped"
            else "FAILURE"
            if status == "completed"
            else "PENDING"
        ),
        "raw": item,
    }


def _status_record(item: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    _bound(item, expected)
    name = item.get("context")
    state = item.get("state")
    if not isinstance(name, str) or not name.strip():
        raise EvidenceError("commit status lacks a logical gate context")
    if state not in {"error", "failure", "pending", "success"}:
        raise EvidenceError("commit status has an unknown state")
    return {
        "kind": "status",
        "name": name,
        "workflow": f"status:{(item.get('creator') or {}).get('login') or 'unknown'}",
        "run_id": 0,
        "id": _positive_int(item.get("id"), "id"),
        "time": _timestamp(item.get("created_at") or item.get("updated_at")),
        "state": (
            "SUCCESS"
            if state in PASSING_STATUS_STATES
            else "PENDING"
            if state == "pending"
            else "FAILURE"
        ),
        "raw": item,
    }


def select_authoritative(
    check_runs: Iterable[dict[str, Any]],
    statuses: Iterable[dict[str, Any]],
    *,
    expected: dict[str, Any],
    exclude_prefixes: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Return one newest record per logical name, or reject ambiguous evidence."""

    records = [
        *(_check_record(item, expected) for item in check_runs),
        *(_status_record(item, expected) for item in statuses),
    ]
    records = [
        record
        for record in records
        if not any(record["name"].startswith(prefix) for prefix in exclude_prefixes)
    ]
    by_identity: dict[tuple[str, int], dict[str, Any]] = {}
    for record in records:
        identity = (record["kind"], record["id"])
        prior = by_identity.get(identity)
        if prior is not None and prior != record:
            raise EvidenceError("conflicting duplicate gate identity")
        by_identity[identity] = record

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in by_identity.values():
        grouped.setdefault(record["name"], []).append(record)

    selected: list[dict[str, Any]] = []
    for name, candidates in grouped.items():
        kinds = {candidate["kind"] for candidate in candidates}
        if len(kinds) != 1:
            raise EvidenceError(f"ambiguous check-run/status identity for {name}")
        workflows = {candidate["workflow"] for candidate in candidates}
        if "" in workflows or len(workflows) != 1:
            raise EvidenceError(f"ambiguous workflow identity for {name}")
        newest = max(
            candidates,
            key=lambda item: (item["run_id"], item["time"], item["id"]),
        )
        ties = [
            item
            for item in candidates
            if (item["run_id"], item["time"], item["id"])
            == (newest["run_id"], newest["time"], newest["id"])
        ]
        if len(ties) != 1:
            raise EvidenceError(f"ambiguous newest gate evidence for {name}")
        selected.append(
            {
                "name": name,
                "state": newest["state"],
                "kind": newest["kind"],
                "id": newest["id"],
                "workflow": newest["workflow"],
                "run_id": newest["run_id"],
            }
        )
    return sorted(selected, key=lambda item: (item["name"], item["kind"]))


def evaluate(selected: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(selected)
    return {
        "total_count": len(items),
        "pending": sum(item.get("state") == "PENDING" for item in items),
        "failed": sum(item.get("state") == "FAILURE" for item in items),
        "successful": sum(item.get("state") == "SUCCESS" for item in items),
        "skipped": sum(item.get("state") == "SKIPPED" for item in items),
        "checks": items,
    }
