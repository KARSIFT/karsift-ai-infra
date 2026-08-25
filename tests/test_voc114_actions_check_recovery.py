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


def pr_required_checks_payload() -> str:
    return json.dumps(
        [
            {"name": "governance-policy", "state": "FAILURE"},
            {"name": "validate", "state": "FAILURE"},
            {"name": "ci / ci", "state": "FAILURE"},
        ]
    )


def promotion_pull_payload() -> str:
    return json.dumps(
        {
            "number": 947,
            "state": "open",
            "head": {"sha": HEAD_SHA, "ref": "develop"},
        }
    )


class Voc114RecoveryMetadataTests(unittest.TestCase):
    def test_promotion_dispatch_suppression_binds_required_context_not_workflow_path(self):
        plans = runner.plan_recovery_dispatches(
            mode="promotion_pr",
            target_sha=HEAD_SHA,
            branch_ref="develop",
            pr_number=947,
        )
        gate_summary = {
            "checks": [
                {
                    "name": "governance-policy",
                    "state": "SUCCESS",
                    "kind": "check_run",
                    "workflow": "github-actions",
                },
                {
                    "name": "validate",
                    "state": "SUCCESS",
                    "kind": "check_run",
                    "workflow": "github-actions",
                },
                {
                    "name": "ci / ci",
                    "state": "FAILURE",
                    "kind": "check_run",
                    "workflow": "github-actions",
                },
            ]
        }
        current_reconcile_run = [
            {
                "id": 32713089936,
                "head_sha": HEAD_SHA,
                "path": ".github/workflows/pipeline.yml",
                "status": "in_progress",
                "conclusion": None,
            }
        ]
        remaining = runner.suppress_active_or_successful_dispatches(
            plans,
            current_reconcile_run,
            head_sha=HEAD_SHA,
            gate_summary=gate_summary,
        )
        self.assertEqual(
            [plan.workflow_file for plan in remaining],
            ["pipeline.yml"],
        )

    def test_unrelated_failed_or_pending_checks_do_not_block_required_contexts(self):
        required = [
            {
                "name": name,
                "state": "SUCCESS",
                "kind": "check_run",
                "workflow": "github-actions",
                "conclusion": "success",
            }
            for name in ("governance-policy", "validate", "ci / ci")
        ]
        gate_summary = {
            "checks": [
                *required,
                {
                    "name": "release / converge",
                    "state": "PENDING",
                    "kind": "check_run",
                    "workflow": "github-actions",
                    "conclusion": None,
                },
                {
                    "name": "historical optional check",
                    "state": "FAILURE",
                    "kind": "check_run",
                    "workflow": "github-actions",
                    "conclusion": "failure",
                },
            ],
            "pending": 1,
            "failed": 1,
            "successful": 3,
        }
        self.assertTrue(
            runner.recovery_complete(
                mode="promotion_pr",
                gate_summary=gate_summary,
                workflow_runs=[],
                head_sha=HEAD_SHA,
            )
        )

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

    def test_recovery_dispatch_uses_job_token_and_app_mints_stay_mutation_only(self):
        merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        reusable = (ROOT / ".github/workflows/recover-actions-checks.yml").read_text(
            encoding="utf-8"
        )
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text(encoding="utf-8")
        template_merge_gate = template.split("\n  merge-gate:\n", 1)[1].split(
            "\n  release:\n", 1
        )[0]
        template_release = template.split("\n  release:\n", 1)[1].split(
            "\n  auto-advance:\n", 1
        )[0]
        mutation_contract = {
            "permission-contents": "write",
            "permission-issues": "write",
            "permission-pull-requests": "write",
        }
        merge_mint = mint_permissions(merge_gate, "Mint App installation token")
        release_mint = mint_permissions(
            release,
            "Mint App installation token for release mutation",
        )
        self.assertEqual(merge_mint, mutation_contract)
        self.assertEqual(release_mint, mutation_contract)
        self.assertNotIn("Mint App installation token for recovery dispatch", reusable)

        merge_recovery = merge_gate.split(
            "Recover missing integration push workflows for merged SHA", 1
        )[1]
        release_recovery = release.split(
            "Recover missing exact-head promotion checks", 1
        )[1].split("Select newest authoritative promotion checks", 1)[0]
        reusable_recovery = reusable.split("Recover missing exact-SHA checks", 1)[1]
        for block in (merge_recovery, release_recovery, reusable_recovery):
            self.assertIn("GH_TOKEN: ${{ github.token }}", block)
            self.assertNotIn("steps.app-token.outputs.token", block)

        for workflow in (merge_gate, reusable):
            self.assertIn("actions: write", workflow)
            self.assertIn("checks: read", workflow)
            self.assertIn("statuses: read", workflow)
        for workflow in (release, template_release):
            self.assertIn("actions: write", workflow)
            self.assertIn("checks: read", workflow)
            self.assertIn("statuses: write", workflow)
        self.assertIn("statuses: read", template_merge_gate)
        self.assertNotIn("statuses: write", template_merge_gate)
        self.assertNotIn("statuses: read", template_release)

    def test_ruleset_statuses_never_replace_genuine_recovery_checks(self):
        summary = runner.select_gate_evidence(
            [{"check_runs": [], "total_count": 0}],
            [{
                "statuses": [
                    {
                        "id": 1,
                        "context": "ci / ci",
                        "state": "success",
                        "created_at": "2026-08-24T00:00:00Z",
                        "creator": {"login": "github-actions[bot]"},
                    }
                ],
                "total_count": 1,
            }],
            head_sha=HEAD_SHA,
        )
        self.assertEqual(summary["total_count"], 0)

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
            if joined.startswith("gh pr checks"):
                # An absent required row uses the legacy workflow-dispatch
                # bootstrap. VOC-121 separately covers exact failed-run reruns.
                return completed_process(stdout="[]")
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
