from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MergeGatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.auto_merge = cls.workflow.split("  auto-merge:", 1)[1]

    def test_r0_through_r4_share_the_autonomous_merge_path(self):
        self.assertIn("Master switch for automatic merge at R0-R4", self.workflow)
        self.assertNotIn("risk != 'R4'", self.auto_merge)
        self.assertNotIn("risk == 'R4'", self.auto_merge)
        self.assertNotIn("automatic_merge_allowed", self.auto_merge)

    def test_unparseable_risk_fails_closed(self):
        self.assertIn('risk="unknown"', self.workflow)
        self.assertIn("needs.report-status.outputs.risk != 'unknown'", self.auto_merge)

    def test_ci_and_independent_verdict_are_hard_gates(self):
        self.assertIn("needs.report-status.outputs.checks_ok == 'true'", self.auto_merge)
        self.assertIn("needs.report-status.outputs.verdict != 'FAIL'", self.auto_merge)
        self.assertIn("needs.report-status.outputs.verdict != 'WAITING'", self.auto_merge)
        self.assertIn("needs.report-status.outputs.verdict != 'PENDING'", self.auto_merge)

    def test_founder_comment_is_not_an_override_path(self):
        self.assertNotIn("  approve-and-merge:", self.workflow)
        self.assertNotIn("COMMENT_AUTHOR", self.workflow)
        self.assertNotIn("COMMENT_BODY", self.workflow)
        self.assertIn("Deprecated compatibility input", self.workflow)

    def test_verdict_uses_shared_fail_dominant_classifier(self):
        self.assertIn("config/classify-review-verdict.py", self.workflow)

    def test_only_current_exact_sha_review_is_considered(self):
        self.assertIn("--json body,author,headRefName,headRefOid", self.workflow)
        self.assertIn('review_header="**Independent verification - bound to commit', self.workflow)
        self.assertIn('.user.login == "karsift-ai-infra-bot[bot]"', self.workflow)
        self.assertIn('.user.type == "Bot"', self.workflow)
        self.assertIn('review / publish-review', self.workflow)
        self.assertIn('plan-review / publish-plan-review', self.workflow)
        self.assertIn("--paginate --slurp", self.workflow)

    def test_merge_rechecks_the_reviewed_head_atomically(self):
        self.assertIn("expected_head_sha:", self.workflow)
        self.assertIn("head_sha: ${{ steps.status.outputs.head_sha }}", self.workflow)
        self.assertIn("--match-head-commit \"$REVIEWED_HEAD_SHA\"", self.auto_merge)


if __name__ == "__main__":
    unittest.main()
