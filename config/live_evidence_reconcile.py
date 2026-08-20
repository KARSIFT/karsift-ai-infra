#!/usr/bin/env python3
"""Pure policy helpers for operator-owned GitHub Actions evidence.

The reconciler deliberately accepts a small YAML subset so the hosted workflow
does not install a parser at runtime. Contracts are data, not templates: YAML
aliases, tags, flow collections, duplicate keys, and unknown fields are all
rejected fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any


SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
TASK_RE = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+-T[0-9]+$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
WORKFLOW_FILE_RE = re.compile(r"^(?:\.github/workflows/)?[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$")
INPUT_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
MAX_METADATA_DURATION_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_AGE_SECONDS = 72 * 60 * 60
DEFAULT_WAIT_TIMEOUT_SECONDS = 72 * 60 * 60


class ContractError(ValueError):
    """A fail-closed contract or evidence rejection with a safe reason code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Dispatch:
    workflow_file: str
    inputs: dict[str, str]


@dataclass(frozen=True)
class Contract:
    task_id: str
    ownership: str
    workflow_file: str | None
    workflow_name: str | None
    workflow_id: int | None
    job_names: tuple[str, ...]
    events: tuple[str, ...]
    branch: str
    lineage_mode: str
    exact_sha: str | None
    conclusion: str
    max_age_seconds: int
    dispatch: Dispatch | None


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        raise ContractError("malformed_yaml")
    if value.startswith(("&", "*", "!", "{", "[", "|", ">")):
        raise ContractError("unsupported_yaml")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ContractError("malformed_yaml") from exc
        if not isinstance(parsed, str):
            raise ContractError("invalid_scalar")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise ContractError("malformed_yaml")
        return value[1:-1].replace("''", "'")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    return value


def parse_contract_yaml(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > 32_768:
        raise ContractError("contract_too_large")
    if "\t" in text or "\x00" in text or "\r" in text:
        raise ContractError("malformed_yaml")
    if re.search(r"(^|\n)\s*---\s*(?:#.*)?(?:\n|$)", text):
        raise ContractError("unsupported_yaml")

    tokens: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise ContractError("malformed_yaml")
        tokens.append((indent, raw[indent:].rstrip()))
    if not tokens or tokens[0][0] != 0:
        raise ContractError("malformed_yaml")

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(tokens) or tokens[index][0] != indent:
            raise ContractError("malformed_yaml")
        is_list = tokens[index][1].startswith("- ")
        container: Any = [] if is_list else {}
        while index < len(tokens):
            current_indent, content = tokens[index]
            if current_indent < indent:
                break
            if current_indent != indent:
                raise ContractError("malformed_yaml")
            if is_list:
                if not content.startswith("- "):
                    raise ContractError("mixed_yaml_collection")
                item = content[2:].strip()
                if not item or ":" in item:
                    raise ContractError("unsupported_yaml")
                container.append(_scalar(item))
                index += 1
                continue
            if content.startswith("- ") or ":" not in content:
                raise ContractError("mixed_yaml_collection")
            key, raw_value = content.split(":", 1)
            key = key.strip()
            if not INPUT_KEY_RE.fullmatch(key) or key in container:
                raise ContractError("duplicate_or_invalid_key")
            raw_value = raw_value.strip()
            index += 1
            if raw_value:
                container[key] = _scalar(raw_value)
            else:
                if index >= len(tokens) or tokens[index][0] <= indent:
                    raise ContractError("malformed_yaml")
                if tokens[index][0] != indent + 2:
                    raise ContractError("malformed_yaml")
                container[key], index = parse_block(index, indent + 2)
        return container, index

    parsed, final_index = parse_block(0, 0)
    if final_index != len(tokens) or not isinstance(parsed, dict):
        raise ContractError("malformed_yaml")
    return parsed


def _keys(value: dict[str, Any], allowed: set[str], code: str) -> None:
    if set(value) - allowed:
        raise ContractError(code)


def _required_string(value: Any, code: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ContractError(code)
    return value


def _string_list(value: Any, code: str, *, required: bool = True) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list) or (required and not value) or len(value) > 30:
        raise ContractError(code)
    result = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 200:
            raise ContractError(code)
        result.append(item)
    if len(result) != len(set(result)):
        raise ContractError(code)
    return tuple(result)


def parse_duration(value: Any, default: int = DEFAULT_MAX_AGE_SECONDS) -> int:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ContractError("invalid_max_age")
    match = re.fullmatch(r"([1-9][0-9]*)([mh])", value)
    if not match:
        raise ContractError("invalid_max_age")
    multiplier = 60 if match.group(2) == "m" else 3600
    seconds = int(match.group(1)) * multiplier
    if seconds > MAX_METADATA_DURATION_SECONDS:
        raise ContractError("invalid_max_age")
    return seconds


def validate_contract(data: dict[str, Any], expected_task_id: str) -> Contract:
    allowed = {
        "schema_version", "task_id", "ownership", "workflow_file",
        "workflow_name", "workflow_id", "job_names", "events", "branch",
        "sha_lineage", "conclusion", "max_age", "dispatch",
    }
    _keys(data, allowed, "unknown_contract_field")
    if data.get("schema_version") != 1:
        raise ContractError("unsupported_schema")
    task_id = _required_string(data.get("task_id"), "invalid_task_id", 80)
    if task_id != expected_task_id or not TASK_RE.fullmatch(task_id):
        raise ContractError("task_id_mismatch")
    ownership = data.get("ownership")
    if ownership not in {"operator", "live-actions"}:
        raise ContractError("invalid_ownership")

    workflow_file = data.get("workflow_file")
    workflow_name = data.get("workflow_name")
    workflow_id = data.get("workflow_id")
    if workflow_file is not None:
        workflow_file = _required_string(workflow_file, "invalid_workflow_file")
        if not WORKFLOW_FILE_RE.fullmatch(workflow_file):
            raise ContractError("invalid_workflow_file")
        workflow_file = workflow_file.removeprefix(".github/workflows/")
    if workflow_name is not None:
        workflow_name = _required_string(workflow_name, "invalid_workflow_name")
    if workflow_id is not None and (not isinstance(workflow_id, int) or workflow_id <= 0):
        raise ContractError("invalid_workflow_id")
    if workflow_file is None and workflow_name is None and workflow_id is None:
        raise ContractError("missing_workflow_identity")

    job_names = _string_list(data.get("job_names"), "invalid_job_names", required=False)
    events = _string_list(data.get("events"), "invalid_events")
    for event in events:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event):
            raise ContractError("invalid_events")
    branch = _required_string(data.get("branch"), "invalid_branch")
    if not REF_RE.fullmatch(branch) or branch.startswith(("refs/", "/")) or ".." in branch:
        raise ContractError("invalid_branch")

    lineage = data.get("sha_lineage")
    if not isinstance(lineage, dict):
        raise ContractError("invalid_sha_lineage")
    _keys(lineage, {"mode", "sha"}, "unknown_sha_lineage_field")
    mode = lineage.get("mode")
    if mode not in {"exact_pr_head", "integration_contains_pr_head", "exact_sha"}:
        raise ContractError("invalid_sha_lineage")
    exact_sha = lineage.get("sha")
    if mode == "exact_sha":
        if not isinstance(exact_sha, str) or not SHA_RE.fullmatch(exact_sha):
            raise ContractError("invalid_exact_sha")
        exact_sha = exact_sha.lower()
    elif exact_sha is not None:
        raise ContractError("unexpected_exact_sha")

    conclusion = data.get("conclusion")
    if conclusion != "success":
        raise ContractError("invalid_conclusion")
    max_age_seconds = parse_duration(data.get("max_age"))

    dispatch_data = data.get("dispatch")
    dispatch = None
    if dispatch_data is not None:
        if not isinstance(dispatch_data, dict):
            raise ContractError("invalid_dispatch")
        _keys(dispatch_data, {"workflow_file", "inputs"}, "unknown_dispatch_field")
        dispatch_file = _required_string(
            dispatch_data.get("workflow_file"), "invalid_dispatch_workflow"
        ).removeprefix(".github/workflows/")
        if workflow_file is None or dispatch_file != workflow_file:
            raise ContractError("dispatch_workflow_mismatch")
        inputs = dispatch_data.get("inputs", {})
        if not isinstance(inputs, dict) or len(inputs) > 20:
            raise ContractError("invalid_dispatch_inputs")
        normalized_inputs: dict[str, str] = {}
        for key, value in inputs.items():
            if not isinstance(key, str) or not INPUT_KEY_RE.fullmatch(key):
                raise ContractError("invalid_dispatch_inputs")
            if isinstance(value, bool):
                normalized = "true" if value else "false"
            elif isinstance(value, (str, int)) and not isinstance(value, float):
                normalized = str(value)
            else:
                raise ContractError("invalid_dispatch_inputs")
            if len(normalized) > 200 or "\n" in normalized or "\r" in normalized:
                raise ContractError("invalid_dispatch_inputs")
            normalized_inputs[key] = normalized
        dispatch = Dispatch(dispatch_file, normalized_inputs)

    return Contract(
        task_id=task_id,
        ownership=ownership,
        workflow_file=workflow_file,
        workflow_name=workflow_name,
        workflow_id=workflow_id,
        job_names=job_names,
        events=events,
        branch=branch,
        lineage_mode=mode,
        exact_sha=exact_sha,
        conclusion=conclusion,
        max_age_seconds=max_age_seconds,
        dispatch=dispatch,
    )


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ContractError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError("invalid_timestamp")
    return parsed.astimezone(timezone.utc)


def review_state(body: str) -> str:
    has_fail = bool(re.search(r"(?m)^VERDICT:\s*FAIL\s*$", body))
    has_wait = bool(
        re.search(r"(?m)^VERDICT:\s*WAITING FOR OPERATOR LIVE EVIDENCE\s*$", body)
    )
    if has_fail:
        return "FAIL"
    if has_wait:
        return "WAITING"
    return "OTHER"


def qualify_run(
    contract: Contract,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    *,
    pr_head_sha: str,
    now: datetime,
    integration_contains_pr: bool | None = None,
    integration_contains_run: bool | None = None,
) -> dict[str, Any]:
    if not SHA_RE.fullmatch(pr_head_sha):
        raise ContractError("invalid_pr_head")
    if contract.workflow_id is not None and run.get("workflow_id") != contract.workflow_id:
        raise ContractError("workflow_mismatch")
    run_path = run.get("path")
    if contract.workflow_file is not None:
        if not isinstance(run_path, str):
            raise ContractError("workflow_mismatch")
        normalized_path = run_path.split("@", 1)[0].removeprefix(".github/workflows/")
        if normalized_path != contract.workflow_file:
            raise ContractError("workflow_mismatch")
    if contract.workflow_name is not None and run.get("name") != contract.workflow_name:
        raise ContractError("workflow_mismatch")
    if run.get("event") not in contract.events:
        raise ContractError("event_mismatch")
    if run.get("head_branch") != contract.branch:
        raise ContractError("branch_mismatch")
    if run.get("conclusion") != contract.conclusion:
        raise ContractError("conclusion_mismatch")
    run_sha = run.get("head_sha")
    if not isinstance(run_sha, str) or not SHA_RE.fullmatch(run_sha):
        raise ContractError("invalid_run_sha")

    completed_at = parse_time(run.get("updated_at"))
    started_at = parse_time(run.get("run_started_at") or run.get("created_at"))
    age = (now.astimezone(timezone.utc) - completed_at).total_seconds()
    if age < -300 or age > contract.max_age_seconds:
        raise ContractError("stale_run")
    duration = int((completed_at - started_at).total_seconds())
    if duration < 0 or duration > MAX_METADATA_DURATION_SECONDS:
        raise ContractError("invalid_duration")

    if contract.lineage_mode == "exact_pr_head" and run_sha.lower() != pr_head_sha.lower():
        raise ContractError("sha_lineage_mismatch")
    if contract.lineage_mode == "exact_sha" and run_sha.lower() != contract.exact_sha:
        raise ContractError("sha_lineage_mismatch")
    if contract.lineage_mode == "integration_contains_pr_head":
        if integration_contains_pr is not True or integration_contains_run is not True:
            raise ContractError("sha_lineage_mismatch")

    if len(jobs) > 100:
        raise ContractError("ambiguous_job_set")
    if contract.job_names:
        selected = []
        for required_name in contract.job_names:
            matches = [job for job in jobs if job.get("name") == required_name]
            if len(matches) != 1 or matches[0].get("conclusion") != "success":
                raise ContractError("job_mismatch")
            selected.append(matches[0])
    else:
        if len(jobs) != 1 or jobs[0].get("conclusion") != "success":
            raise ContractError("ambiguous_job_set")
        selected = jobs
    job_ids = []
    for job in selected:
        job_id = job.get("id")
        if not isinstance(job_id, int) or job_id <= 0:
            raise ContractError("invalid_job_id")
        job_ids.append(job_id)

    run_id = run.get("id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ContractError("invalid_run_id")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "state": "qualified",
        "event": run["event"],
        "branch": run["head_branch"],
        "head_sha": run_sha.lower(),
        "run_id": run_id,
        "job_ids": sorted(job_ids),
        "conclusion": run["conclusion"],
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "duration_seconds": duration,
    }
    if contract.workflow_file is not None:
        evidence["workflow_file"] = contract.workflow_file
    if contract.workflow_name is not None:
        evidence["workflow_name"] = contract.workflow_name
    if contract.workflow_id is not None:
        evidence["workflow_id"] = contract.workflow_id
    return evidence


def timed_out(waiting_since: datetime, now: datetime) -> bool:
    return (
        now.astimezone(timezone.utc) - waiting_since.astimezone(timezone.utc)
    ).total_seconds() >= DEFAULT_WAIT_TIMEOUT_SECONDS


def evidence_json(evidence: dict[str, Any]) -> str:
    allowed = {
        "schema_version", "state", "workflow_file", "workflow_name",
        "workflow_id", "event", "branch", "head_sha", "run_id", "job_ids",
        "conclusion", "started_at", "completed_at", "duration_seconds",
    }
    if set(evidence) - allowed or evidence.get("state") != "qualified":
        raise ContractError("unsafe_evidence_metadata")
    return json.dumps(evidence, indent=2, sort_keys=True) + "\n"
