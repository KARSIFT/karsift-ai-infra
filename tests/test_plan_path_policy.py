from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PlanPathPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan_review = (ROOT / ".github/workflows/plan-review.yml").read_text()
        cls.task_review = (ROOT / ".github/workflows/review.yml").read_text()
        cls.adopt = (ROOT / ".github/workflows/adopt.yml").read_text()

    def test_plan_and_task_paths_resolve_distinct_roles(self):
        self.assertIn("resolve-model.sh plan_reviewer", self.plan_review)
        self.assertIn('role="reviewer"', self.task_review)
        self.assertNotIn("resolve-model.sh plan_reviewer", self.task_review)

    def test_plan_and_task_paths_load_distinct_prompts(self):
        self.assertIn("prompts/plan-review.md", self.plan_review)
        self.assertIn("prompts/review.md", self.task_review)
        self.assertNotIn("prompts/review.md", self.plan_review)

    def test_plan_review_posts_commit_bound_machine_readable_verdict(self):
        self.assertIn("Independent verification - bound to commit", self.plan_review)
        self.assertIn("steps.pr.outputs.sha", self.plan_review)
        self.assertIn("/tmp/verdict.md", self.plan_review)
        reviewer, publisher = self.plan_review.split("\n  publish-plan-review:", 1)
        self.assertNotIn("gh pr comment", reviewer)
        self.assertNotIn("create-github-app-token@", reviewer)
        self.assertIn("actions/download-artifact@", publisher)
        self.assertIn("permission-issues: write", publisher)
        self.assertIn("App-signed plan verification", publisher)

    def test_plan_review_is_bound_to_the_callers_immutable_event_head(self):
        self.assertIn("expected_head_sha:", self.plan_review)
        input_block = self.plan_review.split("expected_head_sha:", 1)[1].split("secrets:", 1)[0]
        self.assertIn("required: true", input_block)
        self.assertIn('EXPECTED_HEAD_SHA: ${{ inputs.expected_head_sha }}', self.plan_review)
        self.assertIn('if [ "$live_sha" != "$EXPECTED_HEAD_SHA" ]', self.plan_review)
        self.assertIn('if [ "$after_diff_sha" != "$EXPECTED_HEAD_SHA" ]', self.plan_review)
        self.assertLess(
            self.plan_review.index('gh pr view "$PR_NUMBER" --json body,title,headRefOid'),
            self.plan_review.index('gh pr diff "$PR_NUMBER"'),
        )

    def test_adoption_requires_passing_verification_for_merged_plan_revision(self):
        self.assertIn("The plan/-branch PR that just merged", self.adopt)
        self.assertIn("--json state,mergedAt,headRefOid", self.adopt)
        self.assertIn("bound to commit", self.adopt)
        self.assertIn('plan-review / publish-plan-review', self.adopt)
        self.assertIn('.user.login == "karsift-ai-infra-bot[bot]"', self.adopt)
        self.assertIn("Independent verification for $head_sha is not passing", self.adopt)


if __name__ == "__main__":
    unittest.main()
