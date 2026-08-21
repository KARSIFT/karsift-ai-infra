#!/usr/bin/env python3
"""Classify whether remediation may dispatch an implementer at an exact checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from auto_advance_ownership import Classification, classify_next_task


def fail_closed(reason: str) -> Classification:
    return Classification("fail-closed", reason)


def classify(repository_root: Path, package_path: str, task_id: str) -> Classification:
    root = repository_root.resolve()
    relative = Path(package_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or len(relative.parts) < 3
        or relative.parts[:2] != ("specs", "changes")
    ):
        return fail_closed("invalid_package_path")

    package = (root / relative).resolve()
    try:
        package.relative_to(root)
    except ValueError:
        return fail_closed("package_outside_repository")

    tasks_path = package / "tasks.md"
    try:
        tasks_md = tasks_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return fail_closed("missing_tasks_file")
    except (OSError, UnicodeError):
        return fail_closed("unreadable_tasks_file")
    return classify_next_task(str(package), task_id, tasks_md)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()

    result = classify(args.repository_root, args.package_path, args.task_id)
    state = {
        "implement": "ORDINARY",
        "prepare-live-evidence": "OPERATOR",
        "fail-closed": "FAIL_CLOSED",
        "none": "FAIL_CLOSED",
    }[result.decision]
    reason = result.reason or (
        "declared_live_evidence_ownership" if state == "OPERATOR" else "ordinary_task"
    )
    print(
        json.dumps(
            {
                "state": state,
                "reason": reason,
                "ownership": result.ownership or "",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
