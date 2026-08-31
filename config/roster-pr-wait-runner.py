#!/usr/bin/env python3
"""CLI adapter for roster PR wait completeness (VOC-142)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from roster_pr_wait import RosterWaitError, roster_wait_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-runs-file", required=True)
    parser.add_argument("--statuses-file", required=True)
    parser.add_argument("--pull-request-file", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--infra-config-dir", default="karsift-ai-infra/config")
    args = parser.parse_args()

    runner = Path(args.infra_config_dir) / "authoritative-checks-runner.py"
    intermediate = Path(args.output).with_suffix(".authoritative.json")
    command = [
        sys.executable,
        str(runner),
        "--check-runs-file",
        args.check_runs_file,
        "--statuses-file",
        args.statuses_file,
        "--pull-request-file",
        args.pull_request_file,
        "--repository",
        args.repository,
        "--head-sha",
        args.head_sha,
        "--base-sha",
        args.base_sha,
        "--pr-number",
        str(args.pr_number),
        "--workflow-event",
        "pull_request",
        "--roster-pr-gate",
        "--output",
        str(intermediate),
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        return completed.returncode

    try:
        evaluate_result = json.loads(intermediate.read_text(encoding="utf-8"))
        snapshot = roster_wait_snapshot(evaluate_result)
    except (OSError, json.JSONDecodeError, RosterWaitError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    Path(args.output).write_text(
        json.dumps(snapshot, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
