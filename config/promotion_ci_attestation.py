#!/usr/bin/env python3
"""Shared promotion CI attestability and release-carrier identity rules."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

PIPELINE_WORKFLOW_PATH = ".github/workflows/pipeline.yml"
RELEASE_JOB_PREFIXES = ("release /",)
TERMINAL_RUN_STATUSES = frozenset({"completed"})
TERMINAL_SUCCESS_CONCLUSIONS = frozenset({"success"})


def workflow_path(run: Mapping[str, Any]) -> str:
    return str(run.get("path") or "").split("@", 1)[0]


def dedicated_promotion_validation_title(pr_number: int) -> str:
    return f"promotion-pr-validation PR #{pr_number}"


def is_dedicated_promotion_validation_run(
    run: Mapping[str, Any], *, pr_number: int | None = None
) -> bool:
    if workflow_path(run) != PIPELINE_WORKFLOW_PATH:
        return False
    if run.get("event") != "workflow_dispatch":
        return False
    title = str(run.get("display_title") or "")
    if pr_number is not None:
        return title == dedicated_promotion_validation_title(pr_number)
    return title.startswith("promotion-pr-validation PR #")


def _release_job_is_non_skipped(job: Mapping[str, Any]) -> bool:
    name = str(job.get("name") or "")
    if not any(name.startswith(prefix) for prefix in RELEASE_JOB_PREFIXES):
        return False
    return str(job.get("conclusion") or "").lower() != "skipped"


def is_release_carrier_run(
    run: Mapping[str, Any], jobs: Sequence[Mapping[str, Any]] | None = None
) -> bool:
    """True when a pipeline run is a reconcile-release / release-converge carrier."""

    if workflow_path(run) != PIPELINE_WORKFLOW_PATH:
        return False

    # Executed job metadata is stronger than a caller-controlled display
    # title.  A run named like dedicated recovery is still a release carrier
    # when it actually ran release/converge; a skipped shared job is harmless.
    if jobs and any(_release_job_is_non_skipped(job) for job in jobs):
        return True

    if is_dedicated_promotion_validation_run(run):
        return False

    event = str(run.get("event") or "")
    title = str(run.get("display_title") or "")
    if event == "workflow_dispatch" and title and not title.startswith(
        "promotion-pr-validation PR #"
    ):
        # Dedicated recovery binds an explicit title; other dispatches on the
        # shared pipeline carrier (including reconcile-release) are not
        # attestable promotion CI.
        return True

    return False


def parent_run_is_attestable(
    run: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]] | None = None,
    *,
    pr_number: int | None = None,
) -> bool:
    """True when a workflow run may back attestable promotion CI evidence."""

    if is_release_carrier_run(run, jobs):
        return False
    if is_dedicated_promotion_validation_run(run, pr_number=pr_number):
        return (
            run.get("status") in TERMINAL_RUN_STATUSES
            and run.get("conclusion") in TERMINAL_SUCCESS_CONCLUSIONS
        )
    if run.get("status") not in TERMINAL_RUN_STATUSES:
        return False
    if run.get("conclusion") not in TERMINAL_SUCCESS_CONCLUSIONS:
        return False
    return True


def dedicated_recovery_run_covers_dispatch(
    run: Mapping[str, Any], *, pr_number: int
) -> bool:
    """True when this exact dedicated recovery dispatch may suppress redispatch."""

    if not is_dedicated_promotion_validation_run(run, pr_number=pr_number):
        return False
    status = run.get("status")
    if status in {"queued", "in_progress", "pending", "waiting"}:
        return True
    return (
        status in TERMINAL_RUN_STATUSES
        and run.get("conclusion") in TERMINAL_SUCCESS_CONCLUSIONS
    )
