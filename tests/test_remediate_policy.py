from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RemediatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/remediate.yml").read_text()

    def test_only_ci_review_failure_or_review_job_error_can_retry(self):
        self.assertIn('CI_FAILED" != "true"', self.workflow)
        self.assertIn('has_fail_verdict" = "false"', self.workflow)
        self.assertIn('REVIEW_JOB_FAILED" != "true"', self.workflow)
        self.assertIn('echo "should_retry=false"', self.workflow)

    def test_retry_is_bounded_to_two_attempts(self):
        self.assertIn("next_attempt=$((attempt + 1))", self.workflow)
        self.assertIn('if [ "$next_attempt" -gt 2 ]; then', self.workflow)
        self.assertIn("Stopping - not retrying automatically", self.workflow)

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
