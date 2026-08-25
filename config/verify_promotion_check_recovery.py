#!/usr/bin/env python3
"""Read-only proof that a promotion PR has genuine exact-head required checks."""

from __future__ import annotations

from dataclasses import dataclass
import re

from actions_check_recovery import (
    PROMOTION_REQUIRED_CONTEXTS,
    missing_contexts,
    select_gate_evidence,
    validate_sha,
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str = ""


def verify_promotion_pr_identity(
    pr: dict,
    *,
    repository: str,
    pr_number: int,
    expected_head_sha: str | None = None,
) -> VerificationResult:
    state = pr.get("state")
    if state not in {"open", "closed"}:
        return VerificationResult(False, "promotion_pr_not_terminal_or_open")
    if state == "closed" and pr.get("merged") is not True:
        return VerificationResult(False, "promotion_pr_closed_without_merge")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    if head_repo != repository:
        return VerificationResult(False, "wrong_head_repository")
    if pr.get("number") != pr_number:
        return VerificationResult(False, "wrong_pr_number")
    head_sha = head.get("sha")
    if not isinstance(head_sha, str) or not SHA_RE.fullmatch(head_sha):
        return VerificationResult(False, "invalid_head_sha")
    if expected_head_sha is not None:
        try:
            validate_sha(expected_head_sha, "expected_head_sha")
        except ValueError:
            return VerificationResult(False, "invalid_expected_head_sha")
        if head_sha != expected_head_sha:
            return VerificationResult(False, "head_sha_mismatch")
    if base.get("ref") != "main" or head.get("ref") != "develop":
        return VerificationResult(False, "not_promotion_pair")
    return VerificationResult(True)


def verify_required_checks(
    gate_summary: dict,
    *,
    head_sha: str,
    pr_required_checks: list[dict],
) -> VerificationResult:
    try:
        validate_sha(head_sha, "head_sha")
    except ValueError:
        return VerificationResult(False, "invalid_head_sha")
    missing = missing_contexts(
        gate_summary,
        PROMOTION_REQUIRED_CONTEXTS,
        pr_required_checks=pr_required_checks,
    )
    if missing:
        return VerificationResult(False, f"missing_required_contexts:{','.join(missing)}")
    # Promotion authority is defined by the three required contexts above.
    # Other exact-head workflows can legitimately be pending, skipped, or
    # failed (for example, a release convergence wake-up that ran before a
    # recovered validate retry). They must remain visible in GitHub history,
    # but they are not promotion-check authority and cannot veto this proof.
    return VerificationResult(True)


def verify_carrier_ref(current_ref: str) -> VerificationResult:
    """Validate this verifier's carrier SHA without conflating it with the promotion head.

    The live-evidence observer binds the workflow run's head SHA to the carrier PR.
    Promotion identity and exact-head check evidence are validated independently.
    """
    if not SHA_RE.fullmatch(current_ref):
        return VerificationResult(False, "invalid_current_ref")
    return VerificationResult(True)
