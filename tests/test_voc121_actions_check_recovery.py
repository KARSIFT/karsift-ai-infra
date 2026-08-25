from __future__ import annotations

import json
import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from actions_check_recovery import (  # noqa: E402
    evaluate,
    missing_contexts,
    plan_recovery_dispatches,
    recovery_complete,
    select_authoritative,
    suppress_active_or_successful_dispatches,
)
from promotion_status_attestation import AttestationError, attestable_contexts  # noqa: E402


def load_runner():
    path = ROOT / "config/actions-check-recovery-runner.py"
    spec = spec_from_file_location("voc121_actions_check_recovery_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()

HEAD_SHA = "a" * 40


def gate_summary_from_check_runs(check_runs: list[dict]) -> dict:
    selected = select_authoritative(check_runs, [], expected={"head_sha": HEAD_SHA})
    return evaluate(selected)


class Voc121ActionsCheckRecoveryTests(unittest.TestCase):
    def test_failed_check_exit_still_yields_valid_required_view_for_recovery(self):
        completed = mock.Mock(
            returncode=1,
            stdout=json.dumps(
                [{"name": "governance-policy", "state": "FAILURE"}]
            ),
            stderr="",
        )
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            self.assertEqual(
                runner.load_required_pr_checks("token", "KARSIFT/example", 993),
                [{"name": "governance-policy", "state": "FAILURE"}],
            )

    def test_empty_required_view_read_failure_stops_recovery(self):
        completed = mock.Mock(returncode=1, stdout="", stderr="provider detail")
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            with self.assertRaises(runner.RunnerError):
                runner.load_required_pr_checks("token", "KARSIFT/example", 993)

    def test_cancelled_pr_check_stays_missing_with_alternate_successful_run(self):
        gate_summary = gate_summary_from_check_runs(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": 1,
                    "name": "governance-policy",
                    "status": "completed",
                    "conclusion": "cancelled",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-24T00:00:00Z",
                },
                {
                    "head_sha": HEAD_SHA,
                    "id": 2,
                    "name": "governance-policy",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-24T00:01:00Z",
                },
            ]
        )
        pr_checks = [{"name": "governance-policy", "state": "FAILURE"}]
        self.assertEqual(
            missing_contexts(
                gate_summary,
                ("governance-policy",),
                pr_required_checks=pr_checks,
            ),
            ["governance-policy"],
        )

    def test_recovery_dispatches_when_pr_required_view_is_unsatisfied(self):
        gate_summary = gate_summary_from_check_runs(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": 2,
                    "name": "governance-policy",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-24T00:01:00Z",
                }
            ]
        )
        pr_checks = [{"name": "governance-policy", "state": "FAILURE"}]
        plans = plan_recovery_dispatches(
            mode="promotion_pr",
            target_sha=HEAD_SHA,
            branch_ref="develop",
            pr_number=993,
        )
        remaining = suppress_active_or_successful_dispatches(
            plans,
            [],
            head_sha=HEAD_SHA,
            gate_summary=gate_summary,
            pr_required_checks=pr_checks,
        )
        self.assertIn("governance-policy.yml", [plan.workflow_file for plan in remaining])

    def test_recovery_complete_follows_pr_required_view(self):
        gate_summary = gate_summary_from_check_runs(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": index,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": f"2026-08-24T00:00:0{index}Z",
                }
                for index, name in enumerate(
                    ("governance-policy", "validate", "ci / ci"), start=1
                )
            ]
        )
        pr_checks = [{"name": "governance-policy", "state": "FAILURE"}]
        self.assertFalse(
            recovery_complete(
                mode="promotion_pr",
                gate_summary=gate_summary,
                workflow_runs=[],
                head_sha=HEAD_SHA,
                pr_required_checks=pr_checks,
            )
        )

    def test_attestation_refuses_when_pr_required_view_is_unsatisfied(self):
        summary = {
            "checks": [
                {
                    "name": "governance-policy",
                    "state": "SUCCESS",
                    "kind": "check_run",
                    "workflow": ".github/workflows/governance-policy.yml",
                    "conclusion": "success",
                    "run_id": 1,
                },
                {
                    "name": "validate",
                    "state": "SUCCESS",
                    "kind": "check_run",
                    "workflow": ".github/workflows/repository-governance.yml",
                    "conclusion": "success",
                    "run_id": 2,
                },
                {
                    "name": "ci / ci",
                    "state": "SUCCESS",
                    "kind": "check_run",
                    "workflow": ".github/workflows/pipeline.yml",
                    "conclusion": "success",
                    "run_id": 3,
                },
            ]
        }
        pr_checks = [{"name": "governance-policy", "state": "FAILURE"}]
        with self.assertRaises(AttestationError):
            attestable_contexts(summary, pr_required_checks=pr_checks)


if __name__ == "__main__":
    unittest.main()
