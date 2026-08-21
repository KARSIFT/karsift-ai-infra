#!/usr/bin/env python3
"""Pure ownership classification for remediation dispatch decisions (VOC-106)."""

from __future__ import annotations

from typing import Literal

from auto_advance_ownership import Classification, classify_next_task


RemediationOwnership = Literal["ordinary", "operator", "live-actions", "fail-closed"]

OPERATOR_ESCALATION_MARKER_PREFIX = "VOC-106: Remediation operator escalation for"
FAIL_CLOSED_MARKER_PREFIX = (
    "VOC-106: Remediation fail-closed on ownership metadata for"
)


def map_classification(classification: Classification) -> tuple[RemediationOwnership, str]:
    if classification.decision == "implement":
        return "ordinary", ""
    if classification.decision == "prepare-live-evidence":
        ownership = classification.ownership or "operator"
        if ownership not in {"operator", "live-actions"}:
            return "fail-closed", "invalid_live_evidence_ownership"
        return ownership, ""
    if classification.decision == "fail-closed":
        return "fail-closed", classification.reason or "ownership_metadata_invalid"
    return "fail-closed", "unexpected_classification"


def classify_task_for_remediation(
    package_path: str,
    task_id: str,
    tasks_md: str | None = None,
) -> tuple[RemediationOwnership, str]:
    ownership, reason = map_classification(
        classify_next_task(package_path, task_id, tasks_md)
    )
    return ownership, reason
