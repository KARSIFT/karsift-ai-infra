import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from branch_sync import (  # noqa: E402
    BranchSyncError,
    governed_main_only_sync_plan,
    promotion_sync_plan,
)


REPOSITORY = "KARSIFT/caller"
HEAD = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40


def promotion_pr(**overrides):
    value = {
        "number": 17,
        "state": "closed",
        "merged_at": "2026-08-27T00:00:00Z",
        "merge_commit_sha": MERGE,
        "head": {
            "ref": "develop",
            "sha": HEAD,
            "repo": {"full_name": REPOSITORY},
        },
        "base": {"ref": "main", "sha": BASE},
    }
    value.update(overrides)
    return value


def merge_commit(*, head=HEAD, base=BASE, target=MERGE):
    return {"sha": target, "parents": [{"sha": base}, {"sha": head}]}


def comparison(*, integration=HEAD, ahead=1, behind=0):
    return {
        "merge_base_commit": {"sha": integration},
        "ahead_by": ahead,
        "behind_by": behind,
    }


def promotion(**overrides):
    inputs = {
        "repository": REPOSITORY,
        "pr_number": 17,
        "pull_request": promotion_pr(),
        "merge_commit": merge_commit(),
        "integration_branch": "develop",
        "production_branch": "main",
        "expected_head_sha": HEAD,
        "expected_base_sha": BASE,
        "integration_sha": HEAD,
        "production_sha": MERGE,
        "comparison": comparison(),
    }
    inputs.update(overrides)
    return promotion_sync_plan(**inputs)


class PromotionBranchSyncTests(unittest.TestCase):
    def test_exact_checked_merge_advances_integration(self):
        plan = promotion()
        self.assertEqual(plan.action, "update")
        self.assertEqual(plan.expected_integration_sha, HEAD)
        self.assertEqual(plan.target_sha, MERGE)

    def test_missing_ref_is_created_and_exact_retry_is_noop(self):
        self.assertEqual(promotion(integration_sha=None, comparison=None).action, "create")
        self.assertEqual(
            promotion(integration_sha=MERGE, comparison=None).action, "noop"
        )

    def test_changed_refs_and_unique_integration_fail_closed(self):
        cases = (
            {"production_sha": "d" * 40},
            {
                "integration_sha": "d" * 40,
                "comparison": comparison(integration="d" * 40),
            },
            {"comparison": comparison(integration=BASE)},
            {"comparison": comparison(behind=1)},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(BranchSyncError):
                promotion(**values)

    def test_stale_pr_or_non_exact_merge_parents_fail_closed(self):
        cases = (
            {"pull_request": promotion_pr(state="open", merged_at=None)},
            {"pull_request": promotion_pr(head={"ref": "other", "sha": HEAD})},
            {"expected_head_sha": "d" * 40},
            {"merge_commit": merge_commit(head="d" * 40)},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(BranchSyncError):
                promotion(**values)


class GovernedMainOnlySyncTests(unittest.TestCase):
    def setUp(self):
        self.marker = {
            "repository": REPOSITORY,
            "authority_issue": "9",
            "package_path": "specs/changes/VOC-127-example",
            "task_id": "VOC-127-T00",
            "pr_number": "21",
            "reviewed_head_sha": HEAD,
            "merge_commit_sha": MERGE,
            "merged_at": "2026-08-27T00:00:00Z",
        }
        self.pr = {
            "number": 21,
            "state": "closed",
            "merged_at": self.marker["merged_at"],
            "merge_commit_sha": MERGE,
            "head": {
                "ref": "agent/voc-127-voc-127-t00",
                "sha": HEAD,
                "repo": {"full_name": REPOSITORY},
            },
            "base": {"ref": "main", "sha": BASE},
        }

    def plan(self, **overrides):
        values = {
            "repository": REPOSITORY,
            "marker": self.marker,
            "pull_request": self.pr,
            "merge_commit": merge_commit(),
            "integration_branch": "develop",
            "production_branch": "main",
            "integration_sha": BASE,
            "production_sha": MERGE,
            "comparison": comparison(integration=BASE),
        }
        values.update(overrides)
        return governed_main_only_sync_plan(**values)

    def test_completed_main_task_can_advance_ancestor_integration(self):
        self.assertEqual(self.plan().action, "update")

    def test_retry_is_noop(self):
        self.assertEqual(
            self.plan(integration_sha=MERGE, comparison=None).action, "noop"
        )

    def test_foreign_non_agent_or_stale_task_proof_fails_closed(self):
        cases = (
            {"pull_request": {**self.pr, "base": {"ref": "develop", "sha": BASE}}},
            {
                "pull_request": {
                    **self.pr,
                    "head": {**self.pr["head"], "ref": "hotfix/manual"},
                }
            },
            {"marker": {**self.marker, "merge_commit_sha": "d" * 40}},
            {"merge_commit": merge_commit(base="d" * 40)},
            {"integration_sha": None, "comparison": None},
            {"production_sha": "d" * 40},
            {"comparison": comparison(integration=HEAD)},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(BranchSyncError):
                self.plan(**values)


if __name__ == "__main__":
    unittest.main()
