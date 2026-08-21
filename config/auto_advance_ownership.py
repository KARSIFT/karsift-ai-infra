#!/usr/bin/env python3
"""Pure ownership classification for auto-advance dispatch decisions (VOC-102)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

from live_evidence_reconcile import (
    ContractError,
    parse_contract_yaml,
    validate_contract,
)


TASK_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+-T\d+[a-z]?$")
EVIDENCE_SUFFIX_RE = re.compile(r"-T(\d+[a-z]?)$")
MARKER_FIELD_PREFIX = "- Automation ownership:"
WAITING_MARKER_PREFIX = (
    "VOC-102: Live-evidence carrier prepared; no implementer run was started."
)
FAIL_CLOSED_MARKER_PREFIX = (
    "VOC-102: Auto-advance fail-closed on ownership metadata for"
)

Decision = Literal["none", "implement", "prepare-live-evidence", "fail-closed"]


@dataclass(frozen=True)
class Classification:
    decision: Decision
    reason: str = ""
    ownership: str | None = None
    evidence_relative_path: str | None = None


def derive_evidence_relative_path(task_id: str) -> str:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid_task_id_for_evidence")
    match = EVIDENCE_SUFFIX_RE.search(task_id)
    if match is None:
        raise ValueError("invalid_task_id_for_evidence")
    return f"t{match.group(1).lower()}-evidence.md"


def parse_automation_ownership_markers(tasks_md: str, task_id: str) -> tuple[str, ...]:
    heading_re = re.compile(
        rf"^##[ \t]+{re.escape(task_id)}(?:[ \t]+(?:—|-).*)?[ \t]*$",
        re.MULTILINE,
    )
    headings = list(heading_re.finditer(tasks_md))
    if not headings:
        return ()
    if len(headings) != 1:
        raise ValueError("duplicate_task_stanza")
    section_start = headings[0].end()
    next_heading = tasks_md.find("\n## ", section_start)
    section = tasks_md[section_start:] if next_heading < 0 else tasks_md[section_start:next_heading]
    markers: list[str] = []
    for line in section.splitlines():
        stripped = line.rstrip()
        if stripped.startswith(MARKER_FIELD_PREFIX):
            markers.append(stripped.removeprefix(MARKER_FIELD_PREFIX).strip())
    return tuple(markers)


def _load_contract_ownership(contract_path: Path, expected_task_id: str) -> str:
    try:
        text = contract_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError("unreadable_contract") from exc
    data = parse_contract_yaml(text)
    return validate_contract(data, expected_task_id).ownership


def classify_next_task(
    package_path: str,
    next_task_id: str,
    tasks_md: str | None = None,
) -> Classification:
    if not TASK_ID_RE.fullmatch(next_task_id):
        return Classification("fail-closed", "invalid_task_id")

    try:
        markers = parse_automation_ownership_markers(tasks_md or "", next_task_id)
    except ValueError as exc:
        return Classification("fail-closed", str(exc))
    if len(markers) > 1:
        return Classification("fail-closed", "duplicate_automation_marker")
    marker = markers[0] if markers else None
    if marker is not None and marker not in {"operator", "live-actions"}:
        return Classification("fail-closed", "invalid_automation_marker")

    contract_path = Path(package_path) / ".karsift/live-evidence" / f"{next_task_id}.yaml"
    contract_exists = contract_path.is_file()

    if contract_exists:
        try:
            ownership = _load_contract_ownership(contract_path, next_task_id)
        except ContractError as exc:
            return Classification("fail-closed", exc.code)
        if marker is not None and marker != ownership:
            return Classification("fail-closed", "marker_contract_conflict")
        try:
            evidence_relative_path = derive_evidence_relative_path(next_task_id)
        except ValueError:
            return Classification("fail-closed", "invalid_task_id_for_evidence")
        return Classification(
            "prepare-live-evidence",
            ownership=ownership,
            evidence_relative_path=evidence_relative_path,
        )

    if marker is not None:
        return Classification("fail-closed", "marker_without_contract")

    return Classification("implement")


def pending_evidence_body(task_id: str, change_id: str, package_path: str) -> str:
    return "\n".join(
        [
            f"# {task_id} — Evidence (pending operator live evidence)",
            "",
            "Deterministic evidence carrier created by auto-advance (VOC-102).",
            "No implementer run was started for this operator-owned task.",
            "",
            f"Package: `{package_path}`",
            f"Change: `{change_id}`",
            "",
            "Record allowlisted metadata only when operator evidence is available.",
            "See docs/operations/live-evidence.md.",
            "",
        ]
    )


def carrier_pr_body(
    *,
    change_id: str,
    task_id: str,
    package_path: str,
    issue_number: int,
    evidence_relative_path: str,
) -> str:
    return "\n".join(
        [
            f"Implements task `{task_id}` from `{change_id}` (`{package_path}`).",
            "",
            f"Closes #{issue_number}.",
            "",
            "Operator-owned live-evidence carrier. No general implementer run was",
            "executed for this task. Repository-controlled reconciliation observes the",
            "declared contract after operator proof arrives.",
            "",
            "Risk classification: R4",
            "",
            f"Package path: `{package_path}`",
            "",
            f"Pending evidence path: `{package_path}/{evidence_relative_path}`",
            "",
        ]
    )


def branch_name(change_id: str, task_id: str) -> str:
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", task_id.lower())).strip("-")
    change_lower = change_id.lower()
    return f"agent/{change_lower}-{slug}"


def is_valid_carrier_pr(
    *,
    pr_title: str,
    pr_body: str,
    change_id: str,
    task_id: str,
    package_path: str,
    issue_number: int,
    evidence_relative_path: str,
) -> bool:
    expected_title = f"{change_id}: {task_id}"
    if pr_title.strip() != expected_title:
        return False
    expected_body = carrier_pr_body(
        change_id=change_id,
        task_id=task_id,
        package_path=package_path,
        issue_number=issue_number,
        evidence_relative_path=evidence_relative_path,
    )
    return pr_body.strip() == expected_body.strip()
