from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RemediatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/remediate.yml").read_text()
        cls.merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.review_prompt = (ROOT / "prompts/review.md").read_text()

    @staticmethod
    def _review_state(text: str) -> str:
        if re.search(
            r"^\*{0,2}VERDICT:\s*WAITING FOR OPERATOR LIVE EVIDENCE\b",
            text,
            re.MULTILINE,
        ):
            return "waiting"
        if re.search(r"^\*{0,2}VERDICT:\s*FAIL\b", text, re.MULTILINE):
            return "fail"
        return "other"

    def test_only_ci_review_failure_or_review_job_error_can_retry(self):
        self.assertIn('CI_FAILED" != "true"', self.workflow)
        self.assertIn('has_fail_verdict" = "false"', self.workflow)
        self.assertIn('REVIEW_JOB_FAILED" != "true"', self.workflow)
        self.assertIn('echo "should_retry=false"', self.workflow)

    def test_retry_is_bounded_to_two_attempts(self):
        self.assertIn("next_attempt=$((attempt + 1))", self.workflow)
        self.assertIn('if [ "$next_attempt" -gt 2 ]; then', self.workflow)
        self.assertIn("Stopping - not retrying automatically", self.workflow)

    def test_waiting_is_machine_detectable_and_does_not_retry(self):
        marker = "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE"
        self.assertIn(marker, self.review_prompt)
        self.assertEqual(self._review_state(marker), "waiting")
        self.assertEqual(self._review_state("VERDICT: FAIL"), "fail")
        self.assertIn('has_waiting_verdict" = "true"', self.workflow)
        self.assertIn('echo "should_retry=false"', self.workflow)
        waiting_guard = self.workflow.index('has_waiting_verdict" = "true"')
        retry_output = self.workflow.index('echo "should_retry=true"')
        self.assertLess(waiting_guard, retry_output)

    def test_waiting_is_bound_to_current_exact_pr_head(self):
        self.assertIn("--json body,headRefOid", self.workflow)
        self.assertIn('review_binding="bound to commit \\`$head_sha\\`"', self.workflow)
        self.assertIn(".body | contains($binding)", self.workflow)

    def test_genuine_fail_and_infrastructure_failures_still_retry(self):
        self.assertEqual(self._review_state("VERDICT: FAIL"), "fail")
        self.assertIn(
            '[ "$CI_FAILED" != "true" ] && [ "$REVIEW_JOB_FAILED" != "true" ] && [ "$has_waiting_verdict" = "true" ]',
            self.workflow,
        )
        self.assertIn('has_fail_verdict" = "false"', self.workflow)
        self.assertIn('echo "should_retry=true"', self.workflow)

    def test_implementer_has_no_general_actions_permission(self):
        permissions = self.implement.split("    permissions:\n", 1)[1].split(
            "    steps:\n", 1
        )[0]
        self.assertNotIn("actions:", permissions)
        self.assertIn("no `actions` permission", (ROOT / "README.md").read_text())

    def test_retry_reuses_implementer_with_incremented_attempt(self):
        retry = self.workflow.split("  retry:", 1)[1]
        self.assertIn("needs.decide.outputs.should_retry == 'true'", retry)
        self.assertIn("uses: KARSIFT/karsift-ai-infra/.github/workflows/implement.yml@main", retry)
        self.assertIn("attempt: ${{ needs.decide.outputs.next_attempt }}", retry)

    def test_no_founder_override_or_comment_authority(self):
        self.assertNotIn("founder_username:", self.workflow)
        self.assertNotIn("COMMENT_AUTHOR", self.workflow)
        self.assertNotIn("approve-and-merge", self.workflow)


if __name__ == "__main__":
    unittest.main()
