#!/usr/bin/env python3
"""Resolve a safe caller checkout ref for release and missing-ref recovery."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,198}[A-Za-z0-9])?$")


class ResolutionError(RuntimeError):
    """Bounded release-checkout resolution failure."""


def _validate_identity(repository: str, integration: str, production: str) -> None:
    if not REPOSITORY_RE.fullmatch(repository):
        raise ResolutionError("repository_identity_invalid")
    for label, branch in (("integration", integration), ("production", production)):
        if (
            not BRANCH_RE.fullmatch(branch)
            or ".." in branch
            or "//" in branch
            or branch.endswith(".lock")
        ):
            raise ResolutionError(f"{label}_branch_invalid")
    if integration == production:
        raise ResolutionError("release_branch_pair_invalid")


def _gh_json(path: str) -> Any:
    completed = subprocess.run(
        ["gh", "api", path],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ResolutionError("github_ref_lookup_failed")
    if len(completed.stdout.encode("utf-8")) > 1_048_576:
        raise ResolutionError("github_ref_payload_oversized")
    try:
        return json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ResolutionError("github_ref_payload_invalid") from exc


def resolve_checkout_ref(
    repository: str,
    integration: str,
    production: str,
) -> str:
    _validate_identity(repository, integration, production)
    refs = _gh_json(
        f"repos/{repository}/git/matching-refs/heads/{integration}"
    )
    if not isinstance(refs, list):
        raise ResolutionError("integration_ref_payload_invalid")
    exact = [entry for entry in refs if entry.get("ref") == f"refs/heads/{integration}"]
    if len(exact) > 1:
        raise ResolutionError("integration_ref_ambiguous")
    if exact:
        target = exact[0].get("object")
        if (
            not isinstance(target, dict)
            or target.get("type") != "commit"
            or not SHA_RE.fullmatch(str(target.get("sha", "")))
        ):
            raise ResolutionError("integration_ref_identity_invalid")
        return integration

    production_ref = _gh_json(f"repos/{repository}/git/ref/heads/{production}")
    if not isinstance(production_ref, dict):
        raise ResolutionError("production_ref_payload_invalid")
    target = production_ref.get("object")
    if (
        not isinstance(target, dict)
        or target.get("type") != "commit"
        or not SHA_RE.fullmatch(str(target.get("sha", "")))
    ):
        raise ResolutionError("production_ref_identity_invalid")
    return production


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--integration-branch", required=True)
    parser.add_argument("--production-branch", required=True)
    parser.add_argument("--github-output", required=True)
    args = parser.parse_args()

    try:
        checkout_ref = resolve_checkout_ref(
            args.repository,
            args.integration_branch,
            args.production_branch,
        )
        output_path = Path(args.github_output)
        with output_path.open("a", encoding="utf-8") as output:
            output.write(f"ref={checkout_ref}\n")
    except (OSError, ResolutionError) as exc:
        reason = str(exc) if isinstance(exc, ResolutionError) else "github_output_write_failed"
        print(f"release-checkout-ref: {reason}", file=sys.stderr)
        return 1

    print(f"release-checkout-ref: selected {checkout_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
