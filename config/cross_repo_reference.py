#!/usr/bin/env python3
"""Generate and validate repository-safe GitHub issue references."""

from __future__ import annotations

import re


CLOSING_KEYWORDS = r"close[sd]?|fix(?:e[sd])?|resolve[sd]?"
CLOSING_REFERENCE_RE = re.compile(
    rf"(?i)\b(?:{CLOSING_KEYWORDS})\s+(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+\b"
)


def issue_reference(authority_repository: str, target_repository: str, issue: int) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", authority_repository):
        raise ValueError("invalid authority repository")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target_repository):
        raise ValueError("invalid target repository")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError("invalid issue number")
    if authority_repository.casefold() == target_repository.casefold():
        return f"Closes #{issue}."
    return f"Relates to {authority_repository}#{issue}."


def reject_cross_repository_closing_text(
    text: str, *, authority_repository: str, target_repository: str
) -> None:
    if authority_repository.casefold() == target_repository.casefold():
        return
    if CLOSING_REFERENCE_RE.search(text):
        raise ValueError("cross-repository text contains a GitHub closing reference")

