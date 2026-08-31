#!/usr/bin/env python3
"""CLI adapter for authoritative_checks used by reusable workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

from authoritative_checks import (
    EvidenceError,
    exact_single_pr_association,
    evaluate,
    flatten_check_runs,
    flatten_statuses,
    select_authoritative,
    validate_pull_request_binding,
)
from promotion_ci_attestation import (
    PIPELINE_WORKFLOW_PATH,
    is_release_carrier_run,
    parent_run_is_attestable,
)


def _read(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ordinary_pr_pipeline_parent(
    run: dict,
    jobs: list[dict] | None,
    *,
    pull_request: dict | None,
    repository: str,
    pr_number: int | None,
    production_branch: str,
) -> bool:
    """Admit exact-bound ordinary PR checks without trusting parent completion."""

    if not isinstance(pull_request, dict) or pr_number is None:
        return False
    head = pull_request.get("head") or {}
    base = pull_request.get("base") or {}
    head_ref = str(head.get("ref") or "")
    base_ref = str(base.get("ref") or "")
    if (
        int(pull_request.get("number") or 0) != pr_number
        or not head_ref.startswith(("agent/", "plan/"))
        or not base_ref
        or not production_branch
        or base_ref == production_branch
        or str((head.get("repo") or {}).get("full_name") or "") != repository
        or str((base.get("repo") or {}).get("full_name") or "") != repository
        or run.get("event") != "pull_request"
        or str(run.get("head_branch") or "") != head_ref
        or run.get("status") != "in_progress"
        or run.get("conclusion") is not None
    ):
        return False
    association = exact_single_pr_association(
        run.get("pull_requests"),
        repository=repository,
        pr_number=pr_number,
        head_sha=str(head.get("sha") or ""),
        head_ref=head_ref,
        base_sha=str(base.get("sha") or ""),
        base_ref=base_ref,
    )
    return association is not None and not is_release_carrier_run(run, jobs)


def _roster_pr_pipeline_parent(
    run: dict,
    jobs: list[dict] | None,
    *,
    pull_request: dict | None,
    repository: str,
    pr_number: int | None,
    production_branch: str,
) -> bool:
    """Admit in-progress roster PR pipeline checks without attestable completion."""

    if not isinstance(pull_request, dict) or pr_number is None:
        return False
    head = pull_request.get("head") or {}
    base = pull_request.get("base") or {}
    head_ref = str(head.get("ref") or "")
    base_ref = str(base.get("ref") or "")
    if (
        int(pull_request.get("number") or 0) != pr_number
        or not head_ref.startswith("karsift/roster-")
        or not base_ref
        or not production_branch
        or base_ref == production_branch
        or str((head.get("repo") or {}).get("full_name") or "") != repository
        or str((base.get("repo") or {}).get("full_name") or "") != repository
        or run.get("event") != "pull_request"
        or str(run.get("head_branch") or "") != head_ref
        or run.get("status") != "in_progress"
        or run.get("conclusion") is not None
    ):
        return False
    association = exact_single_pr_association(
        run.get("pull_requests"),
        repository=repository,
        pr_number=pr_number,
        head_sha=str(head.get("sha") or ""),
        head_ref=head_ref,
        base_sha=str(base.get("sha") or ""),
        base_ref=base_ref,
    )
    return association is not None and not is_release_carrier_run(run, jobs)


def _workflow_runs(
    check_runs,
    repository: str,
    required_events: set[str],
    *,
    pr_number: int | None = None,
    ordinary_pr_gate: bool = False,
    roster_pr_gate: bool = False,
    pull_request: dict | None = None,
    production_branch: str = "main",
):
    cache = {}
    jobs_cache = {}
    selected = []
    pattern = re.compile(
        rf"^https://github\.com/{re.escape(repository)}/actions/runs/([1-9][0-9]*)/job/[1-9][0-9]*$",
        re.IGNORECASE,
    )
    for item in check_runs:
        app_slug = (item.get("app") or {}).get("slug")
        if app_slug != "github-actions":
            selected.append(item)
            continue
        match = pattern.fullmatch(str(item.get("details_url") or ""))
        if not match:
            raise ValueError("GitHub Actions check lacks an immutable workflow run identity")
        run_id = int(match.group(1))
        if run_id not in cache:
            result = subprocess.run(
                ["gh", "api", f"repos/{repository}/actions/runs/{run_id}"],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise ValueError("could not validate workflow run identity")
            cache[run_id] = json.loads(result.stdout)
        run = cache[run_id]
        if run.get("event") not in required_events:
            continue
        if (
            (run.get("repository") or {}).get("full_name") != repository
            or run.get("head_sha") != item.get("head_sha")
            or not isinstance(run.get("path"), str)
            or not run["path"].startswith(".github/workflows/")
        ):
            raise ValueError("workflow run is not bound to expected repository and head")
        jobs = None
        if str(run["path"]).split("@", 1)[0] == ".github/workflows/pipeline.yml":
            if run_id not in jobs_cache:
                result = subprocess.run(
                    [
                        "gh",
                        "api",
                        "--paginate",
                        "--slurp",
                        f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.returncode:
                    raise ValueError("could not validate workflow run jobs")
                pages = json.loads(result.stdout)
                if not isinstance(pages, list) or any(
                    not isinstance(page, dict)
                    or not isinstance(page.get("jobs"), list)
                    or any(not isinstance(job, dict) for job in page["jobs"])
                    for page in pages
                ):
                    raise ValueError("invalid workflow run jobs payload")
                jobs_cache[run_id] = [
                    job for page in pages for job in page["jobs"]
                ]
            jobs = jobs_cache[run_id]
        ordinary_parent = (
            ordinary_pr_gate
            and required_events == {"pull_request"}
            and str(run["path"]).split("@", 1)[0] == PIPELINE_WORKFLOW_PATH
            and _ordinary_pr_pipeline_parent(
                run,
                jobs,
                pull_request=pull_request,
                repository=repository,
                pr_number=pr_number,
                production_branch=production_branch,
            )
        )
        roster_parent = (
            roster_pr_gate
            and required_events == {"pull_request"}
            and str(run["path"]).split("@", 1)[0] == PIPELINE_WORKFLOW_PATH
            and _roster_pr_pipeline_parent(
                run,
                jobs,
                pull_request=pull_request,
                repository=repository,
                pr_number=pr_number,
                production_branch=production_branch,
            )
        )
        if not ordinary_parent and not roster_parent and not parent_run_is_attestable(
            run, jobs, pr_number=pr_number
        ):
            continue
        item["workflow"] = run["path"]
        item["run_id"] = run_id
        selected.append(item)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-runs-file", required=True)
    parser.add_argument("--statuses-file", required=True)
    parser.add_argument("--pull-request-file", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--exclude-prefix", action="append", default=[])
    parser.add_argument("--exclude-status-context", action="append", default=[])
    parser.add_argument("--workflow-event", action="append", default=[])
    parser.add_argument("--ordinary-pr-gate", action="store_true")
    parser.add_argument("--roster-pr-gate", action="store_true")
    parser.add_argument("--production-branch", default="main")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    check_runs = flatten_check_runs(_read(args.check_runs_file))
    statuses = flatten_statuses(_read(args.statuses_file))
    pull_request = _read(args.pull_request_file)
    identity = {
        "repository": args.repository,
        "head_sha": args.head_sha,
        "base_sha": args.base_sha,
        "pr_number": args.pr_number,
    }
    validate_pull_request_binding(pull_request, identity)
    excluded_status_contexts = set(args.exclude_status_context)
    statuses = [
        item for item in statuses if item.get("context") not in excluded_status_contexts
    ]
    if args.workflow_event:
        check_runs = _workflow_runs(
            check_runs,
            args.repository,
            set(args.workflow_event),
            pr_number=args.pr_number,
            ordinary_pr_gate=args.ordinary_pr_gate,
            roster_pr_gate=args.roster_pr_gate,
            pull_request=pull_request,
            production_branch=args.production_branch,
        )
    for item in check_runs + statuses:
        for field, verified_value in identity.items():
            if field in item and item[field] != verified_value:
                raise EvidenceError(
                    f"gate evidence conflicts with verified pull request {field}"
                )
            item[field] = verified_value
    result = evaluate(
        select_authoritative(
            check_runs,
            statuses,
            expected=identity,
            exclude_prefixes=tuple(args.exclude_prefix),
        )
    )
    Path(args.output).write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
