from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LifecycleWorkflowPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adopt = (ROOT / ".github/workflows/adopt.yml").read_text()
        cls.merge = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.advance = (ROOT / ".github/workflows/auto-advance.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.prompt = (ROOT / "prompts/implement.md").read_text()

    def test_authoritative_selector_is_used_by_adopt_merge_and_release(self):
        release = (ROOT / ".github/workflows/release.yml").read_text()
        for workflow in (self.adopt, self.merge, release):
            self.assertIn("authoritative-checks-runner.py", workflow)
            self.assertIn("--paginate --slurp", workflow)

    def test_marker_is_published_after_merge_and_shared_by_consumers(self):
        merge_command = self.merge.index('gh pr merge "$PR_NUMBER"')
        publish_command = self.merge.index("task-completion-runner.py publish")
        self.assertLess(merge_command, publish_command)
        self.assertIn("task-completion-runner.py validate-task", self.advance)

    def test_local_closing_binding_and_cross_repo_policy_both_remain(self):
        self.assertIn("Closes #${{ inputs.issue_number }}", self.implement)
        self.assertIn("Relates to OWNER/CALLER#N", self.prompt)
        for keyword in ("close", "fix", "resolve"):
            self.assertIn(keyword, self.prompt)


if __name__ == "__main__":
    unittest.main()
