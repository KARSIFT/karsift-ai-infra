import argparse
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
SPEC = importlib.util.spec_from_file_location(
    "branch_sync_runner", ROOT / "config/branch-sync-runner.py"
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


SHA = "a" * 40


def args(**overrides):
    values = {
        "mode": "governed-main-only",
        "repository": "KARSIFT/caller",
        "integration_branch": "develop",
        "production_branch": "main",
        "authority_issue_number": 9,
        "skip_ineligible": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class BranchSyncRunnerEligibilityTests(unittest.TestCase):
    @mock.patch.object(runner, "governed_marker")
    @mock.patch.object(runner, "ref_sha")
    def test_ordinary_issue_close_is_a_mutation_free_ineligible_noop(
        self, ref_sha, governed_marker
    ):
        ref_sha.side_effect = (SHA, SHA)
        governed_marker.side_effect = runner.BranchSyncError(
            "completion_marker_missing"
        )
        plan = runner.resolve(args(skip_ineligible=True))
        self.assertEqual(plan.action, "ineligible")
        self.assertEqual(plan.target_sha, SHA)
        self.assertTrue(runner.apply(plan, args(skip_ineligible=True)))

    @mock.patch.object(runner, "governed_marker")
    @mock.patch.object(runner, "ref_sha")
    def test_explicit_retry_fails_when_completion_authority_is_missing(
        self, ref_sha, governed_marker
    ):
        ref_sha.side_effect = (SHA, SHA)
        governed_marker.side_effect = runner.BranchSyncError(
            "completion_marker_missing"
        )
        with self.assertRaisesRegex(
            runner.BranchSyncError, "completion_marker_missing"
        ):
            runner.resolve(args())

    @mock.patch.object(runner, "governed_marker")
    @mock.patch.object(runner, "ref_sha")
    def test_integration_target_task_is_skipped_only_for_automatic_wake(
        self, ref_sha, governed_marker
    ):
        ref_sha.side_effect = (SHA, SHA, SHA, SHA)
        governed_marker.return_value = (
            {"merge_commit_sha": SHA},
            {"base": {"ref": "develop"}},
        )
        self.assertEqual(
            runner.resolve(args(skip_ineligible=True)).action, "ineligible"
        )
        with self.assertRaisesRegex(
            runner.BranchSyncError, "production_task_pr_identity_invalid"
        ):
            runner.resolve(args())


if __name__ == "__main__":
    unittest.main()
