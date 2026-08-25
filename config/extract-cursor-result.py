#!/usr/bin/env python3
"""Validate one Cursor JSON response and write its non-empty result text."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


MAX_RESPONSE_BYTES = 1_048_576
MAX_RESULT_BYTES = 122_880
MAX_FAILURE_INPUT_BYTES = 65_536
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


def classify_error_text(fields: str) -> str:
    """Return a bounded reason code without returning the inspected text."""

    fields = fields.lower()
    if any(marker in fields for marker in ("usage limit", "quota", "spend limit")):
        return "usage_limit"
    if any(marker in fields for marker in ("rate limit", "too many requests", "http 429")):
        return "rate_limit"
    if any(
        marker in fields
        for marker in (
            "authentication",
            "unauthorized",
            "invalid api key",
            "api key is invalid",
        )
    ):
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


def classify_error_reason(payload: dict[str, object]) -> str:
    """Return a bounded reason code without exposing provider response text."""

    fields = " ".join(
        value.lower()
        for key in ("result", "error", "message")
        if isinstance((value := payload.get(key)), str)
    )
    return classify_error_text(fields)


def classify_failure_input(path: Path | None) -> str:
    """Inspect a bounded local diagnostic file and return only an allowlisted code."""

    if path is None:
        return "unspecified"
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FAILURE_INPUT_BYTES + 1)
    except OSError:
        return "unspecified"
    if len(raw) > MAX_FAILURE_INPUT_BYTES:
        return "unspecified"
    return classify_error_text(raw.decode("utf-8", errors="replace"))


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
    failure_record_flags = [
        flag for flag in flags if flag.startswith("--failure-record=")
    ]
    failure_input_flags = [
        flag for flag in flags if flag.startswith("--failure-input=")
    ]
    simple_flags = [
        flag
        for flag in flags
        if not flag.startswith(("--failure-record=", "--failure-input="))
    ]
    allowed_flags = {"--allow-waiting", "--github-annotation"}
    if (
        len(argv) < 3
        or len(argv) > 7
        or len(simple_flags) != len(set(simple_flags))
        or not set(simple_flags).issubset(allowed_flags)
        or len(failure_record_flags) > 1
        or any(not flag.removeprefix("--failure-record=") for flag in failure_record_flags)
        or len(failure_input_flags) > 1
        or any(not flag.removeprefix("--failure-input=") for flag in failure_input_flags)
        or (failure_input_flags and not failure_record_flags)
    ):
        print(
            "usage: extract-cursor-result.py INPUT_JSON OUTPUT_TEXT "
            "[--allow-waiting] [--github-annotation] [--failure-record=PATH] "
            "[--failure-input=PATH]",
            file=sys.stderr,
        )
        return 2
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    allow_waiting = "--allow-waiting" in flags
    github_annotation = "--github-annotation" in flags
    failure_record_path = (
        Path(failure_record_flags[0].removeprefix("--failure-record="))
        if failure_record_flags
        else None
    )
    failure_input_path = (
        Path(failure_input_flags[0].removeprefix("--failure-input="))
        if failure_input_flags
        else None
    )

    def emit_failure(error: CursorResponseError) -> None:
        safe_reason = error.reason
        if safe_reason == "unspecified":
            safe_reason = classify_failure_input(failure_input_path)
        if failure_record_path is not None:
            try:
                failure_record_path.write_text(
                    json.dumps(
                        {
                            "failure_reason": safe_reason,
                            "failure_subtype": error.subtype,
                            "schema_version": 1,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        if github_annotation:
            print(
                f"::error::Cursor invocation failed: subtype={error.subtype}, "
                f"reason={safe_reason}. "
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
