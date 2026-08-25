from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "build_review_failure_comment",
    ROOT / "config/build-review-failure-comment.py",
)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("cannot load review failure comment helper")
failure_comment = module_from_spec(SPEC)
sys.modules[SPEC.name] = failure_comment
SPEC.loader.exec_module(failure_comment)


HEAD = "a" * 40
BASE = "b" * 40
LIVE_PR = {"state": "OPEN", "headRefOid": HEAD, "baseRefOid": BASE}


class ReviewFailureCommentTests(unittest.TestCase):
    def test_builds_exact_revision_bounded_comment(self):
        comment = failure_comment.build_comment(
            mode="reviewer",
            pr=LIVE_PR,
            expected_head=HEAD,
            expected_base=BASE,
            run_id="12345",
            subtype="error_during_execution",
            reason="model_unavailable_or_invalid",
        )
        self.assertIn(f"head_sha: `{HEAD}`", comment)
        self.assertIn(f"base_sha: `{BASE}`", comment)
        self.assertIn("failure_subtype: `error_during_execution`", comment)
        self.assertIn("failure_reason: `model_unavailable_or_invalid`", comment)
        self.assertIn("Raw provider output", comment)

    def test_rejects_stale_or_unbounded_identity_and_classification(self):
        cases = (
            {"expected_head": "c" * 40},
            {"reason": "provider said a secret-like arbitrary message"},
            {"subtype": "bad\n::error::injection"},
            {"run_id": "0"},
            {"mode": "implementer"},
        )
        defaults = {
            "mode": "reviewer",
            "pr": LIVE_PR,
            "expected_head": HEAD,
            "expected_base": BASE,
            "run_id": "12345",
            "subtype": "error_during_execution",
            "reason": "unspecified",
        }
        for override in cases:
            with self.subTest(override=override), self.assertRaises(ValueError):
                failure_comment.build_comment(**(defaults | override))

    def test_cli_writes_only_validated_comment(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            pr_path = scratch_path / "pr.json"
            output_path = scratch_path / "comment.md"
            pr_path.write_text(json.dumps(LIVE_PR), encoding="utf-8")
            rc = failure_comment.main(
                [
                    "build-review-failure-comment.py",
                    "plan-reviewer",
                    str(pr_path),
                    str(output_path),
                    HEAD,
                    BASE,
                    "98765",
                    "error_during_execution",
                    "rate_limit",
                ]
            )
            self.assertEqual(rc, 0)
            comment = output_path.read_text(encoding="utf-8")
            self.assertIn("mode: `plan-reviewer`", comment)
            self.assertIn("failure_reason: `rate_limit`", comment)


if __name__ == "__main__":
    unittest.main()
