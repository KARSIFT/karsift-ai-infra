#!/usr/bin/env python3
"""CLI for the fail-closed production merge-base rule guard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from production_merge_guard import (
    ProductionMergeGuardError,
    validate_production_merge_guard,
)


def _json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionMergeGuardError("production_merge_rules_unreadable") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--effective-rules-file", required=True)
    parser.add_argument("--rulesets-file", required=True)
    args = parser.parse_args()
    try:
        ruleset_id = validate_production_merge_guard(
            repository=args.repository,
            effective_rules=_json(args.effective_rules_file),
            rulesets=_json(args.rulesets_file),
        )
    except ProductionMergeGuardError as exc:
        print(f"production-merge-guard: {exc}", file=sys.stderr)
        return 1
    print(f"production-merge-guard: ok ruleset_id={ruleset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
