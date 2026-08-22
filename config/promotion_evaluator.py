#!/usr/bin/env python3
"""Pure idempotent promotion-decision model used by release workflow tests."""

from __future__ import annotations

from typing import Any


def promotion_decision(
    *,
    pr: dict[str, Any] | None,
    expected_head: str,
    gates: dict[str, Any],
    production_contains_integration: bool,
) -> str:
    if production_contains_integration:
        return "already-promoted"
    if pr is None:
        return "open-pr"
    if pr.get("state") in {"MERGED", "CLOSED"}:
        return "already-terminal"
    if pr.get("state") != "OPEN" or pr.get("isDraft") is not False:
        return "blocked"
    if pr.get("headRefOid") != expected_head:
        return "stale"
    if gates.get("total_count", 0) <= 0 or gates.get("pending", 0) > 0:
        return "pending"
    if gates.get("failed", 0) > 0:
        return "blocked"
    return "merge"
