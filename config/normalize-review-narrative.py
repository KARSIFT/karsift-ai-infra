#!/usr/bin/env python3
"""Remove workflow-owned binding lines from an untrusted review narrative."""

from __future__ import annotations

from pathlib import Path
import re
import sys


MAX_NARRATIVE_BYTES = 122_880
RESERVED_BINDING = re.compile(
    r"(?:"
    r"Independent verification\s*-\s*bound to commit\b.*"
    r"|(?:task_id|package_path|authority_issue|base_sha|pipeline_run_id)\s*:.*"
    r")",
    re.IGNORECASE,
)
VERDICT = re.compile(
    r"VERDICT: (?:FAIL|PASS|PASS WITH NON-BLOCKING FINDINGS|"
    r"WAITING FOR OPERATOR LIVE EVIDENCE)"
)
CONSUMER_VERDICT = re.compile(
    r"^\*{0,2}VERDICT:\s*(?:FAIL|PASS(?:\s+WITH NON-BLOCKING FINDINGS)?|"
    r"WAITING FOR OPERATOR LIVE EVIDENCE)\b"
)


class NarrativeError(ValueError):
    """The model narrative cannot be safely placed in a signed record."""


def presentation_text(line: str) -> str:
    """Return text after common whole-line Markdown presentation wrappers."""
    candidate = line.strip()
    while True:
        previous = candidate
        candidate = re.sub(r"^(?:>\s*)+", "", candidate)
        candidate = re.sub(r"^(?:[-+*]\s+)", "", candidate)
        candidate = re.sub(r"^\d{1,9}[.)]\s+", "", candidate)
        candidate = re.sub(r"^#{1,6}\s+", "", candidate)
        for marker in ("**", "__", "~~", "`", "*", "_"):
            if (
                candidate.startswith(marker)
                and candidate.endswith(marker)
                and len(candidate) > 2 * len(marker)
            ):
                candidate = candidate[len(marker) : -len(marker)].strip()
                break
        if candidate == previous:
            break
    return candidate


def normalize_narrative(raw: bytes) -> str:
    if not raw or len(raw) > MAX_NARRATIVE_BYTES:
        raise NarrativeError("Review narrative is empty or exceeds its size limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NarrativeError("Review narrative is not UTF-8.") from exc

    lines = [
        line
        for line in text.splitlines()
        if not RESERVED_BINDING.fullmatch(presentation_text(line))
    ]
    normalized = "\n".join(lines).strip()
    if not normalized:
        raise NarrativeError("Review narrative is empty after binding normalization.")

    normalized_lines = normalized.splitlines()
    final_line = next((line for line in reversed(normalized_lines) if line.strip()), "")
    consumer_verdicts = [
        line for line in normalized_lines if CONSUMER_VERDICT.search(line.strip())
    ]
    if (
        not VERDICT.fullmatch(final_line)
        or consumer_verdicts != [final_line]
    ):
        raise NarrativeError(
            "Review narrative must end in one unambiguous machine verdict."
        )
    return normalized + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: normalize-review-narrative.py INPUT_TEXT OUTPUT_TEXT",
            file=sys.stderr,
        )
        return 2
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    try:
        output_path.unlink(missing_ok=True)
        output_path.write_text(
            normalize_narrative(input_path.read_bytes()),
            encoding="utf-8",
        )
    except NarrativeError as exc:
        print(str(exc), file=sys.stderr)
        return 75
    except OSError:
        print("Review narrative could not be read or written.", file=sys.stderr)
        return 75
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
