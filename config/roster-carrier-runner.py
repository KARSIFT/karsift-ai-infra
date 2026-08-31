#!/usr/bin/env python3
"""CLI adapter for roster PR carrier resolution (VOC-142).

After a force-with-lease roster push, GitHub can briefly report the previous
open-PR head.sha. This runner polls until that metadata converges to the exact
locally pushed SHA or a non-transient failure/timeout is reached.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from roster_carrier import (
    ROSTER_CARRIER_METADATA_CONVERGENCE_TIMEOUT_SECONDS,
    ROSTER_CARRIER_METADATA_POLL_INTERVAL_SECONDS,
    RosterCarrierResult,
    open_carrier_metadata_lag_is_transient,
    resolve_roster_carrier,
)


def gh_json(command: list[str]) -> object:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "gh command failed")
    return json.loads(completed.stdout or "null")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    owner = args.repository.split("/", 1)[0]
    def flatten_pages(pages: object) -> list[dict]:
        if not isinstance(pages, list):
            return []
        items: list[dict] = []
        for page in pages:
            if isinstance(page, dict) and isinstance(page.get("items"), list):
                batch = page.get("items")
            elif isinstance(page, dict) and isinstance(page.get("pulls"), list):
                batch = page.get("pulls")
            elif isinstance(page, list):
                batch = page
            else:
                batch = []
            items.extend(item for item in batch if isinstance(item, dict))
        return items

    def fetch_pull_pages(state: str) -> list[dict]:
        pages = gh_json(
            [
                "gh",
                "api",
                "-X",
                "GET",
                "--paginate",
                "--slurp",
                f"repos/{args.repository}/pulls",
                "-f",
                f"state={state}",
                "-f",
                f"head={owner}:{args.head_ref}",
                "-f",
                "per_page=100",
            ]
        )
        return flatten_pages(pages)

    deadline = time.monotonic() + ROSTER_CARRIER_METADATA_CONVERGENCE_TIMEOUT_SECONDS
    while True:
        try:
            open_pulls = fetch_pull_pages("open")
            merged_pulls = fetch_pull_pages("closed")
        except (RuntimeError, json.JSONDecodeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

        result = resolve_roster_carrier(
            repository=args.repository,
            head_ref=args.head_ref,
            head_sha=args.head_sha,
            base_ref=args.base_ref,
            open_pulls=open_pulls,
            merged_pulls=merged_pulls,
        )
        if isinstance(result, RosterCarrierResult):
            payload = {
                "action": result.action,
                "pr_number": result.pr_number,
            }
            Path(args.output).write_text(
                json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
            )
            return 0

        if (
            result.code == "MISMATCHED_OPEN_CARRIER"
            and open_carrier_metadata_lag_is_transient(
                repository=args.repository,
                head_ref=args.head_ref,
                head_sha=args.head_sha,
                base_ref=args.base_ref,
                open_pulls=open_pulls,
            )
            and time.monotonic() < deadline
        ):
            time.sleep(ROSTER_CARRIER_METADATA_POLL_INTERVAL_SECONDS)
            continue

        print(result.code, file=sys.stderr)
        if result.detail:
            print(result.detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
