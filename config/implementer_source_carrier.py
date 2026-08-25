#!/usr/bin/env python3
"""Detect and validate coordinated infrastructure carrier publication."""

from __future__ import annotations

import re
from typing import Sequence

from cross_repo_reference import issue_reference, reject_cross_repository_closing_text

INFRA_REPOSITORY = "KARSIFT/karsift-ai-infra"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^agent/[A-Za-z0-9._/-]+$")


class CarrierError(ValueError):
    """Coordinated carrier publication cannot proceed safely."""


def nested_worktree_has_changes(status_porcelain: str) -> bool:
    return bool(status_porcelain.strip())


def validate_publication_metadata(
    *,
    branch: str,
    head_sha: str,
    integration_sha: str,
) -> None:
    if not BRANCH_RE.fullmatch(branch):
        raise CarrierError("invalid_branch")
    if not SHA_RE.fullmatch(head_sha):
        raise CarrierError("invalid_head_sha")
    if not SHA_RE.fullmatch(integration_sha):
        raise CarrierError("invalid_integration_sha")


def validate_no_gitlink_paths(paths: Sequence[str]) -> None:
    for path in paths:
        if path == "karsift-ai-infra" or path.startswith("karsift-ai-infra/"):
            raise CarrierError("caller_index_contains_nested_gitlink")


def build_source_pr_body(
    *,
    authority_repository: str,
    issue_number: int,
    change_id: str,
    task_id: str,
    attempt: int,
) -> str:
    reference = issue_reference(authority_repository, INFRA_REPOSITORY, issue_number)
    body = (
        f"Coordinated infrastructure carrier for task `{task_id}` from "
        f"`{change_id}`.\n\n"
        f"{reference}\n\n"
        f"Implemented by the implementer role (attempt {attempt} of 2). "
        "Independent review and merge are required before the caller fixture "
        "pin can consume this infrastructure revision."
    )
    reject_cross_repository_closing_text(
        body,
        authority_repository=authority_repository,
        target_repository=INFRA_REPOSITORY,
    )
    return body


def fail_loud_recovery_instructions(
    *,
    authority_repository: str,
    issue_number: int,
    task_id: str,
    changed_paths: Sequence[str],
) -> str:
    paths = "\n".join(f"- `{path}`" for path in changed_paths)
    return (
        "Authorized nested `karsift-ai-infra/` edits were detected but isolated "
        "source publication is unavailable on this runner. Refusing to delete "
        "those edits silently.\n\n"
        f"Task: `{task_id}`\n"
        f"Caller issue: {authority_repository}#{issue_number}\n\n"
        "Nested paths with changes:\n"
        f"{paths}\n\n"
        "Recovery: open and merge an infrastructure PR from the nested checkout "
        "state, then rerun implementation or reconcile once the caller fixture "
        "can pin the exact reviewed infrastructure merge SHA."
    )
