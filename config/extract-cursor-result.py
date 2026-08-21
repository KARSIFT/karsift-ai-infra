#!/usr/bin/env python3
"""Validate one Cursor JSON response and write its non-empty result text."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


MAX_RESPONSE_BYTES = 1_048_576
SAFE_SUBTYPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class CursorResponseError(ValueError):
    """The CLI response cannot be used as reviewer output."""


def extract_result(raw: bytes) -> str:
    if not raw:
        raise CursorResponseError("Cursor response is empty.")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CursorResponseError("Cursor response exceeds the bounded size limit.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorResponseError("Cursor response is not one valid UTF-8 JSON object.") from exc
    if not isinstance(payload, dict):
        raise CursorResponseError("Cursor response must be a JSON object.")
    if payload.get("is_error") is True:
        subtype = payload.get("subtype")
        safe_subtype = subtype if isinstance(subtype, str) and SAFE_SUBTYPE.fullmatch(subtype) else "unspecified"
        raise CursorResponseError(
            f"Cursor reported an application-level error (subtype={safe_subtype})."
        )
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise CursorResponseError("Cursor response has no non-empty result text.")
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: extract-cursor-result.py INPUT_JSON OUTPUT_TEXT", file=sys.stderr)
        return 2
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    try:
        output_path.unlink(missing_ok=True)
        result = extract_result(input_path.read_bytes())
        output_path.write_text(result, encoding="utf-8")
    except CursorResponseError as exc:
        print(str(exc), file=sys.stderr)
        return 75
    except OSError:
        print("Cursor response could not be read or written.", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
