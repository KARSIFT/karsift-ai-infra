#!/usr/bin/env python3
"""Fail-closed roster PR carrier resolution for adopt.yml (VOC-142).

Pure resolution is exact-SHA identity only. Post-push open-PR head.sha lag is
handled by roster-carrier-runner.py via bounded metadata-convergence polling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ROSTER_HEAD_RE = re.compile(r"^karsift/roster-[a-z0-9-]+$")
ROSTER_CARRIER_METADATA_CONVERGENCE_TIMEOUT_SECONDS = 60
ROSTER_CARRIER_METADATA_POLL_INTERVAL_SECONDS = 2


@dataclass(frozen=True)
class RosterCarrierResult:
    action: str
    pr_number: str | None = None


@dataclass(frozen=True)
class RosterCarrierFailure:
    code: str
    detail: str = ""


def _valid_sha(value: str) -> bool:
    return bool(value and SHA_RE.fullmatch(value))


def _carrier_identity_matches(
    pr: Mapping[str, Any],
    *,
    repository: str,
    head_ref: str,
    head_sha: str,
    base_ref: str,
) -> bool:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name")
    base_repo = (base.get("repo") or {}).get("full_name")
    return (
        head_repo == repository
        and base_repo == repository
        and str(head.get("ref") or "") == head_ref
        and str(head.get("sha") or "") == head_sha
        and str(base.get("ref") or "") == base_ref
    )


def open_carrier_metadata_lag_is_transient(
    *,
    repository: str,
    head_ref: str,
    head_sha: str,
    base_ref: str,
    open_pulls: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """Return True when exactly one open carrier matches repo/ref/base but not head.sha."""

    stale_matches = 0
    for pr in open_pulls or []:
        if not isinstance(pr, Mapping):
            continue
        if str(pr.get("state") or "").lower() != "open":
            continue
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        head_repo = (head.get("repo") or {}).get("full_name")
        base_repo = (base.get("repo") or {}).get("full_name")
        if (
            head_repo == repository
            and base_repo == repository
            and str(head.get("ref") or "") == head_ref
            and str(base.get("ref") or "") == base_ref
            and str(head.get("sha") or "") != head_sha
        ):
            stale_matches += 1
    return stale_matches == 1


def _filter_matching_carriers(
    pulls: Sequence[Mapping[str, Any]],
    *,
    repository: str,
    head_ref: str,
    head_sha: str,
    base_ref: str,
    lifecycle: str,
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for pr in pulls:
        if not isinstance(pr, Mapping):
            continue
        # GitHub's REST pull-request resource never reports state="merged":
        # merged PRs are closed records with a non-null merged_at.  Keep this
        # API boundary explicit so a synthetic state vocabulary cannot make
        # the already-merged reuse path appear covered when it is not live.
        state = str(pr.get("state") or "").lower()
        if lifecycle == "open":
            if state != "open":
                continue
        elif lifecycle == "merged":
            if state != "closed" or not pr.get("merged_at"):
                continue
        else:
            raise ValueError("invalid carrier lifecycle")
            continue
        if _carrier_identity_matches(
            pr,
            repository=repository,
            head_ref=head_ref,
            head_sha=head_sha,
            base_ref=base_ref,
        ):
            matches.append(pr)
    return matches


def resolve_roster_carrier(
    *,
    repository: str,
    head_ref: str,
    head_sha: str,
    base_ref: str,
    open_pulls: Sequence[Mapping[str, Any]] | None,
    merged_pulls: Sequence[Mapping[str, Any]] | None,
) -> RosterCarrierResult | RosterCarrierFailure:
    """Resolve whether adopt should reuse, create, or fail closed."""

    if repository.count("/") != 1 or not repository.strip():
        return RosterCarrierFailure("INVALID_REPOSITORY")
    if not ROSTER_HEAD_RE.fullmatch(head_ref):
        return RosterCarrierFailure("INVALID_HEAD_REF")
    if not _valid_sha(head_sha):
        return RosterCarrierFailure("INVALID_HEAD_SHA")
    if not base_ref or any(character.isspace() for character in base_ref):
        return RosterCarrierFailure("INVALID_BASE_REF")

    open_matches = _filter_matching_carriers(
        open_pulls or [],
        repository=repository,
        head_ref=head_ref,
        head_sha=head_sha,
        base_ref=base_ref,
        lifecycle="open",
    )
    if len(open_matches) > 1:
        return RosterCarrierFailure("AMBIGUOUS_OPEN_CARRIER")
    if len(open_matches) == 1:
        number = str(open_matches[0].get("number") or "")
        if not number:
            return RosterCarrierFailure("INVALID_OPEN_CARRIER")
        return RosterCarrierResult(action="reuse_open", pr_number=number)

    merged_matches = _filter_matching_carriers(
        merged_pulls or [],
        repository=repository,
        head_ref=head_ref,
        head_sha=head_sha,
        base_ref=base_ref,
        lifecycle="merged",
    )
    if len(merged_matches) > 1:
        return RosterCarrierFailure("AMBIGUOUS_MERGED_CARRIER")
    if len(merged_matches) == 1:
        number = str(merged_matches[0].get("number") or "")
        if not number:
            return RosterCarrierFailure("INVALID_MERGED_CARRIER")
        return RosterCarrierResult(action="reuse_merged", pr_number=number)

    # Mismatched carriers with the same head ref must fail closed rather than
    # creating a second PR or retargeting an unrelated carrier.
    for pr in open_pulls or []:
        if not isinstance(pr, Mapping):
            continue
        head = pr.get("head") or {}
        if str(head.get("ref") or "") == head_ref:
            return RosterCarrierFailure("MISMATCHED_OPEN_CARRIER")
    for pr in merged_pulls or []:
        if not isinstance(pr, Mapping):
            continue
        head = pr.get("head") or {}
        if str(head.get("ref") or "") == head_ref:
            return RosterCarrierFailure("MISMATCHED_MERGED_CARRIER")

    return RosterCarrierResult(action="create")
