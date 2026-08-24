from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkConsolidationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = (ROOT / "prompts/plan.md").read_text()
        cls.plan_review = (ROOT / "prompts/plan-review.md").read_text()
        cls.implement = (ROOT / "prompts/implement.md").read_text()
        cls.review = (ROOT / "prompts/review.md").read_text()

    def test_planner_uses_largest_safe_coherent_units(self):
        self.assertIn("largest safe, coherent change unit", self.plan)
        self.assertIn("minimum sufficient number of tasks", self.plan)
        self.assertIn("one end-to-end implementation task", self.plan)
        self.assertIn("several related skills", self.plan)
        self.assertNotIn("small,\nordered", self.plan)

    def test_size_and_component_counts_do_not_split_work(self):
        for prompt in (self.plan, self.plan_review, self.review):
            normalized = prompt.lower()
            self.assertIn("line", normalized)
            self.assertIn("component", normalized)
            self.assertIn("not", normalized)

    def test_multiple_tasks_require_real_boundaries(self):
        for boundary in (
            "authority",
            "independent release",
            "rollback",
            "hard dependency",
            "environment",
            "post-merge evidence",
            "reviewability",
        ):
            self.assertIn(boundary, self.plan)
            self.assertIn(boundary, self.plan_review)
        for prompt in (self.plan, self.plan_review):
            normalized = " ".join(prompt.split())
            self.assertIn("More than three tasks is exceptional", normalized)

    def test_implementer_keeps_causal_remediation_in_scope(self):
        self.assertIn("whole named task", self.implement)
        self.assertIn("causally in scope", self.implement)
        self.assertIn("do not create extra work merely because", self.implement)


if __name__ == "__main__":
    unittest.main()
