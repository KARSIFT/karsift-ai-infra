#!/usr/bin/env python3
"""Pure policy for VOC-104 ready_for_review exact-SHA evidence reuse."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
from typing import Literal


def _load_classify_verdict():
    path = Path(__file__).with_name("classify-review-verdict.py")
    spec = spec_from_file_location("classify_review_verdict", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.classify


classify_verdict = _load_classify_verdict()


ReuseOutcome = Literal["reuse-evidence", "full-path", "fail-closed-to-full-path"]

TRUSTED_BOT_LOGIN = "karsift-ai-infra-bot[bot]"
REQUIRED_CI_JOB = "ci / ci"
AGENT_PUBLISHER_JOB = "review / publish-review"
PLAN_PUBLISHER_JOB = "plan-review / publish-plan-review"
MERGE_GATE_PREFIX = "merge-gate"
REMEDIATE_PREFIX = "remediate"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PACKAGE_RE = re.compile(
    r"^specs/changes/[A-Z][A-Z0-9]*-[0-9]+-[a-z0-9][a-z0-9-]*$"
)
TASK_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+-T[0-9]+[a-z]?$")
POLICY_WORKFLOW_PATHS = frozenset(
    {
        "KARSIFT/karsift-ai-infra/.github/workflows/ci.yml",
        "KARSIFT/karsift-ai-infra/.github/workflows/merge-gate.yml",
        "KARSIFT/karsift-ai-infra/.github/workflows/plan-review.yml",
        "KARSIFT/karsift-ai-infra/.github/workflows/ready-for-review-reuse.yml",
        "KARSIFT/karsift-ai-infra/.github/workflows/review.yml",
    }
)


@dataclass(frozen=True)
class ReuseDecision:
    outcome: ReuseOutcome
    reason: str = ""
    prior_run_id: int | None = None


@dataclass(frozen=True)
class PipelineRunSummary:
    run_id: int
    workflow_path: str
    event: str
    head_sha: str
    head_branch: str
    base_sha: str
    status: str
    conclusion: str | None
    jobs: tuple[dict, ...]
    policy_sha: str


def _normalize_sha(value: object) -> str:
    if not isinstance(value, str):
        return ""
    lowered = value.lower()
    return lowered if SHA_RE.fullmatch(lowered) else ""


def shared_policy_sha(run: dict) -> str:
    """Return one immutable SHA for the complete reuse-policy workflow set."""
    referenced = run.get("referenced_workflows")
    if not isinstance(referenced, list):
        return ""
    matches: dict[str, list[str]] = {path: [] for path in POLICY_WORKFLOW_PATHS}
    for workflow in referenced:
        if not isinstance(workflow, dict):
            return ""
        path = str(workflow.get("path") or "").split("@", 1)[0]
        if path in matches:
            matches[path].append(_normalize_sha(workflow.get("sha")))
    if any(len(shas) != 1 or not shas[0] for shas in matches.values()):
        return ""
    policy_shas = {shas[0] for shas in matches.values()}
    return next(iter(policy_shas)) if len(policy_shas) == 1 else ""


def _publisher_job(head_ref: str) -> str:
    if head_ref.startswith("agent/"):
        return AGENT_PUBLISHER_JOB
    if head_ref.startswith("plan/"):
        return PLAN_PUBLISHER_JOB
    return ""


def _job_conclusion(job: dict) -> str:
    return str(job.get("conclusion") or "").lower()


def _job_name(job: dict) -> str:
    return str(job.get("name") or "")


def required_success_jobs(head_ref: str) -> tuple[str, ...]:
    publisher = _publisher_job(head_ref)
    if not publisher:
        return ()
    return (REQUIRED_CI_JOB, publisher)


def current_reuse_decision_jobs(head_ref: str) -> tuple[str, ...]:
    if head_ref.startswith("agent/"):
        # While eligibility is running, GitHub exposes the reusable CI caller
        # as `ci`; once invoked it becomes the required `ci / ci` context.
        return ("ci", REQUIRED_CI_JOB, "extract-package-path", "review")
    if head_ref.startswith("plan/"):
        return ("ci", REQUIRED_CI_JOB, "plan-review")
    return ()


def current_reuse_merge_jobs(head_ref: str) -> tuple[str, ...]:
    if head_ref.startswith("agent/"):
        return (REQUIRED_CI_JOB, "extract-package-path", "review")
    if head_ref.startswith("plan/"):
        return (REQUIRED_CI_JOB, "plan-review")
    return ()


def current_publisher_caller_jobs(head_ref: str) -> tuple[str, ...]:
    if head_ref.startswith("agent/"):
        return ("extract-package-path", "review")
    if head_ref.startswith("plan/"):
        return ("plan-review",)
    return ()


def prior_run_has_required_success(
    *,
    run: PipelineRunSummary,
    head_ref: str,
    expected_head_sha: str,
    expected_base_sha: str,
    current_run_id: int,
    expected_policy_sha: str,
) -> bool:
    if run.run_id <= 0 or run.run_id >= current_run_id:
        return False
    if run.workflow_path != ".github/workflows/pipeline.yml":
        return False
    if run.event != "pull_request":
        return False
    if _normalize_sha(run.head_sha) != _normalize_sha(expected_head_sha):
        return False
    if run.head_branch != head_ref:
        return False
    if _normalize_sha(run.base_sha) != _normalize_sha(expected_base_sha):
        return False
    if (
        not _normalize_sha(expected_policy_sha)
        or _normalize_sha(run.policy_sha) != _normalize_sha(expected_policy_sha)
    ):
        return False
    if run.status != "completed" or run.conclusion != "success":
        return False
    if not run.jobs:
        return False
    for required in required_success_jobs(head_ref):
        matches = [job for job in run.jobs if _job_name(job) == required]
        if len(matches) != 1 or _job_conclusion(matches[0]) != "success":
            return False
    return True


def select_prior_run(
    *,
    runs: list[PipelineRunSummary],
    head_ref: str,
    expected_head_sha: str,
    expected_base_sha: str,
    current_run_id: int,
    expected_policy_sha: str,
) -> PipelineRunSummary | None:
    eligible = [
        run
        for run in runs
        if prior_run_has_required_success(
            run=run,
            head_ref=head_ref,
            expected_head_sha=expected_head_sha,
            expected_base_sha=expected_base_sha,
            current_run_id=current_run_id,
            expected_policy_sha=expected_policy_sha,
        )
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda run: run.run_id)


def parse_identity_lines(
    *,
    body: str,
    head_ref: str,
) -> tuple[str, str, str, str] | None:
    packages: list[str] = []
    tasks: list[str] = []
    issues: list[str] = []
    if head_ref.startswith("agent/"):
        tasks = re.findall(r"(?<=Implements task `)[^`]+", body)
        packages = re.findall(r"(?<=Package path: `)[^`]+", body)
        issues = re.findall(r"(?<=Closes #)[0-9]+", body)
        if len(tasks) != 1 or len(packages) != 1 or len(issues) != 1:
            return None
        if not TASK_RE.fullmatch(tasks[0]) or not PACKAGE_RE.fullmatch(packages[0]):
            return None
        return tasks[0], packages[0], issues[0], ""
    if head_ref.startswith("plan/"):
        packages = re.findall(r"(?<=New package directory: `)[^`]+", body)
        if len(packages) != 1 or not PACKAGE_RE.fullmatch(packages[0]):
            return None
        return "", packages[0], "", ""
    return None


def trusted_review_comment(
    *,
    comments: list[dict],
    head_sha: str,
    base_sha: str,
    task_id: str,
    package_path: str,
    authority_issue: str,
    pipeline_run_id: int,
) -> str:
    review_header = f"**Independent verification - bound to commit `{head_sha}`**"
    base_line = f"base_sha: `{base_sha}`"
    task_line = f"task_id: `{task_id}`" if task_id else ""
    package_line = f"package_path: `{package_path}`"
    issue_line = f"authority_issue: `{authority_issue}`" if authority_issue else ""
    run_line = f"pipeline_run_id: `{pipeline_run_id}`"
    matches: list[tuple[str, str, int]] = []
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != TRUSTED_BOT_LOGIN or user.get("type") != "Bot":
            continue
        body = str(comment.get("body") or "")
        if not body.startswith(review_header):
            continue
        lines = body.splitlines()
        if review_header not in lines:
            continue
        if package_line not in lines or base_line not in lines or run_line not in lines:
            continue
        if task_line and task_line not in lines:
            continue
        if issue_line and issue_line not in lines:
            continue
        matches.append(
            (
                body,
                str(comment.get("created_at") or ""),
                int(comment.get("id") or 0),
            )
        )
    if not matches:
        return ""
    # Match merge-gate's jq `sort_by(.created_at, .id) | last` exactly so
    # eligibility and final merge validation cannot disagree about which
    # otherwise-valid trusted verdict is authoritative.
    matches.sort(key=lambda item: (item[1], item[2]))
    return matches[-1][0]


def attestation_required(*, result_path_exists: bool) -> bool:
    return result_path_exists


def attestation_present(
    *,
    comments: list[dict],
    head_sha: str,
    base_sha: str,
    task_id: str,
) -> bool:
    binding = f"result_head_sha: `{head_sha}`"
    base_binding = f"base_sha: `{base_sha}`"
    task_binding = f"task_id: `{task_id}`"
    count = 0
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != TRUSTED_BOT_LOGIN or user.get("type") != "Bot":
            continue
        body = str(comment.get("body") or "")
        if not body.startswith("**Live-evidence reconcile — qualified**"):
            continue
        lines = body.splitlines()
        if binding in lines and base_binding in lines and task_binding in lines:
            count += 1
    return count == 1


def evaluate_reuse_eligibility(
    *,
    event_action: str,
    expected_head_sha: str,
    expected_base_sha: str,
    live_head_sha: str,
    live_base_sha: str,
    is_draft: bool | None,
    head_ref: str,
    pr_body: str,
    comments: list[dict],
    pipeline_runs: list[PipelineRunSummary],
    pr_checks: list[dict],
    current_run_id: int,
    current_policy_sha: str,
    result_path_exists: bool,
    evaluation_error: str = "",
) -> ReuseDecision:
    if evaluation_error:
        return ReuseDecision("fail-closed-to-full-path", evaluation_error)
    if event_action != "ready_for_review":
        return ReuseDecision("full-path", "not_ready_for_review")
    expected_head = _normalize_sha(expected_head_sha)
    expected_base = _normalize_sha(expected_base_sha)
    live_head = _normalize_sha(live_head_sha)
    live_base = _normalize_sha(live_base_sha)
    if not expected_head or not expected_base:
        return ReuseDecision("full-path", "invalid_expected_sha")
    if live_head != expected_head or live_base != expected_base:
        return ReuseDecision("full-path", "base_or_head_drift")
    if is_draft is not True and is_draft is not False:
        return ReuseDecision("fail-closed-to-full-path", "invalid_is_draft")
    if is_draft:
        return ReuseDecision("full-path", "still_draft")
    if not _normalize_sha(current_policy_sha):
        return ReuseDecision("fail-closed-to-full-path", "invalid_current_policy_sha")
    identity = parse_identity_lines(body=pr_body, head_ref=head_ref)
    if identity is None:
        return ReuseDecision("full-path", "identity_metadata_mismatch")
    task_id, package_path, authority_issue, _ = identity
    prior_run = select_prior_run(
        runs=pipeline_runs,
        head_ref=head_ref,
        expected_head_sha=live_head,
        expected_base_sha=live_base,
        current_run_id=current_run_id,
        expected_policy_sha=current_policy_sha,
    )
    if prior_run is None:
        return ReuseDecision("full-path", "missing_prior_success")
    verdict_body = trusted_review_comment(
        comments=comments,
        head_sha=live_head,
        base_sha=live_base,
        task_id=task_id,
        package_path=package_path,
        authority_issue=authority_issue,
        pipeline_run_id=prior_run.run_id,
    )
    if not verdict_body:
        return ReuseDecision("full-path", "missing_trusted_verdict")
    verdict_state = classify_verdict(verdict_body)
    if verdict_state not in {"PASS", "PASS_WITH_FINDINGS"}:
        return ReuseDecision("full-path", f"non_reusable_verdict:{verdict_state}")
    if not pr_checks_allow_reuse(pr_checks=pr_checks, head_ref=head_ref):
        return ReuseDecision("full-path", "non_green_pr_checks")
    if attestation_required(result_path_exists=result_path_exists):
        if not attestation_present(
            comments=comments,
            head_sha=live_head,
            base_sha=live_base,
            task_id=task_id,
        ):
            return ReuseDecision("full-path", "missing_live_evidence_attestation")
    return ReuseDecision(
        "reuse-evidence",
        "eligible",
        prior_run_id=prior_run.run_id,
    )


def pr_checks_allow_reuse(*, pr_checks: list[dict], head_ref: str) -> bool:
    """Require existing checks green without requiring not-yet-created caller jobs."""
    reusable = set(current_reuse_decision_jobs(head_ref))
    if not reusable or not pr_checks:
        return False
    for check in pr_checks:
        name = str(check.get("name") or "")
        state = str(check.get("state") or "").upper()
        if (
            name.startswith(MERGE_GATE_PREFIX)
            or name.startswith(REMEDIATE_PREFIX)
            or name.startswith("ready-for-review-reuse")
        ):
            continue
        if name in reusable and state in {"SUCCESS", "SKIPPED", "PENDING"}:
            continue
        if state not in {"SUCCESS", "SKIPPED"}:
            return False
    return True


def compute_checks_ok_with_reuse(
    *,
    pr_checks: list[dict],
    head_ref: str,
    prior_jobs: list[dict],
    reuse_outcome: str,
) -> bool:
    if reuse_outcome != "reuse-evidence":
        return compute_checks_ok(pr_checks)
    prior_required = set(required_success_jobs(head_ref))
    current_reusable = set(current_reuse_merge_jobs(head_ref))
    if not prior_required or not current_reusable:
        return False
    for required in prior_required:
        matches = [job for job in prior_jobs if _job_name(job) == required]
        if len(matches) != 1 or _job_conclusion(matches[0]) != "success":
            return False
    current_names = {str(check.get("name") or "") for check in pr_checks}
    if not current_reusable.issubset(current_names):
        return False
    current_ci = [
        check
        for check in pr_checks
        if str(check.get("name") or "") == REQUIRED_CI_JOB
    ]
    if len(current_ci) != 1 or str(current_ci[0].get("state") or "") != "SUCCESS":
        return False
    for check in pr_checks:
        name = str(check.get("name") or "")
        state = str(check.get("state") or "")
        if name.startswith(MERGE_GATE_PREFIX) or name.startswith(REMEDIATE_PREFIX):
            continue
        if name == REQUIRED_CI_JOB:
            continue
        if name in current_reusable and state == "SKIPPED":
            continue
        if state not in {"SUCCESS", "SKIPPED"}:
            return False
    return True


def compute_checks_ok(pr_checks: list[dict]) -> bool:
    for check in pr_checks:
        name = str(check.get("name") or "")
        state = str(check.get("state") or "")
        if (
            name.startswith(MERGE_GATE_PREFIX)
            or name.startswith(REMEDIATE_PREFIX)
            or name.startswith("ready-for-review-reuse")
        ):
            continue
        if state not in {"SUCCESS", "SKIPPED"}:
            return False
    return True


def publisher_check_ok(
    *,
    pr_checks: list[dict],
    head_ref: str,
    prior_jobs: list[dict] | None,
    reuse_outcome: str,
) -> bool:
    publisher = _publisher_job(head_ref)
    if not publisher:
        return False
    current = [
        check
        for check in pr_checks
        if str(check.get("name") or "") == publisher
    ]
    if reuse_outcome == "reuse-evidence" and prior_jobs is not None:
        prior_matches = [job for job in prior_jobs if _job_name(job) == publisher]
        prior_success = (
            len(prior_matches) == 1
            and _job_conclusion(prior_matches[0]) == "success"
        )
        current_publisher = current_publisher_caller_jobs(head_ref)
        caller_checks = [
            check
            for check in pr_checks
            if str(check.get("name") or "") in current_publisher
        ]
        return prior_success and bool(caller_checks) and all(
            str(item.get("state") or "") in {"SUCCESS", "SKIPPED"}
            for item in caller_checks
        )
    return bool(current) and all(
        str(item.get("state") or "") == "SUCCESS" for item in current
    )
