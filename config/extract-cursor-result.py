#!/usr/bin/env python3
"""Validate one Cursor JSON response and write its non-empty result text."""

from __future__ import annotations

import json
import os
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

    def __init__(
        self,
        message: str,
        *,
        subtype: str = "unspecified",
        reason: str = "unspecified",
    ) -> None:
        super().__init__(message)
        self.subtype = subtype
        self.reason = reason


def classify_error_reason(payload: dict[str, object]) -> str:
    """Return a bounded reason code without exposing provider response text."""

    fields = " ".join(
        value.lower()
        for key in ("result", "error", "message")
        if isinstance((value := payload.get(key)), str)
    )
    if any(marker in fields for marker in ("usage limit", "quota", "spend limit")):
        return "usage_limit"
    if any(marker in fields for marker in ("rate limit", "too many requests", "http 429")):
        return "rate_limit"
    if any(marker in fields for marker in ("authentication", "unauthorized", "invalid api key")):
        return "authentication"
    if "model" in fields and any(
        marker in fields
        for marker in ("not available", "not found", "unknown", "unsupported", "invalid")
    ):
        return "model_unavailable_or_invalid"
    if "parameter" in fields and any(
        marker in fields for marker in ("unsupported", "unknown", "invalid")
    ):
        return "model_parameter_invalid"
    return "unspecified"


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
        safe_reason = classify_error_reason(payload)
        raise CursorResponseError(
            "Cursor reported an application-level error "
            f"(subtype={safe_subtype}, reason={safe_reason}).",
            subtype=safe_subtype,
            reason=safe_reason,
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
    flags = argv[3:]
    allowed_flags = {
        "--allow-waiting",
        "--github-annotation",
        "--set-github-output",
    }
    if (
        len(argv) < 3
        or len(argv) > 6
        or len(flags) != len(set(flags))
        or not set(flags).issubset(allowed_flags)
    ):
        print(
            "usage: extract-cursor-result.py INPUT_JSON OUTPUT_TEXT "
            "[--allow-waiting] [--github-annotation] [--set-github-output]",
            file=sys.stderr,
        )
        return 2
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    allow_waiting = "--allow-waiting" in flags
    github_annotation = "--github-annotation" in flags
    set_github_output = "--set-github-output" in flags

    def emit_failure(error: CursorResponseError) -> None:
        if set_github_output:
            github_output = os.environ.get("GITHUB_OUTPUT", "")
            if github_output:
                try:
                    with Path(github_output).open("a", encoding="utf-8") as sink:
                        sink.write(f"failure_subtype={error.subtype}\n")
                        sink.write(f"failure_reason={error.reason}\n")
                except OSError:
                    pass
        if github_annotation:
            print(
                f"::error::Cursor invocation failed: {error} "
                "Raw provider output is withheld.",
                file=sys.stdout,
            )
        else:
            print(str(error), file=sys.stderr)

    try:
        output_path.unlink(missing_ok=True)
        result = extract_result(input_path.read_bytes(), allow_waiting=allow_waiting)
        output_path.write_text(result, encoding="utf-8")
    except CursorResponseError as exc:
        emit_failure(exc)
        return 75
    except OSError:
        emit_failure(CursorResponseError("Cursor response could not be read or written."))
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
