from __future__ import annotations

import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from actions_check_recovery import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    evaluate,
    recovery_complete,
    select_authoritative,
)
from required_check_satisfaction import SatisfactionError  # noqa: E402


def load_runner():
    path = ROOT / "config/actions-check-recovery-runner.py"
    spec = spec_from_file_location("voc122_actions_check_recovery_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()

HEAD_SHA = "a" * 40
REPOSITORY = "KARSIFT/example"
BRANCH = "release/voc-122"
PR_NUMBER = 1000


def gate_summary_from_check_runs(check_runs: list[dict]) -> dict:
    selected = select_authoritative(check_runs, [], expected={"head_sha": HEAD_SHA})
    return evaluate(selected)


def success_gate_summary() -> dict:
    return gate_summary_from_check_runs(
        [
            {
                "head_sha": HEAD_SHA,
                "id": index,
                "name": name,
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
                "started_at": f"2026-08-25T23:54:0{index}Z",
            }
            for index, name in enumerate(
                ("governance-policy", "validate", "ci / ci"), start=1
            )
        ]
    )


def promotion_argv(*, timeout_seconds: str = "1") -> list[str]:
    return [
        "actions-check-recovery-runner.py",
        "--repository",
        REPOSITORY,
        "--mode",
        "promotion_pr",
        "--target-sha",
        HEAD_SHA,
        "--branch-ref",
        BRANCH,
        "--pr-number",
        str(PR_NUMBER),
        "--timeout-seconds",
        timeout_seconds,
        "--github-token",
        "token",
    ]


def selected_governance_run(*, run_id: int = 32912850066, run_attempt: int = 1) -> dict:
    return {
        "id": run_id,
        "run_attempt": run_attempt,
        "status": "completed",
        "conclusion": "cancelled",
        "event": "pull_request",
        "head_sha": HEAD_SHA,
        "head_branch": BRANCH,
        "name": "Governance policy",
        "path": ".github/workflows/governance-policy.yml",
        "pull_requests": [{"number": PR_NUMBER}],
    }


def metadata_result(
    pr_view: list[dict],
    *,
    gate_summary: dict | None = None,
) -> tuple[bool, dict, list[dict], list[dict]]:
    return (True, gate_summary or success_gate_summary(), [], pr_view)


class Voc122ActionsCheckRecoveryTests(unittest.TestCase):
    def test_absent_then_cancelled_selected_row_is_rerun_once_and_succeeds(self):
        absent_view = [
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        cancelled_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/32912850066",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        successful_view = [
            {"name": name, "state": "SUCCESS"}
            for name in ("governance-policy", "validate", "ci / ci")
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=[
                metadata_result(absent_view, gate_summary=gate_summary),
                metadata_result(cancelled_view, gate_summary=gate_summary),
                metadata_result(successful_view, gate_summary=gate_summary),
            ],
        ), mock.patch.object(
            runner, "load_selected_workflow_run", return_value=selected_governance_run()
        ), mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun, mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner.time, "sleep"
        ):
            self.assertEqual(runner.main(), 0)
        dispatch.assert_called_once()
        self.assertEqual(
            dispatch.call_args.args[2],
            "governance-policy.yml",
        )
        rerun.assert_called_once_with("token", REPOSITORY, 32912850066)

    def test_absent_context_dispatch_is_not_repeated_on_later_snapshots(self):
        absent_view = [
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=lambda **kwargs: metadata_result(
                absent_view, gate_summary=gate_summary
            ),
        ), mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun, mock.patch.object(
            runner.time, "sleep"
        ), mock.patch.object(
            runner.time, "time", side_effect=[0.0, 0.0, 0.5, 1.1]
        ):
            self.assertEqual(runner.main(), 1)
        dispatch.assert_called_once()
        rerun.assert_not_called()

    def test_selected_run_ids_are_not_rerun_again_on_later_snapshots(self):
        cancelled_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/32912850066",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=lambda **kwargs: metadata_result(
                cancelled_view, gate_summary=gate_summary
            ),
        ), mock.patch.object(
            runner, "load_selected_workflow_run", return_value=selected_governance_run()
        ), mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun, mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner.time, "sleep"
        ), mock.patch.object(
            runner.time, "time", side_effect=[0.0, 0.0, 0.5, 1.1]
        ):
            self.assertEqual(runner.main(), 1)
        rerun.assert_called_once_with("token", REPOSITORY, 32912850066)
        dispatch.assert_not_called()

    def test_replan_rerun_refuses_second_attempt_selected_run(self):
        pending_view = [
            {"name": "governance-policy", "state": "PENDING"},
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        cancelled_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/32912850066",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=[
                (True, gate_summary, [], pending_view),
                (True, gate_summary, [], cancelled_view),
            ],
        ), mock.patch.object(
            runner,
            "load_selected_workflow_run",
            return_value=selected_governance_run(run_attempt=2),
        ), mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun, mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch:
            self.assertEqual(runner.main(), 1)
        rerun.assert_not_called()
        dispatch.assert_not_called()

    def test_replan_rerun_still_binds_pr_head_branch_event_and_workflow(self):
        pending_view = [
            {"name": "governance-policy", "state": "PENDING"},
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        cancelled_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/32912850066",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        payload = selected_governance_run()
        for field, value in (
            ("head_sha", "b" * 40),
            ("head_branch", "other"),
            ("event", "workflow_dispatch"),
            ("path", ".github/workflows/foreign.yml"),
            ("pull_requests", [{"number": PR_NUMBER - 1}]),
            ("run_attempt", 2),
        ):
            with self.subTest(field=field), mock.patch.object(
                sys, "argv", promotion_argv()
            ), mock.patch.object(
                runner,
                "run_metadata_phase",
                side_effect=[
                    (True, gate_summary, [], pending_view),
                    (True, gate_summary, [], cancelled_view),
                ],
            ), mock.patch.object(
                runner,
                "load_selected_workflow_run",
                return_value={**payload, field: value},
            ), mock.patch.object(
                runner, "rerun_selected_workflow"
            ) as rerun, mock.patch.object(
                runner, "dispatch_workflow"
            ) as dispatch:
                self.assertEqual(runner.main(), 1)
            rerun.assert_not_called()
            dispatch.assert_not_called()

    def test_dispatch_success_does_not_suppress_later_cancelled_selected_row(self):
        absent_view = [
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        cancelled_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/32912850066",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        successful_view = [
            {"name": name, "state": "SUCCESS"}
            for name in ("governance-policy", "validate", "ci / ci")
        ]
        gate_summary = gate_summary_from_check_runs(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": 32912851134,
                    "name": "governance-policy",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-25T23:54:50Z",
                },
                {
                    "head_sha": HEAD_SHA,
                    "id": 2,
                    "name": "validate",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-25T23:54:51Z",
                },
                {
                    "head_sha": HEAD_SHA,
                    "id": 3,
                    "name": "ci / ci",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-25T23:54:52Z",
                },
            ]
        )
        self.assertFalse(
            recovery_complete(
                mode="promotion_pr",
                gate_summary=gate_summary,
                workflow_runs=[],
                head_sha=HEAD_SHA,
                pr_required_checks=cancelled_view,
            )
        )
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=[
                metadata_result(absent_view, gate_summary=gate_summary),
                metadata_result(cancelled_view, gate_summary=gate_summary),
                metadata_result(successful_view, gate_summary=gate_summary),
            ],
        ), mock.patch.object(
            runner, "load_selected_workflow_run", return_value=selected_governance_run()
        ), mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun, mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner.time, "sleep"
        ):
            self.assertEqual(runner.main(), 0)
        dispatch.assert_called_once()
        rerun.assert_called_once_with("token", REPOSITORY, 32912850066)

    def test_ambiguous_required_check_run_fails_closed_on_replan(self):
        absent_view = [
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        ambiguous_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/111",
            },
            {
                "name": "governance-policy",
                "state": "FAILURE",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/222",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=[
                (True, gate_summary, [], absent_view),
                (True, gate_summary, [], ambiguous_view),
            ],
        ), mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun:
            self.assertEqual(runner.main(), 1)
        dispatch.assert_called_once()
        rerun.assert_not_called()

    def test_foreign_required_check_fails_closed_on_replan(self):
        absent_view = [
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        foreign_view = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "workflow_dispatch",
                "workflow": "Governance policy",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/32912850066",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=[
                (True, gate_summary, [], absent_view),
                (True, gate_summary, [], foreign_view),
            ],
        ), mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun:
            self.assertEqual(runner.main(), 1)
        dispatch.assert_called_once()
        rerun.assert_not_called()

    def test_failed_ci_ci_dispatches_recovery_instead_of_rerunning_doomed_pr_job(self):
        failed_ci_view = [
            {
                "name": "ci / ci",
                "state": "FAILURE",
                "event": "pull_request",
                "workflow": "pipeline",
                "link": f"https://github.com/{REPOSITORY}/actions/runs/33122154521",
            },
            {"name": "governance-policy", "state": "SUCCESS"},
            {"name": "validate", "state": "SUCCESS"},
        ]
        gate_summary = success_gate_summary()
        with mock.patch.object(sys, "argv", promotion_argv()), mock.patch.object(
            runner,
            "run_metadata_phase",
            side_effect=lambda **kwargs: metadata_result(
                failed_ci_view, gate_summary=gate_summary
            ),
        ), mock.patch.object(
            runner, "rerun_selected_workflow"
        ) as rerun, mock.patch.object(
            runner, "dispatch_workflow"
        ) as dispatch, mock.patch.object(
            runner.time, "sleep"
        ), mock.patch.object(
            runner.time, "time", side_effect=[0.0, 0.0, 0.5, 1.1]
        ):
            self.assertEqual(runner.main(), 1)
        rerun.assert_not_called()
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[2], "pipeline.yml")

    def test_timeout_poll_interval_and_integration_push_planning_remain(self):
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 1800)
        self.assertEqual(POLL_INTERVAL_SECONDS, 30)
        source = (ROOT / "config/actions-check-recovery-runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("apply_promotion_pr_recovery_plan", source)
        self.assertIn("apply_integration_push_recovery_plan", source)
        self.assertIn("if mode == \"promotion_pr\":", source)
        self.assertNotIn("apply_integration_push_recovery_plan(", source.split("while time.time()")[1])


if __name__ == "__main__":
    unittest.main()
