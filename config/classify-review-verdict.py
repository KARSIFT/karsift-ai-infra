#!/usr/bin/env python3
"""Classify the last machine-readable reviewer verdict in a text file."""

from pathlib import Path
import re
import sys


VERDICTS = (
    (re.compile(r"^\*{0,2}VERDICT:\s*WAITING FOR OPERATOR LIVE EVIDENCE\b"), "WAITING"),
    (re.compile(r"^\*{0,2}VERDICT:\s*FAIL\b"), "FAIL"),
    (
        re.compile(r"^\*{0,2}VERDICT:\s*PASS WITH NON-BLOCKING FINDINGS\b"),
        "PASS_WITH_FINDINGS",
    ),
    (re.compile(r"^\*{0,2}VERDICT:\s*PASS\b"), "PASS"),
)


def classify(text: str) -> str:
    """Return a fail-dominant recognized verdict, preserving PENDING."""
    states: set[str] = set()
    for line in text.splitlines():
        for pattern, candidate in VERDICTS:
            if pattern.search(line):
                states.add(candidate)
                break
    for state in ("FAIL", "WAITING", "PASS_WITH_FINDINGS", "PASS"):
        if state in states:
            return state
    return "PENDING"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: classify-review-verdict.py <verdict-file>", file=sys.stderr)
        return 2
    print(classify(Path(sys.argv[1]).read_text()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
