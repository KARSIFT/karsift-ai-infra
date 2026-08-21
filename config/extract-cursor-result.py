#!/usr/bin/env python3
"""Validate one Cursor JSON response and write its non-empty result text."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


MAX_RESPONSE_BYTES = 1_048_576
MAX_RESULT_BYTES = 122_880
SAFE_SUBTYPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
VERDICT = re.compile(
    r"VERDICT: (?:FAIL|PASS|PASS WITH NON-BLOCKING FINDINGS|"
    r"WAITING FOR OPERATOR LIVE EVIDENCE)"
)


class CursorResponseError(ValueError):
    """The CLI response cannot be used as reviewer output."""


def extract_result(raw: bytes, *, allow_waiting: bool = False) -> str:
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
    is_error = payload.get("is_error")
    if not isinstance(is_error, bool):
        raise CursorResponseError("Cursor response has no valid boolean error state.")
    if is_error:
        subtype = payload.get("subtype")
        safe_subtype = subtype if isinstance(subtype, str) and SAFE_SUBTYPE.fullmatch(subtype) else "unspecified"
        raise CursorResponseError(
            f"Cursor reported an application-level error (subtype={safe_subtype})."
        )
    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise CursorResponseError("Cursor response has no non-empty result text.")
    if len(result.encode("utf-8")) > MAX_RESULT_BYTES:
        raise CursorResponseError("Cursor result exceeds the bounded size limit.")
    lines = result.splitlines()
    verdicts = [line for line in lines if VERDICT.fullmatch(line)]
    final_line = next((line for line in reversed(lines) if line.strip()), "")
    if len(verdicts) != 1 or verdicts[0] != final_line:
        raise CursorResponseError(
            "Cursor result must end in one unambiguous machine verdict."
        )
    if not allow_waiting and verdicts[0] == "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE":
        raise CursorResponseError("Cursor result contains a verdict unavailable in this review mode.")
    return result


def main(argv: list[str]) -> int:
    if len(argv) not in {3, 4} or (len(argv) == 4 and argv[3] != "--allow-waiting"):
        print(
            "usage: extract-cursor-result.py INPUT_JSON OUTPUT_TEXT [--allow-waiting]",
            file=sys.stderr,
        )
        return 2
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    try:
        output_path.unlink(missing_ok=True)
        result = extract_result(input_path.read_bytes(), allow_waiting=len(argv) == 4)
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
