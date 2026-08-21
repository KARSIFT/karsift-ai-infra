from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RemediatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / ".github/workflows/remediate.yml").read_text()
        cls.merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.review_prompt = (ROOT / "prompts/review.md").read_text()

    def test_only_ci_or_genuine_review_failure_can_retry(self):
        self.assertIn("config/decide-remediation.py", self.workflow)
        self.assertIn('--ci-failed "$CI_FAILED"', self.workflow)
        self.assertIn('--review-job-failed "$REVIEW_JOB_FAILED"', self.workflow)
        self.assertIn('echo "should_retry=false"', self.workflow)

    def test_retry_is_bounded_to_two_attempts(self):
        self.assertIn("next_attempt=$((attempt + 1))", self.workflow)
        self.assertIn('if [ "$next_attempt" -gt 2 ]; then', self.workflow)
        self.assertIn("Stopping - not retrying automatically", self.workflow)

    def test_waiting_is_machine_detectable_and_does_not_retry(self):
        marker = "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE"
        self.assertIn(marker, self.review_prompt)
        self.assertIn("config/classify-review-verdict.py", self.workflow)
        self.assertIn('decision" = "WAITING"', self.workflow)
        self.assertIn('echo "should_retry=false"', self.workflow)
        waiting_guard = self.workflow.index('decision" = "WAITING"')
        retry_output = self.workflow.index('echo "should_retry=true"')
        self.assertLess(waiting_guard, retry_output)

    def test_waiting_is_bound_to_current_exact_pr_head(self):
        self.assertIn("--json body,headRefOid,baseRefOid", self.workflow)
        self.assertIn('review_header="**Independent verification - bound to commit', self.workflow)
        self.assertIn('base_line="base_sha:', self.workflow)
        self.assertIn('index($base)', self.workflow)
        self.assertIn('.user.login == "karsift-ai-infra-bot[bot]"', self.workflow)
        self.assertIn('.user.type == "Bot"', self.workflow)
        self.assertIn('package_line="package_path:', self.workflow)
        self.assertIn("--paginate --slurp", self.workflow)

    def test_genuine_fail_retries_but_review_infrastructure_is_suppressed(self):
        self.assertIn('decision" != "RETRY"', self.workflow)
        self.assertIn('echo "should_retry=true"', self.workflow)
        self.assertIn('decision" = "REVIEW_INFRA_FAILURE"', self.workflow)
        self.assertIn('echo "review_infrastructure_failure=true"', self.workflow)
        self.assertIn(
            "steps.parse.outputs.review_infrastructure_failure == 'true'",
            self.workflow,
        )
        self.assertIn("without implementation retry", self.workflow)
        retry_decision = self.workflow.split(
            'if [ "$decision" != "RETRY" ]', 1
        )[1].split("- name: Record sanitized CI failure metadata", 1)[0]
        self.assertNotIn("Review job errored (no verdict)", retry_decision)
        self.assertNotIn("hit a review-job error", retry_decision)
        metadata_step = self.workflow.split(
            "- name: Record sanitized review-job-error metadata", 1
        )[1].split("\n  retry:", 1)[0]
        self.assertNotIn("VERDICT: FAIL", metadata_step)
        self.assertIn("--json headRefOid,baseRefOid,state", metadata_step)
        self.assertIn('head_sha: \\`$EXPECTED_HEAD_SHA\\`', metadata_step)
        self.assertIn('base_sha: \\`$EXPECTED_BASE_SHA\\`', metadata_step)
        self.assertLess(
            metadata_step.index("PR base/head pair changed"),
            metadata_step.index('gh pr comment "$PR_NUMBER"'),
        )

    def test_ci_failure_context_is_metadata_only_and_retry_reproduces_checks(self):
        ci_metadata = self.workflow.split(
            "- name: Record sanitized CI failure metadata without log replay", 1
        )[1].split("- name: Record sanitized review-job-error metadata", 1)[0]
        self.assertNotIn("/actions/jobs/", ci_metadata)
        self.assertNotIn("/logs", ci_metadata)
        self.assertNotIn("/artifacts", ci_metadata)
        self.assertNotIn("--allow-escape-sequences", ci_metadata)
        self.assertNotIn("gh pr comment", ci_metadata)
        self.assertIn('run_id: \\`$sanitized_run_id\\`', ci_metadata)
        self.assertIn('head_sha: \\`$EXPECTED_HEAD_SHA\\`', ci_metadata)
        self.assertIn('base_sha: \\`$EXPECTED_BASE_SHA\\`', ci_metadata)
        self.assertIn("$GITHUB_STEP_SUMMARY", ci_metadata)
        self.assertIn("previous attempt failed deterministic CI before review", self.implement)
        self.assertIn("Reproduce the failure in this", self.implement)
        self.assertIn("failed_head_sha: ${{ inputs.expected_head_sha }}", self.implement)

    def test_stale_caller_run_cannot_dispatch_newer_head(self):
        self.assertIn("expected_head_sha:", self.workflow)
        self.assertIn("expected_base_sha:", self.workflow)
        self.assertIn('--expected-sha "$EXPECTED_HEAD_SHA"', self.workflow)
        self.assertIn('base_sha" != "$EXPECTED_BASE_SHA', self.workflow)
        self.assertIn('initial_decision" = "STALE"', self.workflow)
        self.assertIn('echo "stale_run=true"', self.workflow)

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
        self.assertIn("expected_base_sha: ${{ inputs.expected_base_sha }}", retry)

    def test_no_founder_override_or_comment_authority(self):
        self.assertNotIn("founder_username:", self.workflow)
        self.assertNotIn("COMMENT_AUTHOR", self.workflow)
        self.assertNotIn("approve-and-merge", self.workflow)


if __name__ == "__main__":
    unittest.main()
