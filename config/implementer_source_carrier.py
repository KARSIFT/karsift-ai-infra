#!/usr/bin/env python3
"""Detect and validate coordinated infrastructure carrier publication."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

from cross_repo_reference import issue_reference, reject_cross_repository_closing_text

INFRA_REPOSITORY = "KARSIFT/karsift-ai-infra"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^agent/[A-Za-z0-9._/-]+$")
SOURCE_BUNDLE_REF = "refs/karsift/source-bundle-head"
ZERO_SHA = "0" * 40


class CarrierError(ValueError):
    """Coordinated carrier publication cannot proceed safely."""


def _git(
    repository: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise CarrierError(f"git_command_failed:{args[0]}:{detail}")
    return completed


def verify_bundle_heads(
    *,
    repository: Path,
    bundle_path: Path,
    expected_head_sha: str,
    expected_ref: str = SOURCE_BUNDLE_REF,
) -> None:
    """Require one advertised bundle head bound to the authorized commit."""

    if not SHA_RE.fullmatch(expected_head_sha):
        raise CarrierError("invalid_head_sha")
    if not bundle_path.is_file() or bundle_path.stat().st_size == 0:
        raise CarrierError("source_bundle_missing_or_empty")
    listed = _git(
        repository,
        "bundle",
        "list-heads",
        str(bundle_path),
    ).stdout.splitlines()
    expected = f"{expected_head_sha} {expected_ref}"
    if listed != [expected]:
        raise CarrierError("unexpected_source_bundle_heads")
    _git(repository, "bundle", "verify", str(bundle_path))


def create_verified_source_bundle(
    *,
    repository: Path,
    base_sha: str,
    head_sha: str,
    bundle_path: Path,
    temporary_ref: str = SOURCE_BUNDLE_REF,
) -> None:
    """Create the nested-source bundle through one isolated named ref.

    Git refuses a bundle whose positive revision is only a raw object ID
    because it has no advertised head. Bind the exact committed head to one
    temporary ref, verify that it is the bundle's sole advertised head, and
    remove the ref on every exit path.
    """

    if not SHA_RE.fullmatch(base_sha):
        raise CarrierError("invalid_base_sha")
    if not SHA_RE.fullmatch(head_sha):
        raise CarrierError("invalid_head_sha")
    if temporary_ref != SOURCE_BUNDLE_REF:
        raise CarrierError("invalid_temporary_ref")
    repository = repository.resolve()
    bundle_path = bundle_path.resolve()
    if bundle_path.exists():
        raise CarrierError("source_bundle_already_exists")
    if _git(repository, "show-ref", "--verify", "--quiet", temporary_ref, check=False).returncode == 0:
        raise CarrierError("temporary_ref_already_exists")
    if _git(repository, "cat-file", "-e", f"{base_sha}^{{commit}}", check=False).returncode:
        raise CarrierError("base_commit_missing")
    if _git(repository, "cat-file", "-e", f"{head_sha}^{{commit}}", check=False).returncode:
        raise CarrierError("head_commit_missing")
    if _git(repository, "rev-parse", "HEAD").stdout.strip() != head_sha:
        raise CarrierError("head_sha_does_not_match_checkout")
    if _git(
        repository,
        "merge-base",
        "--is-ancestor",
        base_sha,
        head_sha,
        check=False,
    ).returncode:
        raise CarrierError("base_is_not_head_ancestor")

    created_ref = False
    try:
        _git(repository, "update-ref", temporary_ref, head_sha, ZERO_SHA)
        created_ref = True
        _git(
            repository,
            "bundle",
            "create",
            str(bundle_path),
            f"{base_sha}..{temporary_ref}",
        )
        verify_bundle_heads(
            repository=repository,
            bundle_path=bundle_path,
            expected_head_sha=head_sha,
            expected_ref=temporary_ref,
        )
    except Exception:
        bundle_path.unlink(missing_ok=True)
        raise
    finally:
        if created_ref:
            _git(repository, "update-ref", "-d", temporary_ref, head_sha)

    if _git(repository, "show-ref", "--verify", "--quiet", temporary_ref, check=False).returncode == 0:
        bundle_path.unlink(missing_ok=True)
        raise CarrierError("temporary_ref_cleanup_failed")


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


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-bundle")
    create.add_argument("--repository", type=Path, required=True)
    create.add_argument("--base-sha", required=True)
    create.add_argument("--head-sha", required=True)
    create.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create_verified_source_bundle(
            repository=args.repository,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            bundle_path=args.output,
        )
    except CarrierError as error:
        print(f"source_carrier_error:{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
