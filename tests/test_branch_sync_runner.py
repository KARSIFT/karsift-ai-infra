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
OLD = "b" * 40


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


class BranchSyncRunnerMutationTests(unittest.TestCase):
    def setUp(self):
        self.args = args(
            mode="promotion",
            pr_number=17,
            expected_head_sha=OLD,
            expected_base_sha="c" * 40,
        )
        self.plan = runner.BranchSyncPlan("update", OLD, SHA)

    @mock.patch.object(runner, "resolve")
    @mock.patch.object(runner, "_run")
    @mock.patch.object(runner, "git")
    @mock.patch.object(runner, "git_env", return_value={})
    def test_apply_uses_exact_lease_and_revalidates_before_and_after_push(
        self, git_env, git, run, resolve
    ):
        git.side_effect = lambda *values, **_kwargs: (
            SHA if values[:2] == ("rev-parse", "origin/main") else
            OLD if values[:2] == ("rev-parse", "origin/develop") else ""
        )
        run.return_value = mock.Mock(returncode=0)
        resolve.side_effect = (
            self.plan,
            runner.BranchSyncPlan("noop", SHA, SHA),
        )
        self.assertTrue(runner.apply(self.plan, self.args))
        pushes = [call for call in git.call_args_list if call.args[0] == "push"]
        self.assertEqual(len(pushes), 1)
        self.assertIn(
            "--force-with-lease=refs/heads/develop:" + OLD,
            pushes[0].args,
        )
        self.assertEqual(resolve.call_count, 2)
        git_env.assert_called_once_with()

    @mock.patch.object(runner, "resolve")
    @mock.patch.object(runner, "_run")
    @mock.patch.object(runner, "git")
    @mock.patch.object(runner, "git_env", return_value={})
    def test_changed_state_fails_before_push(
        self, _git_env, git, run, resolve
    ):
        git.side_effect = lambda *values, **_kwargs: (
            SHA if values[:2] == ("rev-parse", "origin/main") else
            OLD if values[:2] == ("rev-parse", "origin/develop") else ""
        )
        run.return_value = mock.Mock(returncode=1)
        resolve.return_value = runner.BranchSyncPlan("update", "d" * 40, SHA)
        with self.assertRaisesRegex(
            runner.BranchSyncError, "branch_state_changed_before_push"
        ):
            runner.apply(self.plan, self.args)
        self.assertFalse(any(call.args[0] == "push" for call in git.call_args_list))

    @mock.patch.object(runner, "_run")
    @mock.patch.object(runner, "git")
    @mock.patch.object(runner, "git_env", return_value={})
    def test_tree_comparison_error_is_not_treated_as_a_real_diff(
        self, _git_env, git, run
    ):
        git.side_effect = lambda *values, **_kwargs: (
            SHA if values[:2] == ("rev-parse", "origin/main") else
            OLD if values[:2] == ("rev-parse", "origin/develop") else ""
        )
        run.return_value = mock.Mock(returncode=128)
        with self.assertRaisesRegex(
            runner.BranchSyncError, "tree_equivalence_check_failed"
        ):
            runner.apply(self.plan, self.args)
        self.assertFalse(any(call.args[0] == "push" for call in git.call_args_list))

if __name__ == "__main__":
    unittest.main()
