#!/usr/bin/env python3
"""Build one exact-revision, bounded reviewer-infrastructure failure comment."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


SHA = re.compile(r"^[0-9a-fA-F]{40}$")
SAFE_SUBTYPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SAFE_REASONS = {
    "authentication",
    "model_parameter_invalid",
    "model_unavailable_or_invalid",
    "rate_limit",
    "unspecified",
    "usage_limit",
}
MODES = {"reviewer", "plan-reviewer"}


def build_comment(
    *,
    mode: str,
    pr: dict[str, object],
    expected_head: str,
    expected_base: str,
    run_id: str,
    subtype: str,
    reason: str,
) -> str:
    if mode not in MODES:
        raise ValueError("Review failure mode is invalid.")
    if not SHA.fullmatch(expected_head) or not SHA.fullmatch(expected_base):
        raise ValueError("Review failure base/head identity is invalid.")
    if not run_id.isdigit() or run_id.startswith("0"):
        raise ValueError("Review failure run identity is invalid.")
    if not SAFE_SUBTYPE.fullmatch(subtype) or reason not in SAFE_REASONS:
        raise ValueError("Review failure classification is outside the bounded vocabulary.")
    if (
        pr.get("state") != "OPEN"
        or pr.get("headRefOid") != expected_head
        or pr.get("baseRefOid") != expected_base
    ):
        raise ValueError("PR base/head pair changed before failure publication.")
    return "\n".join(
        (
            "**Independent verification - bounded reviewer infrastructure failure**",
            "",
            "The reviewer failed before producing a verdict. This is not a finding about the reviewed change.",
            "",
            f"mode: `{mode}`",
            f"head_sha: `{expected_head}`",
            f"base_sha: `{expected_base}`",
            f"pipeline_run_id: `{run_id}`",
            f"failure_subtype: `{subtype}`",
            f"failure_reason: `{reason}`",
            "",
            "Raw provider output, prompts, environment values, and credentials are withheld.",
            "",
        )
    )


def main(argv: list[str]) -> int:
    if len(argv) != 9:
        print(
            "usage: build-review-failure-comment.py MODE PR_JSON OUTPUT_COMMENT "
            "EXPECTED_HEAD EXPECTED_BASE RUN_ID SUBTYPE REASON",
            file=sys.stderr,
        )
        return 2
    _, mode, pr_path, output_path, head, base, run_id, subtype, reason = argv
    try:
        pr = json.loads(Path(pr_path).read_text(encoding="utf-8"))
        if not isinstance(pr, dict):
            raise ValueError("PR metadata must be an object.")
        comment = build_comment(
            mode=mode,
            pr=pr,
            expected_head=head,
            expected_base=base,
            run_id=run_id,
            subtype=subtype,
            reason=reason,
        )
        Path(output_path).write_text(comment, encoding="utf-8")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
