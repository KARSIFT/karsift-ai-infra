from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "extract_cursor_result",
    ROOT / "config/extract-cursor-result.py",
)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("cannot load Cursor result helper")
cursor_result = module_from_spec(SPEC)
sys.modules[SPEC.name] = cursor_result
SPEC.loader.exec_module(cursor_result)


class CursorResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review_workflows = (
            (ROOT / ".github/workflows/review.yml").read_text(),
            (ROOT / ".github/workflows/plan-review.yml").read_text(),
        )
        cls.retry_helper = (ROOT / "config/retry-helpers.sh").read_text()

    def test_extracts_documented_non_empty_result(self):
        raw = json.dumps({"is_error": False, "result": "VERDICT: PASS"}).encode()
        self.assertEqual(cursor_result.extract_result(raw), "VERDICT: PASS")

    def test_content_free_success_is_rejected_for_bounded_retry(self):
        for payload in ({"is_error": False}, {"is_error": False, "result": "  "}):
            with self.subTest(payload=payload), self.assertRaises(
                cursor_result.CursorResponseError
            ):
                cursor_result.extract_result(json.dumps(payload).encode())

    def test_malformed_error_state_and_non_verdict_result_are_rejected(self):
        fixtures = (
            {"is_error": "false", "result": "VERDICT: PASS"},
            {"result": "VERDICT: PASS"},
            {"is_error": False, "result": "unable to review"},
            {"is_error": False, "result": "VERDICT: PASS\ntrailing text"},
            {
                "is_error": False,
                "result": "VERDICT: PASS\nVERDICT: FAIL",
            },
        )
        for payload in fixtures:
            with self.subTest(payload=payload), self.assertRaises(
                cursor_result.CursorResponseError
            ):
                cursor_result.extract_result(json.dumps(payload).encode())

    def test_waiting_verdict_is_task_review_only(self):
        raw = json.dumps(
            {
                "is_error": False,
                "result": "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE",
            }
        ).encode()
        with self.assertRaises(cursor_result.CursorResponseError):
            cursor_result.extract_result(raw)
        self.assertEqual(
            cursor_result.extract_result(raw, allow_waiting=True),
            "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE",
        )

    def test_error_diagnostic_never_echoes_arbitrary_result_content(self):
        secret_like = "arbitrary-provider-payload-must-not-be-echoed"
        with self.assertRaises(cursor_result.CursorResponseError) as raised:
            cursor_result.extract_result(
                json.dumps(
                    {
                        "is_error": True,
                        "subtype": "rate_limit",
                        "result": secret_like,
                    }
                ).encode()
            )
        self.assertIn("subtype=rate_limit", str(raised.exception))
        self.assertIn("reason=unspecified", str(raised.exception))
        self.assertNotIn(secret_like, str(raised.exception))

    def test_error_diagnostic_classifies_only_allowlisted_reason_codes(self):
        fixtures = (
            ("You've hit your usage limit for this billing cycle", "usage_limit"),
            ("HTTP 429: too many requests", "rate_limit"),
            ("Authentication failed: invalid API key", "authentication"),
            ("Requested model is not available", "model_unavailable_or_invalid"),
            ("Invalid parameter override", "model_parameter_invalid"),
        )
        for provider_text, expected in fixtures:
            with self.subTest(expected=expected), self.assertRaises(
                cursor_result.CursorResponseError
            ) as raised:
                cursor_result.extract_result(
                    json.dumps(
                        {
                            "is_error": True,
                            "subtype": "error_during_execution",
                            "result": provider_text,
                        }
                    ).encode()
                )
            diagnostic = str(raised.exception)
            self.assertIn(f"reason={expected}", diagnostic)
            self.assertNotIn(provider_text, diagnostic)

    def test_github_annotation_exposes_only_the_bounded_diagnostic(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            input_path = scratch_path / "response.json"
            output_path = scratch_path / "verdict.md"
            failure_record = scratch_path / "failure.json"
            provider_text = "Requested model is not available; secret-like tail"
            input_path.write_text(
                json.dumps(
                    {
                        "is_error": True,
                        "subtype": "error_during_execution",
                        "result": provider_text,
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "config/extract-cursor-result.py"),
                    str(input_path),
                    str(output_path),
                    "--allow-waiting",
                    "--github-annotation",
                    f"--failure-record={failure_record}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 75)
            self.assertIn("::error::Cursor invocation failed:", completed.stdout)
            self.assertIn("reason=model_unavailable_or_invalid", completed.stdout)
            self.assertIn("Raw provider output is withheld.", completed.stdout)
            self.assertNotIn(provider_text, completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(output_path.exists())
            self.assertEqual(
                json.loads(failure_record.read_text(encoding="utf-8")),
                {
                    "failure_reason": "model_unavailable_or_invalid",
                    "failure_subtype": "error_during_execution",
                    "schema_version": 1,
                },
            )

    def test_github_annotation_uses_bounded_codes_for_io_failure(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            missing_input = scratch_path / "provider-response-missing.json"
            output_path = scratch_path / "verdict.md"
            failure_record = scratch_path / "failure.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "config/extract-cursor-result.py"),
                    str(missing_input),
                    str(output_path),
                    "--github-annotation",
                    f"--failure-record={failure_record}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 75)
            self.assertEqual(
                completed.stdout.strip(),
                "::error::Cursor invocation failed: subtype=unspecified, "
                "reason=unspecified. Raw provider output is withheld.",
            )
            self.assertNotIn(str(missing_input), completed.stdout)
            self.assertEqual(completed.stderr, "")
            self.assertFalse(output_path.exists())
            self.assertEqual(
                json.loads(failure_record.read_text(encoding="utf-8")),
                {
                    "failure_reason": "unspecified",
                    "failure_subtype": "unspecified",
                    "schema_version": 1,
                },
            )

    def test_empty_response_uses_bounded_stderr_only_for_safe_classification(self):
        fixtures = (
            ("Authentication failed: invalid API key", "authentication"),
            ("Requested model is not available", "model_unavailable_or_invalid"),
            ("Invalid parameter override", "model_parameter_invalid"),
            ("You've hit your usage limit", "usage_limit"),
            ("HTTP 429: too many requests", "rate_limit"),
        )
        for diagnostic, expected_reason in fixtures:
            with (
                self.subTest(expected_reason=expected_reason),
                tempfile.TemporaryDirectory() as scratch,
            ):
                scratch_path = Path(scratch)
                input_path = scratch_path / "response.json"
                output_path = scratch_path / "verdict.md"
                failure_input = scratch_path / "cursor-stderr.log"
                failure_record = scratch_path / "failure.json"
                input_path.write_bytes(b"")
                failure_input.write_text(
                    f"{diagnostic}; secret-like tail must stay private",
                    encoding="utf-8",
                )
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "config/extract-cursor-result.py"),
                        str(input_path),
                        str(output_path),
                        "--github-annotation",
                        f"--failure-record={failure_record}",
                        f"--failure-input={failure_input}",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 75)
                self.assertIn(f"reason={expected_reason}", completed.stdout)
                self.assertNotIn(diagnostic, completed.stdout + completed.stderr)
                self.assertNotIn("secret-like", completed.stdout + completed.stderr)
                self.assertEqual(
                    json.loads(failure_record.read_text(encoding="utf-8")),
                    {
                        "failure_reason": expected_reason,
                        "failure_subtype": "unspecified",
                        "schema_version": 1,
                    },
                )

    def test_failure_input_is_bounded_and_requires_a_failure_record(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            input_path = scratch_path / "response.json"
            output_path = scratch_path / "verdict.md"
            failure_input = scratch_path / "cursor-stderr.log"
            failure_record = scratch_path / "failure.json"
            input_path.write_bytes(b"")
            failure_input.write_bytes(
                b"Authentication failed: invalid API key\n"
                + b"x" * cursor_result.MAX_FAILURE_INPUT_BYTES
            )
            invalid = cursor_result.main(
                [
                    "extract-cursor-result.py",
                    str(input_path),
                    str(output_path),
                    f"--failure-input={failure_input}",
                ]
            )
            self.assertEqual(invalid, 2)
            bounded = cursor_result.main(
                [
                    "extract-cursor-result.py",
                    str(input_path),
                    str(output_path),
                    f"--failure-record={failure_record}",
                    f"--failure-input={failure_input}",
                ]
            )
            self.assertEqual(bounded, 75)
            self.assertEqual(
                json.loads(failure_record.read_text(encoding="utf-8"))[
                    "failure_reason"
                ],
                "unspecified",
            )

    def test_review_failure_paths_emit_sanitized_diagnostics_only(self):
        for workflow in self.review_workflows:
            with self.subTest(workflow=workflow.splitlines()[0]):
                self.assertIn("--github-annotation", workflow)
                self.assertIn("--failure-record=", workflow)
                self.assertIn("--failure-input=/tmp/cursor-stderr.log", workflow)
                self.assertIn("Upload bounded", workflow)
                self.assertIn("Download bounded", workflow)
                self.assertIn("actions/upload-artifact@", workflow)
                self.assertIn("actions/download-artifact@", workflow)
                self.assertIn("if-no-files-found: error", workflow)
                self.assertNotIn("steps.verify.outputs.failure_reason", workflow)
                self.assertIn("build-review-failure-comment.py", workflow)
                self.assertIn("ref: ${{ job.workflow_sha }}", workflow)
                self.assertIn("permission-pull-requests: write", workflow)
                self.assertNotIn("cat /tmp/cursor-stderr.log >&2", workflow)

    def test_invalid_or_oversized_responses_fail_closed(self):
        fixtures = (
            b"not-json",
            json.dumps(["not", "an", "object"]).encode(),
            b"x" * (cursor_result.MAX_RESPONSE_BYTES + 1),
        )
        for raw in fixtures:
            with self.subTest(size=len(raw)), self.assertRaises(
                cursor_result.CursorResponseError
            ):
                cursor_result.extract_result(raw)

    def test_failed_parse_removes_stale_output(self):
        with tempfile.TemporaryDirectory() as scratch:
            input_path = Path(scratch) / "response.json"
            output_path = Path(scratch) / "verdict.md"
            input_path.write_text('{"is_error": false}', encoding="utf-8")
            output_path.write_text("stale verdict", encoding="utf-8")
            self.assertEqual(
                cursor_result.main(
                    ["extract-cursor-result.py", str(input_path), str(output_path)]
                ),
                75,
            )
            self.assertFalse(output_path.exists())

    def test_both_reviewers_validate_inside_the_bounded_retry(self):
        for workflow in self.review_workflows:
            with self.subTest(workflow=workflow.splitlines()[0]):
                run_review = workflow.split("          run_review() {", 1)[1].split(
                    "          }", 1
                )[0]
                self.assertIn("config/extract-cursor-result.py", run_review)
                self.assertIn("if agent -p --trust --mode plan", run_review)
                self.assertIn('agent_rc=$?', run_review)
                self.assertNotIn("if ! agent", run_review)
                self.assertLess(
                    workflow.index("config/extract-cursor-result.py"),
                    workflow.index("retry_if_transient"),
                )
        self.assertEqual(self.review_workflows[0].count("--allow-waiting"), 3)
        self.assertNotIn("--allow-waiting", self.review_workflows[1])

    def test_tempfail_result_validation_is_always_bounded_retry_eligible(self):
        self.assertIn('[ "$rc" -ne 75 ]', self.retry_helper)
        self.assertIn('"$attempt" -ge "$max_extra_attempts"', self.retry_helper)

        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            clock_path = scratch_path / "clock"
            log_path = scratch_path / "large.log"
            clock_path.write_text("0\n", encoding="utf-8")
            log_path.write_text("x" * 500, encoding="utf-8")
            script = textwrap.dedent(
                f"""
                source {ROOT / 'config/retry-helpers.sh'}
                date() {{
                  clock_value=$(< {clock_path})
                  printf '%s\\n' "$clock_value"
                  printf '%s\\n' "$((clock_value + 61))" > {clock_path}
                }}
                sleep() {{ :; }}
                attempts=0
                content_free() {{
                  attempts=$((attempts + 1))
                  return 75
                }}
                if retry_if_transient {log_path} -- content_free; then
                  rc=0
                else
                  rc=$?
                fi
                printf 'rc=%s attempts=%s\\n' "$rc" "$attempts"
                """
            )
            completed = subprocess.run(
                ["bash", "-c", script],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("rc=75 attempts=3", completed.stdout)


if __name__ == "__main__":
    unittest.main()
