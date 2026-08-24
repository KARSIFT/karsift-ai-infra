#!/usr/bin/env python3
"""CLI wrapper for deterministic package task policy validation."""

from __future__ import annotations

import argparse
from pathlib import Path

from package_task_policy import (
    PackageTaskPolicyError,
    change_id_from_package_path,
    validate_package_tasks,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("--package-path", required=True)
    parser.add_argument("--change-id")
    args = parser.parse_args()

    package_path = Path(args.package_path)
    change_id = args.change_id or change_id_from_package_path(str(package_path))
    try:
        sections = validate_package_tasks(
            (package_path / "tasks.md").read_text(encoding="utf-8"),
            change_id,
        )
    except (OSError, UnicodeError) as exc:
        raise SystemExit(f"tasks_md_unreadable: {exc}") from exc
    except PackageTaskPolicyError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"package_task_policy_passed: tasks={len(sections)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
