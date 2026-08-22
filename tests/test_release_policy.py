from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleasePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release = (ROOT / ".github/workflows/release.yml").read_text()
        cls.template = (ROOT / "templates/project-repo/.github/workflows/pipeline.yml").read_text()

    def test_founder_comment_is_not_release_authority(self):
        self.assertNotIn("  promote:", self.release)
        self.assertNotIn("COMMENT_AUTHOR", self.release)
        self.assertNotIn("COMMENT_BODY", self.release)
        self.assertIn("Deprecated compatibility input", self.release)

    def test_every_entrypoint_converges_on_one_serialized_job(self):
        self.assertIn("github.event_name == 'issues'", self.release)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.release)
        self.assertIn("github.event_name == 'check_run'", self.release)
        self.assertIn("github.event_name == 'workflow_run'", self.release)
        self.assertEqual(self.release.count("  converge:"), 1)
        self.assertIn("group: release-converge-", self.release)
        self.assertEqual(self.release.count("gh pr merge"), 1)

    def test_completion_uses_shared_marker_validator_not_closed_state(self):
        self.assertIn("task-completion-runner.py validate-task", self.release)
        self.assertIn("task-completion-runner.py validate-roster", self.release)
        self.assertNotIn("all_closed", self.release)

    def test_terminal_check_wakes_only_release_evaluation(self):
        self.assertIn("check_run:\n    types: [completed]", self.template)
        self.assertIn("workflow_run:", self.template)
        self.assertIn("github.event_name == 'check_run'", self.template)
        self.assertIn("release_reevaluation", self.release)
        self.assertNotIn("workflow run pipeline", self.release)

    def test_promotion_checks_are_paginated_authoritative_and_sha_bound(self):
        self.assertIn("authoritative-checks-runner.py", self.release)
        self.assertIn("--paginate --slurp", self.release)
        self.assertIn("--match-head-commit \"$CHECKED_HEAD_SHA\"", self.release)
        self.assertIn('headRefOid <<<"$live")" != "$CHECKED_HEAD_SHA', self.release)
        self.assertNotIn("statusCheckRollup", self.release)
        self.assertNotIn("gh pr checks", self.release)


if __name__ == "__main__":
    unittest.main()
