#!/usr/bin/env python3
"""CLI wrapper for auto-advance ownership classification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from auto_advance_ownership import Classification, classify_next_task


def write_output(classification: Classification, outputs: dict[str, str]) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    payload = {
        "decision": classification.decision,
        "reason": classification.reason,
        "ownership": classification.ownership or "",
        "evidence_relative_path": classification.evidence_relative_path or "",
        "should_dispatch": "true" if classification.decision == "implement" else "false",
        **outputs,
    }
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in payload.items():
                handle.write(f"{key}={value}\n")
    else:
        print(json.dumps(payload, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--next-task-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--issue-number", type=int, required=True)
    args = parser.parse_args()

    tasks_md_path = Path(args.package_path) / "tasks.md"
    if not tasks_md_path.is_file():
        classification = Classification("fail-closed", "missing_tasks_file")
    else:
        try:
            tasks_md = tasks_md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            classification = Classification("fail-closed", "unreadable_tasks_file")
        else:
            classification = classify_next_task(
                args.package_path, args.next_task_id, tasks_md
            )
    write_output(
        classification,
        {
            "change_id": args.change_id,
            "package_path": args.package_path,
            "task_id": args.next_task_id,
            "issue_number": str(args.issue_number),
        },
    )
    if classification.decision == "fail-closed":
        print(
            f"auto-advance: fail-closed ({classification.reason}) for {args.next_task_id}",
            file=sys.stderr,
        )
        return 0
    print(
        f"auto-advance: decision={classification.decision} task={args.next_task_id}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
