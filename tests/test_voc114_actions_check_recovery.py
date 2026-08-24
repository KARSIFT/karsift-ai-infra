from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
sys.path.insert(0, str(CONFIG))


def load_runner():
    path = CONFIG / "actions-check-recovery-runner.py"
    spec = spec_from_file_location("actions_check_recovery_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()

HEAD_SHA = "a" * 40
REPOSITORY = "KARSIFT/example"
TOKEN = "test-token"


def mint_permissions(workflow: str, step_name: str) -> dict[str, str]:
    marker = f"- name: {step_name}"
    self_contained = workflow.split(marker, 1)[1].split("\n      - name:", 1)[0]
    pairs = re.findall(
        r"^\s+(permission-[a-z-]+):\s+(read|write)\s*$",
        self_contained,
        re.MULTILINE,
    )
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise AssertionError(f"duplicate permission input in {step_name}")
    return dict(pairs)


def require_permissions(actual: dict[str, str], expected: dict[str, str]) -> None:
    missing = {
        key: value
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if missing:
        raise AssertionError(f"missing or incorrect recovery permissions: {missing}")


def completed_process(returncode: int = 0, stdout: str = "") -> mock.Mock:
    result = mock.Mock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


def gate_status_payload() -> str:
    return json.dumps([{"statuses": [], "total_count": 0}])


def commit_files_payload() -> str:
    return json.dumps([{"files": [{"filename": "docs/example.md"}]}])


def workflow_runs_payload() -> str:
    return json.dumps([{"workflow_runs": [], "total_count": 0}])


def check_runs_payload() -> str:
    return json.dumps([{"check_runs": [], "total_count": 0}])


def promotion_pull_payload() -> str:
    return json.dumps(
        {
            "number": 947,
            "state": "open",
            "head": {"sha": HEAD_SHA, "ref": "develop"},
        }
    )


class Voc114RecoveryMetadataTests(unittest.TestCase):
    def test_gh_api_uses_environment_context_without_invalid_repo_flag(self):
        with mock.patch(
            "subprocess.run",
            return_value=completed_process(stdout="{}"),
        ) as run_mock:
            self.assertEqual(
                runner.gh_api(
                    TOKEN,
                    REPOSITORY,
                    f"repos/{REPOSITORY}/pulls/947",
                    read_failure=runner.COMMIT_METADATA_READ_FAILED,
                ),
                {},
            )
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:2], ["gh", "api"])
        self.assertNotIn("--repo", command)
        self.assertEqual(run_mock.call_args.kwargs["env"]["GH_REPO"], REPOSITORY)

    def test_runner_declares_endpoint_classes(self):
        source = (CONFIG / "actions-check-recovery-runner.py").read_text(encoding="utf-8")
        self.assertIn('CHECK_RUNS_READ_FAILED = "check_runs_read_failed"', source)
        self.assertIn('WORKFLOW_RUNS_READ_FAILED = "workflow_runs_read_failed"', source)
        self.assertIn('COMMIT_METADATA_READ_FAILED = "commit_metadata_read_failed"', source)
        self.assertNotIn('raise RunnerError("github_metadata_read_failed")', source)

    def test_recovery_mint_paths_declare_read_contract(self):
        merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        reusable = (ROOT / ".github/workflows/recover-actions-checks.yml").read_text(
            encoding="utf-8"
        )
        mutation_contract = {
            "permission-actions": "write",
            "permission-checks": "read",
            "permission-statuses": "read",
            "permission-contents": "write",
            "permission-pull-requests": "write",
        }
        reusable_contract = {
            "permission-actions": "write",
            "permission-checks": "read",
            "permission-statuses": "read",
            "permission-contents": "read",
            "permission-pull-requests": "read",
        }
        require_permissions(
            mint_permissions(merge_gate, "Mint App installation token"),
            mutation_contract,
        )
        require_permissions(
            mint_permissions(
                release,
                "Mint App installation token for release mutation",
            ),
            mutation_contract,
        )
        require_permissions(
            mint_permissions(
                reusable,
                "Mint App installation token for recovery dispatch",
            ),
            reusable_contract,
        )

    def test_missing_read_permissions_fail_the_mint_contract_model(self):
        complete = {
            "permission-actions": "write",
            "permission-checks": "read",
            "permission-statuses": "read",
            "permission-contents": "read",
            "permission-pull-requests": "read",
        }
        for missing_key in (
            "permission-actions",
            "permission-checks",
            "permission-statuses",
            "permission-contents",
            "permission-pull-requests",
        ):
            incomplete = dict(complete)
            del incomplete[missing_key]
            with self.subTest(missing_key=missing_key):
                with self.assertRaises(AssertionError):
                    require_permissions(incomplete, complete)

    def test_duplicate_permission_inputs_fail_the_mint_contract_model(self):
        malformed = """- name: Recovery
        with:
          permission-actions: write
          permission-actions: read
"""
        with self.assertRaisesRegex(AssertionError, "duplicate permission"):
            mint_permissions(malformed, "Recovery")

    def test_check_runs_read_failure_is_localized(self):
        with mock.patch(
            "subprocess.run",
            return_value=completed_process(returncode=1),
        ):
            with self.assertRaises(runner.RunnerError) as ctx:
                runner.load_gate_summary(TOKEN, REPOSITORY, HEAD_SHA)
        self.assertEqual(str(ctx.exception), runner.CHECK_RUNS_READ_FAILED)

    def test_workflow_runs_read_failure_is_localized(self):
        with mock.patch(
            "subprocess.run",
            return_value=completed_process(returncode=1),
        ):
            with self.assertRaises(runner.RunnerError) as ctx:
                runner.load_workflow_runs(TOKEN, REPOSITORY, HEAD_SHA)
        self.assertEqual(str(ctx.exception), runner.WORKFLOW_RUNS_READ_FAILED)

    def test_commit_metadata_read_failure_is_localized(self):
        with mock.patch(
            "subprocess.run",
            return_value=completed_process(returncode=1),
        ):
            with self.assertRaises(runner.RunnerError) as ctx:
                runner.load_changed_paths(TOKEN, REPOSITORY, HEAD_SHA)
        self.assertEqual(str(ctx.exception), runner.COMMIT_METADATA_READ_FAILED)

    @mock.patch.object(runner, "dispatch_workflow")
    @mock.patch("subprocess.run")
    def test_integration_push_metadata_read_succeeds_before_dispatch(
        self, run_mock, dispatch_mock
    ):
        def run_side_effect(command, **kwargs):
            joined = " ".join(command)
            if "/check-runs" in joined or "/status" in joined:
                payload = gate_status_payload() if "/status" in joined else check_runs_payload()
                return completed_process(stdout=payload)
            if "/actions/runs" in joined:
                return completed_process(stdout=workflow_runs_payload())
            if f"/commits/{HEAD_SHA}?" in joined or (
                f"/commits/{HEAD_SHA}" in joined
                and "/check-runs" not in joined
                and "/status" not in joined
            ):
                return completed_process(stdout=commit_files_payload())
            if "/actions/workflows/" in joined and kwargs.get("input"):
                return completed_process()
            raise AssertionError(f"unexpected command: {joined}")

        run_mock.side_effect = run_side_effect
        argv = [
            "actions-check-recovery-runner.py",
            "--repository",
            REPOSITORY,
            "--mode",
            "integration_push",
            "--target-sha",
            HEAD_SHA,
            "--branch-ref",
            "develop",
            "--timeout-seconds",
            "1",
            "--github-token",
            TOKEN,
        ]
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            runner.time, "sleep", return_value=None
        ), mock.patch.object(sys, "stderr", stderr):
            result = runner.main()
        self.assertEqual(result, 1)
        dispatch_mock.assert_called()
        self.assertNotIn("github_metadata_read_failed", stderr.getvalue())

    @mock.patch.object(runner, "dispatch_workflow")
    @mock.patch("subprocess.run")
    def test_promotion_pr_metadata_read_succeeds_before_dispatch(
        self, run_mock, dispatch_mock
    ):
        def run_side_effect(command, **kwargs):
            joined = " ".join(command)
            if "/pulls/947" in joined:
                return completed_process(stdout=promotion_pull_payload())
            if "/check-runs" in joined or "/status" in joined:
                payload = gate_status_payload() if "/status" in joined else check_runs_payload()
                return completed_process(stdout=payload)
            if "/actions/runs" in joined:
                return completed_process(stdout=workflow_runs_payload())
            if "/actions/workflows/" in joined and kwargs.get("input"):
                return completed_process()
            raise AssertionError(f"unexpected command: {joined}")

        run_mock.side_effect = run_side_effect
        argv = [
            "actions-check-recovery-runner.py",
            "--repository",
            REPOSITORY,
            "--mode",
            "promotion_pr",
            "--target-sha",
            HEAD_SHA,
            "--branch-ref",
            "develop",
            "--pr-number",
            "947",
            "--timeout-seconds",
            "1",
            "--github-token",
            TOKEN,
        ]
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            runner.time, "sleep", return_value=None
        ), mock.patch.object(sys, "stderr", stderr):
            result = runner.main()
        self.assertEqual(result, 1)
        dispatch_mock.assert_called()
        self.assertNotIn("github_metadata_read_failed", stderr.getvalue())

    @mock.patch.object(runner, "dispatch_workflow")
    @mock.patch("subprocess.run")
    def test_no_dispatch_after_check_runs_read_failure(
        self, run_mock, dispatch_mock
    ):
        def run_side_effect(command, **kwargs):
            joined = " ".join(command)
            if f"/commits/{HEAD_SHA}?" in joined or (
                f"/commits/{HEAD_SHA}" in joined
                and "/check-runs" not in joined
                and "/status" not in joined
            ):
                return completed_process(stdout=commit_files_payload())
            return completed_process(returncode=1)

        run_mock.side_effect = run_side_effect
        argv = [
            "actions-check-recovery-runner.py",
            "--repository",
            REPOSITORY,
            "--mode",
            "integration_push",
            "--target-sha",
            HEAD_SHA,
            "--branch-ref",
            "develop",
            "--timeout-seconds",
            "1",
            "--github-token",
            TOKEN,
        ]
        stderr = io.StringIO()
        with mock.patch.object(runner, "plan_recovery_dispatches") as plan_mock, mock.patch.object(
            sys, "argv", argv
        ), mock.patch.object(sys, "stderr", stderr):
            result = runner.main()
        self.assertEqual(result, 1)
        plan_mock.assert_not_called()
        dispatch_mock.assert_not_called()
        self.assertIn(runner.CHECK_RUNS_READ_FAILED, stderr.getvalue())

    @mock.patch.object(runner, "dispatch_workflow")
    @mock.patch("subprocess.run")
    def test_no_dispatch_after_workflow_runs_read_failure(
        self, run_mock, dispatch_mock
    ):
        def run_side_effect(command, **kwargs):
            joined = " ".join(command)
            if "/check-runs" in joined or "/status" in joined:
                payload = gate_status_payload() if "/status" in joined else check_runs_payload()
                return completed_process(stdout=payload)
            if f"/commits/{HEAD_SHA}?" in joined or (
                f"/commits/{HEAD_SHA}" in joined
                and "/check-runs" not in joined
                and "/status" not in joined
            ):
                return completed_process(stdout=commit_files_payload())
            if "/actions/runs" in joined:
                return completed_process(returncode=1)
            raise AssertionError(f"unexpected command: {joined}")

        run_mock.side_effect = run_side_effect
        argv = [
            "actions-check-recovery-runner.py",
            "--repository",
            REPOSITORY,
            "--mode",
            "integration_push",
            "--target-sha",
            HEAD_SHA,
            "--branch-ref",
            "develop",
            "--timeout-seconds",
            "1",
            "--github-token",
            TOKEN,
        ]
        stderr = io.StringIO()
        with mock.patch.object(runner, "plan_recovery_dispatches") as plan_mock, mock.patch.object(
            sys, "argv", argv
        ), mock.patch.object(sys, "stderr", stderr):
            result = runner.main()
        self.assertEqual(result, 1)
        plan_mock.assert_not_called()
        dispatch_mock.assert_not_called()
        self.assertIn(runner.WORKFLOW_RUNS_READ_FAILED, stderr.getvalue())

    @mock.patch.object(runner, "dispatch_workflow")
    @mock.patch("subprocess.run")
    def test_no_dispatch_after_commit_metadata_read_failure(
        self, run_mock, dispatch_mock
    ):
        def run_side_effect(command, **kwargs):
            joined = " ".join(command)
            if f"/commits/{HEAD_SHA}?" in joined or (
                f"/commits/{HEAD_SHA}" in joined
                and "/check-runs" not in joined
                and "/status" not in joined
            ):
                return completed_process(returncode=1)
            raise AssertionError(f"unexpected command: {joined}")

        run_mock.side_effect = run_side_effect
        argv = [
            "actions-check-recovery-runner.py",
            "--repository",
            REPOSITORY,
            "--mode",
            "integration_push",
            "--target-sha",
            HEAD_SHA,
            "--branch-ref",
            "develop",
            "--timeout-seconds",
            "1",
            "--github-token",
            TOKEN,
        ]
        stderr = io.StringIO()
        with mock.patch.object(runner, "plan_recovery_dispatches") as plan_mock, mock.patch.object(
            sys, "argv", argv
        ), mock.patch.object(sys, "stderr", stderr):
            result = runner.main()
        self.assertEqual(result, 1)
        plan_mock.assert_not_called()
        dispatch_mock.assert_not_called()
        self.assertIn(runner.COMMIT_METADATA_READ_FAILED, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
