#!/usr/bin/env python3
"""Classify the post-implementer nested infrastructure checkout safely."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


class NestedCheckoutError(ValueError):
    """The nested path cannot be proven to be a distinct Git checkout."""


def classify_nested_checkout(path: Path) -> str:
    """Return ``absent`` or ``valid``; reject every ambiguous surviving path."""

    if path.is_symlink():
        raise NestedCheckoutError("nested_checkout_symlink")
    if not path.exists():
        return "absent"
    if not path.is_dir():
        raise NestedCheckoutError("nested_checkout_not_directory")

    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise NestedCheckoutError("nested_checkout_not_git")

    reported = completed.stdout.strip()
    if not reported:
        raise NestedCheckoutError("nested_checkout_missing_root")
    try:
        actual_root = Path(reported).resolve(strict=True)
        expected_root = path.resolve(strict=True)
    except OSError as exc:
        raise NestedCheckoutError("nested_checkout_unresolvable_root") from exc
    if actual_root != expected_root:
        raise NestedCheckoutError("nested_checkout_inherits_parent_git")
    return "valid"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        print(classify_nested_checkout(args.path))
    except NestedCheckoutError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
