#!/usr/bin/env python3
"""Validate the server-side rule that atomically binds a promotion base."""

from __future__ import annotations

from typing import Any


class ProductionMergeGuardError(ValueError):
    """Production is not protected by a non-bypassable strict status rule."""


OPERATOR_PAYLOAD_INCOMPLETE_ACTION = (
    "configure karsift-ai-infra-bot Repository permissions Administration: "
    "Read and write, obtain installation-owner approval on KARSIFT organization "
    "installation 148001476, retain the workflow explicit single-repository guard "
    "token scope for the current caller repository, do not rotate secrets, and "
    "rerun the failed guard or reconcile-release"
)


def _has_required_checks(parameters: dict[str, Any]) -> bool:
    checks = parameters.get("required_status_checks")
    return (
        isinstance(checks, list)
        and len(checks) > 0
        and all(
            isinstance(check, dict)
            and isinstance(check.get("context"), str)
            and bool(check["context"].strip())
            for check in checks
        )
    )


def validate_production_merge_guard(
    *,
    repository: str,
    effective_rules: Any,
    rulesets: Any,
) -> int:
    """Return the qualifying ruleset ID or fail closed.

    GitHub's strict required-status-check policy is the server-side guarantee
    that a PR head must still be up to date with its base at merge time.  The
    qualifying rule must be repository-owned, active, require PRs, and expose
    no bypass actors.  The effective-rules response proves that GitHub applies
    the ruleset to the production branch being merged.
    """
    if not isinstance(effective_rules, list) or not isinstance(rulesets, list):
        raise ProductionMergeGuardError("production_merge_rules_invalid")

    effective_ids: set[int] = set()
    for rule in effective_rules:
        if not isinstance(rule, dict):
            continue
        parameters = rule.get("parameters") or {}
        ruleset_id = rule.get("ruleset_id")
        if (
            rule.get("type") == "required_status_checks"
            and parameters.get("strict_required_status_checks_policy") is True
            and _has_required_checks(parameters)
            and rule.get("ruleset_source_type") == "Repository"
            and rule.get("ruleset_source") == repository
            and isinstance(ruleset_id, int)
        ):
            effective_ids.add(ruleset_id)

    for ruleset in rulesets:
        if not isinstance(ruleset, dict) or ruleset.get("id") not in effective_ids:
            continue
        rules = ruleset.get("rules")
        bypass_actors = ruleset.get("bypass_actors")
        if bypass_actors is None or not isinstance(bypass_actors, list):
            raise ProductionMergeGuardError("production_merge_guard_payload_incomplete")
        if (
            ruleset.get("enforcement") != "active"
            or ruleset.get("target") != "branch"
            or ruleset.get("source_type") != "Repository"
            or ruleset.get("source") != repository
            or bypass_actors != []
            or not isinstance(rules, list)
        ):
            continue
        has_pull_request = any(
            isinstance(rule, dict) and rule.get("type") == "pull_request"
            for rule in rules
        )
        has_strict_checks = False
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters") or {}
            if (
                parameters.get("strict_required_status_checks_policy") is True
                and _has_required_checks(parameters)
            ):
                has_strict_checks = True
                break
        if has_pull_request and has_strict_checks:
            return ruleset["id"]

    raise ProductionMergeGuardError("production_merge_guard_missing")
