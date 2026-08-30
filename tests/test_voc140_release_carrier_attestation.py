"""VOC-140 release-carrier and dedicated promotion-pr-validation attestability."""

from __future__ import annotations

import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from actions_check_recovery import (  # noqa: E402
    recovery_complete,
    suppress_active_or_successful_dispatches,
    plan_recovery_dispatches,
)
from promotion_ci_attestation import (  # noqa: E402
    dedicated_promotion_validation_title,
    is_dedicated_promotion_validation_run,
    is_release_carrier_run,
    parent_run_is_attestable,
)
from promotion_status_attestation import (  # noqa: E402
    AttestationError,
    verify_promotion_required_run_semantics,
)


HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
REPOSITORY = "KARSIFT/example"
PR_NUMBER = 1090
RUN_ID = 33136633666


def load_runner(filename: str, module_name: str):
    path = ROOT / "config" / filename
    spec = spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recovery_runner = load_runner(
    "actions-check-recovery-runner.py", "voc140_actions_check_recovery_runner"
)
authoritative_runner = load_runner(
    "authoritative-checks-runner.py", "voc140_authoritative_checks_runner"
)


def gate_summary_with_ci(*, run_id: int = RUN_ID) -> dict:
    return {
        "checks": [
            {
                "name": "governance-policy",
                "state": "SUCCESS",
                "kind": "check_run",
                "workflow": "github-actions",
                "conclusion": "success",
                "run_id": 1,
            },
            {
                "name": "validate",
                "state": "SUCCESS",
                "kind": "check_run",
                "workflow": "github-actions",
                "conclusion": "success",
                "run_id": 2,
            },
            {
                "name": "ci / ci",
                "state": "SUCCESS",
                "kind": "check_run",
                "workflow": ".github/workflows/pipeline.yml",
                "conclusion": "success",
                "run_id": run_id,
            },
        ],
        "pending": 0,
        "failed": 0,
        "successful": 3,
    }


def gate_summary_without_ci() -> dict:
    summary = gate_summary_with_ci()
    summary["checks"] = [
        item for item in summary["checks"] if item["name"] != "ci / ci"
    ]
    summary["successful"] = 2
    return summary


def required_checks_success() -> list[dict[str, str]]:
    return [
        {"name": "governance-policy", "state": "SUCCESS"},
        {"name": "validate", "state": "SUCCESS"},
        {"name": "ci / ci", "state": "SUCCESS"},
    ]


def release_carrier_run(*, status: str = "in_progress", conclusion: str | None = None) -> dict:
    return {
        "id": RUN_ID,
        "path": ".github/workflows/pipeline.yml",
        "event": "workflow_dispatch",
        "display_title": "pipeline",
        "status": status,
        "conclusion": conclusion,
        "head_sha": HEAD_SHA,
    }


def dedicated_recovery_run(*, status: str = "completed", conclusion: str = "success") -> dict:
    return {
        "id": 33136865709,
        "path": ".github/workflows/pipeline.yml",
        "event": "workflow_dispatch",
        "display_title": dedicated_promotion_validation_title(PR_NUMBER),
        "status": status,
        "conclusion": conclusion,
        "head_sha": HEAD_SHA,
    }


class Voc140ReleaseCarrierAttestationTests(unittest.TestCase):
    def test_in_progress_release_carrier_is_not_attestable(self):
        run = release_carrier_run()
        jobs = [{"name": "release / converge", "conclusion": None}]
        self.assertTrue(is_release_carrier_run(run, jobs))
        self.assertFalse(parent_run_is_attestable(run, jobs, pr_number=PR_NUMBER))

    def test_dedicated_promotion_validation_completed_is_attestable(self):
        run = dedicated_recovery_run()
        self.assertTrue(is_dedicated_promotion_validation_run(run, pr_number=PR_NUMBER))
        self.assertFalse(is_release_carrier_run(run))
        self.assertTrue(parent_run_is_attestable(run, pr_number=PR_NUMBER))

    def test_dedicated_title_with_executed_release_job_is_a_carrier(self):
        run = dedicated_recovery_run()
        jobs = [{"name": "release / converge", "conclusion": "success"}]
        self.assertTrue(is_release_carrier_run(run, jobs))
        self.assertFalse(parent_run_is_attestable(run, jobs, pr_number=PR_NUMBER))

    def test_dedicated_title_with_skipped_release_job_remains_attestable(self):
        run = dedicated_recovery_run()
        jobs = [{"name": "release / converge", "conclusion": "skipped"}]
        self.assertFalse(is_release_carrier_run(run, jobs))
        self.assertTrue(parent_run_is_attestable(run, jobs, pr_number=PR_NUMBER))

    def test_recovery_not_complete_when_ci_parent_is_in_progress_carrier(self):
        summary = gate_summary_with_ci()
        workflow_runs = [release_carrier_run()]
        self.assertFalse(
            recovery_complete(
                mode="promotion_pr",
                gate_summary=summary,
                workflow_runs=workflow_runs,
                head_sha=HEAD_SHA,
                pr_required_checks=required_checks_success(),
                pr_number=PR_NUMBER,
            )
        )

    def test_recovery_not_complete_when_attestable_summary_filtered_out_ci(self):
        self.assertFalse(
            recovery_complete(
                mode="promotion_pr",
                gate_summary=gate_summary_without_ci(),
                workflow_runs=[release_carrier_run()],
                head_sha=HEAD_SHA,
                pr_required_checks=required_checks_success(),
                pr_number=PR_NUMBER,
            )
        )

    def test_non_successful_parent_states_never_complete_recovery(self):
        for status, conclusion in (
            ("in_progress", None),
            ("queued", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                self.assertFalse(
                    recovery_complete(
                        mode="promotion_pr",
                        gate_summary=gate_summary_with_ci(),
                        workflow_runs=[
                            release_carrier_run(
                                status=status, conclusion=conclusion
                            )
                        ],
                        head_sha=HEAD_SHA,
                        pr_required_checks=required_checks_success(),
                        pr_number=PR_NUMBER,
                    )
                )

    def test_recovery_complete_with_dedicated_completed_recovery(self):
        summary = gate_summary_with_ci(run_id=33136865709)
        workflow_runs = [dedicated_recovery_run()]
        self.assertTrue(
            recovery_complete(
                mode="promotion_pr",
                gate_summary=summary,
                workflow_runs=workflow_runs,
                head_sha=HEAD_SHA,
                pr_required_checks=required_checks_success(),
                pr_number=PR_NUMBER,
            )
        )

    def test_recovery_runner_fetches_jobs_and_rejects_executed_release_job(self):
        check_run = {
            "app": {"slug": "github-actions"},
            "details_url": (
                f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}/job/99"
            ),
        }
        run = dedicated_recovery_run()
        run["id"] = RUN_ID
        with mock.patch.object(
            recovery_runner, "gh_api", return_value=run
        ), mock.patch.object(
            recovery_runner,
            "load_workflow_jobs",
            return_value=[{"name": "release / converge", "conclusion": "success"}],
        ) as jobs_mock:
            selected = recovery_runner._attestable_check_runs(
                [check_run], "token", REPOSITORY, pr_number=PR_NUMBER
            )
        self.assertEqual(selected, [])
        jobs_mock.assert_called_once_with("token", REPOSITORY, RUN_ID)

    def test_recovery_runner_accepts_skipped_release_definition(self):
        check_run = {
            "app": {"slug": "github-actions"},
            "details_url": (
                f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}/job/99"
            ),
        }
        run = dedicated_recovery_run()
        run["id"] = RUN_ID
        with mock.patch.object(
            recovery_runner, "gh_api", return_value=run
        ), mock.patch.object(
            recovery_runner,
            "load_workflow_jobs",
            return_value=[{"name": "release / converge", "conclusion": "skipped"}],
        ):
            selected = recovery_runner._attestable_check_runs(
                [check_run], "token", REPOSITORY, pr_number=PR_NUMBER
            )
        self.assertEqual(selected[0]["run_id"], RUN_ID)

    def test_recovery_runner_filters_every_non_successful_parent_state(self):
        check_run = {
            "app": {"slug": "github-actions"},
            "details_url": (
                f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}/job/99"
            ),
        }
        for status, conclusion in (
            ("in_progress", None),
            ("queued", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
        ):
            run = dedicated_recovery_run(status=status, conclusion=conclusion)
            run["id"] = RUN_ID
            with self.subTest(status=status, conclusion=conclusion), mock.patch.object(
                recovery_runner, "gh_api", return_value=run
            ), mock.patch.object(
                recovery_runner, "load_workflow_jobs", return_value=[]
            ):
                self.assertEqual(
                    recovery_runner._attestable_check_runs(
                        [check_run], "token", REPOSITORY, pr_number=PR_NUMBER
                    ),
                    [],
                )

    def test_authoritative_runner_rejects_dedicated_title_with_release_job(self):
        check_run = {
            "app": {"slug": "github-actions"},
            "details_url": (
                f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}/job/99"
            ),
            "head_sha": HEAD_SHA,
        }
        run = dedicated_recovery_run()
        run["id"] = RUN_ID
        run["repository"] = {"full_name": REPOSITORY}

        def completed(command, **_kwargs):
            result = mock.Mock(returncode=0, stderr="")
            endpoint = command[-1]
            if endpoint.endswith(f"/actions/runs/{RUN_ID}"):
                result.stdout = __import__("json").dumps(run)
            elif f"/actions/runs/{RUN_ID}/jobs" in endpoint:
                result.stdout = __import__("json").dumps(
                    [{"jobs": [{"name": "release / converge", "conclusion": "success"}]}]
                )
            else:
                raise AssertionError(f"unexpected command: {command}")
            return result

        with mock.patch.object(
            authoritative_runner.subprocess, "run", side_effect=completed
        ):
            selected = authoritative_runner._workflow_runs(
                [check_run], REPOSITORY, {"workflow_dispatch"}, pr_number=PR_NUMBER
            )
        self.assertEqual(selected, [])

    def test_in_progress_release_carrier_does_not_suppress_recovery_dispatch(self):
        plans = plan_recovery_dispatches(
            mode="promotion_pr",
            target_sha=HEAD_SHA,
            branch_ref="develop",
            pr_number=PR_NUMBER,
        )
        remaining = suppress_active_or_successful_dispatches(
            plans,
            [release_carrier_run()],
            head_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
        )
        self.assertEqual(
            [plan.workflow_file for plan in remaining],
            ["governance-policy.yml", "repository-governance.yml", "pipeline.yml"],
        )

    def test_dedicated_recovery_in_progress_suppresses_pipeline_dispatch(self):
        plans = plan_recovery_dispatches(
            mode="promotion_pr",
            target_sha=HEAD_SHA,
            branch_ref="develop",
            pr_number=PR_NUMBER,
        )
        remaining = suppress_active_or_successful_dispatches(
            plans,
            [dedicated_recovery_run(status="in_progress", conclusion=None)],
            head_sha=HEAD_SHA,
            pr_number=PR_NUMBER,
        )
        self.assertEqual(
            [plan.workflow_file for plan in remaining],
            ["governance-policy.yml", "repository-governance.yml"],
        )

    def test_release_carrier_run_rejected_at_attestation(self):
        payload = {
            "id": RUN_ID,
            "event": "pull_request",
            "path": ".github/workflows/pipeline.yml",
            "display_title": "Release: promote develop to main",
            "status": "completed",
            "conclusion": "success",
            "head_sha": HEAD_SHA,
            "head_branch": "develop",
            "repository": {"full_name": REPOSITORY},
            "pull_requests": [
                {
                    "number": PR_NUMBER,
                    "base": {
                        "sha": BASE_SHA,
                        "ref": "main",
                        "repo": {"full_name": REPOSITORY},
                    },
                    "head": {
                        "sha": HEAD_SHA,
                        "ref": "develop",
                        "repo": {"full_name": REPOSITORY},
                    },
                }
            ],
        }
        jobs = [{"name": "release / converge", "conclusion": "success"}]
        self.assertTrue(is_release_carrier_run(payload, jobs))
        with self.assertRaisesRegex(AttestationError, "untrusted_ci_recovery_identity"):
            verify_promotion_required_run_semantics(
                payload,
                context="ci / ci",
                run_id=RUN_ID,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                base_sha=BASE_SHA,
                head_sha=HEAD_SHA,
                base_ref="main",
                head_ref="develop",
                jobs=jobs,
            )


if __name__ == "__main__":
    unittest.main()
