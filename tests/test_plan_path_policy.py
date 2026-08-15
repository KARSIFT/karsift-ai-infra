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

    def test_adoption_requires_passing_verification_for_merged_plan_revision(self):
        self.assertIn("The plan/-branch PR that just merged", self.adopt)
        self.assertIn("--json state,mergedAt,headRefOid", self.adopt)
        self.assertIn("bound to commit", self.adopt)
        self.assertIn("Independent verification for $head_sha is not passing", self.adopt)


if __name__ == "__main__":
    unittest.main()
