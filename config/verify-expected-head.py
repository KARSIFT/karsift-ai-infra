#!/usr/bin/env python3
"""Fail-closed exact-head guard shared by remediation and implementation."""

from __future__ import annotations

import argparse
import re


SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


def verify(expected_sha: str, current_sha: str) -> str:
    if not expected_sha or not SHA_RE.fullmatch(expected_sha):
        return "INVALID_EXPECTED_SHA"
    if not current_sha or not SHA_RE.fullmatch(current_sha):
        return "INVALID_CURRENT_SHA"
    if expected_sha.lower() != current_sha.lower():
        return "STALE"
    return "CURRENT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--current-sha", required=True)
    args = parser.parse_args()
    print(verify(args.expected_sha, args.current_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
