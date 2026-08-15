from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleasePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = (ROOT / ".github/workflows/release.yml").read_text()
        cls.template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def test_founder_comment_is_not_release_authority(self):
        self.assertNotIn("  promote:", self.release)
        self.assertNotIn("COMMENT_AUTHOR", self.release)
        self.assertNotIn("COMMENT_BODY", self.release)
        self.assertIn("  retry-promote:", self.release)

    def test_completed_roster_promotes_without_opt_in(self):
        auto = self.release.split("  auto-promote:", 1)[1].split(
            "  retry-promote:", 1
        )[0]
        self.assertNotIn("inputs.auto_release_enabled == 'true'", auto)
        self.assertIn("needs.check-and-open.outputs.release_issue_number != ''", auto)

    def test_retry_is_dispatch_driven_and_caller_wired(self):
        self.assertIn("inputs.release_issue_number != ''", self.release)
        self.assertIn("reconcile-release", self.template)
        self.assertIn("release_issue_number:", self.template)

    def test_promotion_checks_use_rest_and_bind_the_checked_head(self):
        auto = self.release.split("  auto-promote:", 1)[1].split(
            "  retry-promote:", 1
        )[0]
        retry = self.release.split("  retry-promote:", 1)[1]
        for job in (auto, retry):
            self.assertIn("checks: read", job)
            self.assertIn("statuses: read", job)
        sections = self.release.split("- name: Wait for every promotion PR check")[1:]
        self.assertEqual(2, len(sections))
        for section in sections:
            checks = section.split("- name: Merge checked promotion PR", 1)[0]
            self.assertIn("/check-runs?per_page=100", checks)
            self.assertIn("/status\"", checks)
            self.assertIn('current_sha" != "$checked_sha', checks)
            self.assertIn("stable_count", checks)
            self.assertNotIn("statusCheckRollup", checks)
            self.assertNotIn("gh pr checks", checks)


if __name__ == "__main__":
    unittest.main()
