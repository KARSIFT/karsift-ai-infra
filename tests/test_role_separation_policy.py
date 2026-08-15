from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def active_role(config: str, role: str) -> str:
    matches = re.findall(rf"^{re.escape(role)}:\s*(\S+)\s*$", config, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(f"expected one active {role} binding, found {matches}")
    return matches[0]


class RoleSeparationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = (ROOT / "config/roles.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.review = (ROOT / ".github/workflows/review.yml").read_text()
        cls.implement_prompt = (ROOT / "prompts/implement.md").read_text()
        cls.review_prompt = (ROOT / "prompts/review.md").read_text()

    def test_implementer_and_reviewer_bindings_are_distinct(self):
        self.assertNotEqual(
            active_role(self.roles, "implementer"),
            active_role(self.roles, "reviewer"),
        )

    def test_workflows_resolve_their_own_roles(self):
        self.assertIn('role="implementer"', self.implement)
        self.assertIn('role="reviewer"', self.review)
        self.assertNotIn('role="reviewer"', self.implement)

    def test_implementer_prompt_forbids_self_review_and_merge(self):
        self.assertIn("You cannot merge your own work", self.implement_prompt)
        self.assertIn("cannot mark yourself as the independent reviewer", self.implement_prompt)

    def test_reviewer_prompt_is_read_only_and_cannot_grant_authority(self):
        self.assertIn("Do not edit any file", self.review_prompt)
        self.assertIn("cannot grant founder or steward approval", self.review_prompt)
        self.assertIn("no repository-write, merge, deployment", self.review_prompt)

    def test_reviewer_verdict_is_commit_bound(self):
        self.assertIn("Bind your report to the exact commit SHA", self.review_prompt)
        self.assertIn("VERDICT: FAIL", self.review_prompt)
        self.assertIn("VERDICT: PASS", self.review_prompt)


if __name__ == "__main__":
    unittest.main()
