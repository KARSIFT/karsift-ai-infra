import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from release_reevaluation import should_reevaluate  # noqa: E402


class ReleaseReevaluationTests(unittest.TestCase):
    def event(self, **check_overrides):
        check = {
            "status": "completed",
            "head_sha": "a" * 40,
            "pull_requests": [
                {
                    "head": {"ref": "develop", "sha": "a" * 40},
                    "base": {"ref": "main"},
                }
            ],
        }
        check.update(check_overrides)
        return {"repository": {"full_name": "KARSIFT/caller"}, "check_run": check}

    def test_terminal_promotion_check_wakes_cheap_evaluation(self):
        self.assertTrue(
            should_reevaluate(
                self.event(), repository="KARSIFT/caller", integration_branch="develop", production_branch="main"
            )
        )

    def test_external_check_without_pr_array_binds_to_unique_live_promotion(self):
        event = self.event(pull_requests=[])
        promotion = {
            "state": "OPEN",
            "headRefName": "develop",
            "headRefOid": "a" * 40,
            "baseRefName": "main",
        }
        self.assertTrue(
            should_reevaluate(
                event, repository="KARSIFT/caller", integration_branch="develop",
                production_branch="main", promotion_pr=promotion
            )
        )

    def test_completed_external_workflow_run_wakes_same_evaluator(self):
        event = {
            "repository": {"full_name": "KARSIFT/caller"},
            "workflow_run": {
                "status": "completed",
                "head_sha": "a" * 40,
                "pull_requests": [],
            },
        }
        promotion = {
            "state": "OPEN", "headRefName": "develop",
            "headRefOid": "a" * 40, "baseRefName": "main",
        }
        self.assertTrue(should_reevaluate(
            event, repository="KARSIFT/caller", integration_branch="develop",
            production_branch="main", promotion_pr=promotion
        ))

    def test_nonterminal_foreign_or_nonpromotion_event_is_ignored(self):
        cases = [
            self.event(status="in_progress"),
            self.event(pull_requests=[]),
            self.event(pull_requests=[{"head": {"ref": "feature", "sha": "a" * 40}, "base": {"ref": "main"}}]),
            {**self.event(), "repository": {"full_name": "foreign/repo"}},
        ]
        for event in cases:
            self.assertFalse(
                should_reevaluate(event, repository="KARSIFT/caller", integration_branch="develop", production_branch="main")
            )


if __name__ == "__main__":
    unittest.main()
