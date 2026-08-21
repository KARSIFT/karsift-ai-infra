from pathlib import Path
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
sys.path.insert(0, str(CONFIG))

import ready_for_review_reuse as policy  # noqa: E402
import verify_ready_for_review_reuse as verifier  # noqa: E402

RUNNER_SPEC = spec_from_file_location(
    "ready_for_review_reuse_runner",
    CONFIG / "ready-for-review-reuse-runner.py",
)
if RUNNER_SPEC is None or RUNNER_SPEC.loader is None:
    raise AssertionError("cannot load ready_for_review reuse runner")
runner = module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


HEAD = "a" * 40
BASE = "b" * 40
AGENT_REF = "agent/voc-104-voc-104-t00"
PLAN_REF = "plan/voc-104-ready-for-review-reruns-unchanged-exact-sha-ci"
PACKAGE = "specs/changes/VOC-104-ready-for-review-reruns-unchanged-exact-sha-ci"


def pipeline_run(
    *,
    run_id=100,
    workflow_path=".github/workflows/pipeline.yml",
    head_sha=HEAD,
    head_branch=AGENT_REF,
    base_sha=BASE,
    ci="success",
    publisher="success",
    event="pull_request",
    status="completed",
    conclusion="success",
    duplicate_ci=False,
):
    publisher_name = (
        policy.PLAN_PUBLISHER_JOB
        if head_branch.startswith("plan/")
        else policy.AGENT_PUBLISHER_JOB
    )
    jobs = [
        {"name": policy.REQUIRED_CI_JOB, "conclusion": ci},
        {"name": publisher_name, "conclusion": publisher},
    ]
    if duplicate_ci:
        jobs.append({"name": policy.REQUIRED_CI_JOB, "conclusion": "success"})
    return policy.PipelineRunSummary(
        run_id=run_id,
        workflow_path=workflow_path,
        event=event,
        head_sha=head_sha,
        head_branch=head_branch,
        base_sha=base_sha,
        status=status,
        conclusion=conclusion,
        jobs=tuple(jobs),
    )


def agent_body(package=PACKAGE):
    return textwrap.dedent(
        f"""\
        Implements task `VOC-104-T00`
        Package path: `{package}`
        Closes #875
        """
    )


def plan_body(package=PACKAGE):
    return f"New package directory: `{package}`\n"


def review_comment(
    *,
    plan=False,
    verdict="PASS",
    login=None,
    comment_id=1,
    created_at="2026-08-21T00:00:00Z",
):
    identity = [f"package_path: `{PACKAGE}`"] if plan else [
        "task_id: `VOC-104-T00`",
        f"package_path: `{PACKAGE}`",
        "authority_issue: `875`",
    ]
    return {
        "id": comment_id,
        "created_at": created_at,
        "user": {
            "login": login or policy.TRUSTED_BOT_LOGIN,
            "type": "Bot" if login is None else "User",
        },
        "body": "\n".join(
            [
                f"**Independent verification - bound to commit `{HEAD}`**",
                *identity,
                f"base_sha: `{BASE}`",
                f"pipeline_run_id: `100`",
                "",
                f"VERDICT: {verdict}",
            ]
        ),
    }


def decide(**overrides):
    values = {
        "event_action": "ready_for_review",
        "expected_head_sha": HEAD,
        "expected_base_sha": BASE,
        "live_head_sha": HEAD,
        "live_base_sha": BASE,
        "is_draft": False,
        "head_ref": AGENT_REF,
        "pr_body": agent_body(),
        "comments": [review_comment()],
        "pipeline_runs": [pipeline_run()],
        "pr_checks": [
            {"name": "governance-policy", "state": "SUCCESS"},
            {"name": "ready-for-review-reuse / decide", "state": "PENDING"},
        ],
        "current_run_id": 200,
        "result_path_exists": False,
    }
    values.update(overrides)
    return policy.evaluate_reuse_eligibility(**values)


class ReuseDecisionTests(unittest.TestCase):
    def test_agent_and_plan_positive_paths(self):
        self.assertEqual(decide().outcome, "reuse-evidence")
        plan = decide(
            head_ref=PLAN_REF,
            pr_body=plan_body(),
            comments=[review_comment(plan=True, verdict="PASS WITH NON-BLOCKING FINDINGS")],
            pipeline_runs=[pipeline_run(head_branch=PLAN_REF)],
            pr_checks=[
                {"name": "governance-policy", "state": "SUCCESS"},
            ],
        )
        self.assertEqual(plan.outcome, "reuse-evidence")

    def test_non_ready_actions_take_full_path(self):
        for action in ("opened", "synchronize", "reopened"):
            with self.subTest(action=action):
                self.assertEqual(decide(event_action=action).outcome, "full-path")

    def test_drift_and_draft_take_full_path(self):
        cases = (
            {"live_head_sha": "c" * 40},
            {"live_base_sha": "d" * 40},
            {"is_draft": True},
        )
        for values in cases:
            with self.subTest(values=values):
                self.assertEqual(decide(**values).outcome, "full-path")

    def test_evaluation_uncertainty_has_distinct_fail_closed_outcome(self):
        self.assertEqual(
            decide(evaluation_error="github_metadata_read_failed").outcome,
            "fail-closed-to-full-path",
        )
        self.assertEqual(
            decide(is_draft=None).outcome,
            "fail-closed-to-full-path",
        )

    def test_prior_run_must_be_distinct_exact_head_branch_and_unambiguous(self):
        bad_runs = (
            pipeline_run(run_id=200),
            pipeline_run(run_id=201),
            pipeline_run(workflow_path=".github/workflows/not-pipeline.yml"),
            pipeline_run(head_sha="c" * 40),
            pipeline_run(base_sha="c" * 40),
            pipeline_run(head_branch="agent/another-task"),
            pipeline_run(duplicate_ci=True),
            pipeline_run(ci="failure"),
            pipeline_run(publisher="skipped"),
        )
        for run in bad_runs:
            with self.subTest(run=run):
                self.assertEqual(decide(pipeline_runs=[run]).outcome, "full-path")

    def test_only_trusted_exact_identity_comment_qualifies(self):
        self.assertEqual(decide(comments=[]).outcome, "full-path")
        self.assertEqual(
            decide(comments=[review_comment(login="human-user")]).outcome,
            "full-path",
        )
        self.assertEqual(
            decide(pr_body=agent_body("../../unsafe")).outcome,
            "full-path",
        )
        for verdict in (
            "WAITING FOR OPERATOR LIVE EVIDENCE",
            "FAIL",
            "PENDING",
            "MALFORMED",
        ):
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    decide(comments=[review_comment(verdict=verdict)]).outcome,
                    "full-path",
                )
        wrong_base = review_comment()
        wrong_base["body"] = wrong_base["body"].replace(
            f"base_sha: `{BASE}`",
            f"base_sha: `{'c' * 40}`",
        )
        self.assertEqual(decide(comments=[wrong_base]).outcome, "full-path")
        wrong_head = review_comment()
        wrong_head["body"] = wrong_head["body"].replace(HEAD, "c" * 40)
        self.assertEqual(decide(comments=[wrong_head]).outcome, "full-path")
        for original, replacement in (
            ("task_id: `VOC-104-T00`", "task_id: `VOC-104-T01`"),
            (f"package_path: `{PACKAGE}`", "package_path: `specs/changes/VOC-104-other`"),
            ("authority_issue: `875`", "authority_issue: `999`"),
        ):
            with self.subTest(binding=original):
                wrong_identity = review_comment()
                wrong_identity["body"] = wrong_identity["body"].replace(
                    original,
                    replacement,
                )
                self.assertEqual(
                    decide(comments=[wrong_identity]).outcome,
                    "full-path",
                )
        untrusted_bot = review_comment()
        untrusted_bot["user"] = {"login": "different-bot[bot]", "type": "Bot"}
        self.assertEqual(decide(comments=[untrusted_bot]).outcome, "full-path")
        wrong_run = review_comment()
        wrong_run["body"] = wrong_run["body"].replace(
            "pipeline_run_id: `100`",
            "pipeline_run_id: `99`",
        )
        self.assertEqual(decide(comments=[wrong_run]).outcome, "full-path")

    def test_trusted_verdict_selection_matches_merge_gate_timestamp_then_id(self):
        older_higher_id = review_comment(
            verdict="FAIL",
            comment_id=20,
            created_at="2026-08-21T00:00:00Z",
        )
        newer_lower_id = review_comment(
            verdict="PASS",
            comment_id=10,
            created_at="2026-08-21T00:01:00Z",
        )
        self.assertEqual(
            decide(comments=[newer_lower_id, older_higher_id]).outcome,
            "reuse-evidence",
        )

    def test_non_green_unrelated_pr_check_takes_full_path(self):
        self.assertEqual(decide().outcome, "reuse-evidence")
        self.assertEqual(
            decide(
                pr_checks=[
                    {"name": "ci", "state": "PENDING"},
                    {"name": "extract-package-path", "state": "PENDING"},
                    {"name": "review", "state": "PENDING"},
                    {"name": "governance-policy", "state": "SUCCESS"},
                ]
            ).outcome,
            "reuse-evidence",
        )
        self.assertEqual(
            decide(
                pr_checks=[
                    {"name": "ci", "state": "PENDING"},
                    {"name": "extract-package-path", "state": "PENDING"},
                    {"name": "review", "state": "PENDING"},
                    {"name": "governance-policy", "state": "FAILURE"},
                ]
            ).outcome,
            "full-path",
        )

    def test_only_canonical_package_paths_are_eligible(self):
        invalid = (
            "specs/changes/..",
            "specs/changes/VOC-104",
            "specs/changes/voc-104-lowercase-prefix",
            "specs/changes/VOC-104-Uppercase-Suffix",
        )
        for package in invalid:
            with self.subTest(package=package):
                self.assertEqual(
                    decide(pr_body=agent_body(package)).outcome,
                    "full-path",
                )

    def test_required_attestation_is_exact_and_unique(self):
        self.assertEqual(decide(result_path_exists=True).outcome, "full-path")
        attestation = {
            "id": 2,
            "user": {"login": policy.TRUSTED_BOT_LOGIN, "type": "Bot"},
            "body": textwrap.dedent(
                f"""\
                **Live-evidence reconcile — qualified**
                task_id: `VOC-104-T00`
                result_head_sha: `{HEAD}`
                base_sha: `{BASE}`
                """
            ),
        }
        self.assertEqual(
            decide(
                result_path_exists=True,
                comments=[review_comment(), attestation],
            ).outcome,
            "reuse-evidence",
        )
        self.assertEqual(
            decide(
                result_path_exists=True,
                comments=[review_comment(), attestation, dict(attestation, id=3)],
            ).outcome,
            "full-path",
        )
        wrong_task = dict(
            attestation,
            body=attestation["body"].replace("VOC-104-T00", "VOC-104-T01"),
        )
        self.assertEqual(
            decide(
                result_path_exists=True,
                comments=[review_comment(), wrong_task],
            ).outcome,
            "full-path",
        )


class MetadataAdapterFailureTests(unittest.TestCase):
    def run_main(self, api_type):
        argv = [
            "ready-for-review-reuse-runner.py",
            "--repository",
            "KARSIFT/example",
            "--pr-number",
            "9",
            "--expected-head-sha",
            HEAD,
            "--expected-base-sha",
            BASE,
            "--event-action",
            "ready_for_review",
            "--current-run-id",
            "200",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-only"}, clear=False),
            mock.patch.object(runner, "GitHubApi", api_type),
            mock.patch.object(runner, "write_output") as write_output,
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(runner.main(), 0)
        decision = write_output.call_args.args[0]
        self.assertEqual(decision.outcome, "fail-closed-to-full-path")
        return decision.reason

    def test_api_failure_emits_distinct_fail_closed_outcome(self):
        class FailingApi:
            def __init__(self, token, repository):
                pass

            def gh(self, args, accepted_codes=(0,)):
                raise runner.MetadataError("github_metadata_read_failed")

        self.assertEqual(
            self.run_main(FailingApi),
            "github_metadata_read_failed",
        )

    def test_malformed_metadata_emits_internal_fail_closed_outcome(self):
        class MalformedApi:
            def __init__(self, token, repository):
                pass

            def gh(self, args, accepted_codes=(0,)):
                return "not-json"

        self.assertEqual(
            self.run_main(MalformedApi),
            "evaluation_internal_error",
        )


class MergeGateReuseTests(unittest.TestCase):
    def setUp(self):
        self.pr_checks = [
            {"name": policy.REQUIRED_CI_JOB, "state": "SUCCESS"},
            {"name": policy.AGENT_PUBLISHER_JOB, "state": "SUCCESS"},
            {"name": "extract-package-path", "state": "SKIPPED"},
            {"name": "review", "state": "SKIPPED"},
            {"name": "governance-policy", "state": "SUCCESS"},
            {"name": "merge-gate / report-status", "state": "IN_PROGRESS"},
        ]
        self.prior_jobs = list(pipeline_run().jobs)

    def test_current_skips_do_not_supersede_validated_prior_success(self):
        self.assertTrue(
            policy.compute_checks_ok_with_reuse(
                pr_checks=self.pr_checks,
                head_ref=AGENT_REF,
                prior_jobs=self.prior_jobs,
                reuse_outcome="reuse-evidence",
            )
        )
        self.assertTrue(
            policy.publisher_check_ok(
                pr_checks=self.pr_checks,
                head_ref=AGENT_REF,
                prior_jobs=self.prior_jobs,
                reuse_outcome="reuse-evidence",
            )
        )

    def test_missing_prior_success_or_current_required_name_fails_closed(self):
        bad_prior = [
            {"name": policy.REQUIRED_CI_JOB, "conclusion": "success"},
        ]
        self.assertFalse(
            policy.compute_checks_ok_with_reuse(
                pr_checks=self.pr_checks,
                head_ref=AGENT_REF,
                prior_jobs=bad_prior,
                reuse_outcome="reuse-evidence",
            )
        )
        duplicate_prior = self.prior_jobs + [dict(self.prior_jobs[0])]
        self.assertFalse(
            policy.compute_checks_ok_with_reuse(
                pr_checks=self.pr_checks,
                head_ref=AGENT_REF,
                prior_jobs=duplicate_prior,
                reuse_outcome="reuse-evidence",
            )
        )
        without_ci = [
            item
            for item in self.pr_checks
            if item["name"] != policy.REQUIRED_CI_JOB
        ]
        self.assertFalse(
            policy.compute_checks_ok_with_reuse(
                pr_checks=without_ci,
                head_ref=AGENT_REF,
                prior_jobs=self.prior_jobs,
                reuse_outcome="reuse-evidence",
            )
        )
        skipped_current_ci = [dict(item) for item in self.pr_checks]
        skipped_current_ci[0] = {
            **skipped_current_ci[0],
            "state": "SKIPPED",
        }
        self.assertFalse(
            policy.compute_checks_ok_with_reuse(
                pr_checks=skipped_current_ci,
                head_ref=AGENT_REF,
                prior_jobs=self.prior_jobs,
                reuse_outcome="reuse-evidence",
            )
        )
        duplicate_current_ci = [dict(item) for item in self.pr_checks]
        duplicate_current_ci.append(dict(self.pr_checks[0]))
        self.assertFalse(
            policy.compute_checks_ok_with_reuse(
                pr_checks=duplicate_current_ci,
                head_ref=AGENT_REF,
                prior_jobs=self.prior_jobs,
                reuse_outcome="reuse-evidence",
            )
        )

    def test_merge_helper_accepts_github_jobs_object_not_a_bare_list(self):
        helper = CONFIG / "merge-gate-reuse-checks.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checks_file = root / "checks.json"
            jobs_file = root / "jobs.json"
            checks_file.write_text(json.dumps(self.pr_checks), encoding="utf-8")
            jobs_file.write_text(
                json.dumps({"total_count": 2, "jobs": self.prior_jobs}),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "checks",
                    "--pr-checks-file",
                    str(checks_file),
                    "--prior-jobs-file",
                    str(jobs_file),
                    "--head-ref",
                    AGENT_REF,
                    "--reuse-outcome",
                    "reuse-evidence",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "true")

    def test_failed_reuse_decision_does_not_poison_completed_full_path(self):
        self.assertTrue(
            policy.compute_checks_ok(
                [
                    {"name": policy.REQUIRED_CI_JOB, "state": "SUCCESS"},
                    {"name": policy.AGENT_PUBLISHER_JOB, "state": "SUCCESS"},
                    {
                        "name": "ready-for-review-reuse / decide (ready_for_review)",
                        "state": "FAILURE",
                    },
                ]
            )
        )


class ProofVerifierTests(unittest.TestCase):
    def source_pr(self, *, number=9, head_sha=HEAD, base_sha=BASE, branch=AGENT_REF):
        repository = {"full_name": "KARSIFT/example"}
        return {
            "number": number,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-21T10:00:00Z",
            "body": agent_body(),
            "head": {
                "sha": head_sha,
                "ref": branch,
                "repo": repository,
            },
            "base": {"sha": base_sha, "repo": repository},
        }

    def run_metadata(
        self,
        *,
        run_id=300,
        branch=AGENT_REF,
        path=None,
        prs=None,
        event="pull_request",
        base_sha=BASE,
    ):
        return {
            "id": run_id,
            "repository": {"full_name": "KARSIFT/example"},
            "name": "pipeline",
            "path": path or ".github/workflows/pipeline.yml",
            "event": event,
            "head_sha": HEAD,
            "head_branch": branch,
            "status": "completed",
            "conclusion": "success",
            "pull_requests": [] if prs is None else prs,
        }

    def test_empty_pull_request_array_is_bound_by_exact_head_and_branch(self):
        ready = verifier.verify_ready_run(
            run=self.run_metadata(),
            repository="KARSIFT/example",
            pr_number=9,
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            expected_head_ref=AGENT_REF,
            source_pr=self.source_pr(),
        )
        self.assertTrue(ready.ok)
        prior = verifier.verify_prior_run(
            run=self.run_metadata(run_id=100),
            repository="KARSIFT/example",
            pr_number=9,
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            expected_head_ref=AGENT_REF,
            prior_run_id=100,
            ready_run_id=300,
            source_pr=self.source_pr(),
        )
        self.assertTrue(prior.ok)
        proof_head = "c" * 40
        self.assertTrue(
            verifier.verify_current_ref(
                current_ref=proof_head,
                expected_head_sha=proof_head,
            ).ok
        )
        self.assertNotEqual(proof_head, HEAD)

    def test_wrong_branch_path_pr_or_same_run_is_rejected(self):
        self.assertFalse(
            verifier.verify_ready_run(
                run=self.run_metadata(branch="agent/other"),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                source_pr=self.source_pr(),
            ).ok
        )
        self.assertFalse(
            verifier.verify_ready_run(
                run=self.run_metadata(
                    prs=[
                        {
                            "number": 9,
                            "base": {"sha": "c" * 40},
                            "head": {"sha": HEAD},
                        }
                    ]
                ),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                source_pr=self.source_pr(),
            ).ok
        )
        self.assertFalse(
            verifier.verify_prior_run(
                run=self.run_metadata(run_id=300),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                prior_run_id=300,
                ready_run_id=300,
                source_pr=self.source_pr(),
            ).ok
        )
        self.assertFalse(
            verifier.verify_prior_run(
                run=self.run_metadata(run_id=301),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                prior_run_id=301,
                ready_run_id=300,
                source_pr=self.source_pr(),
            ).ok
        )
        self.assertFalse(
            verifier.verify_prior_run(
                run=self.run_metadata(run_id=100, event="workflow_dispatch"),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                prior_run_id=100,
                ready_run_id=300,
                source_pr=self.source_pr(),
            ).ok
        )
        self.assertFalse(
            verifier.verify_prior_run(
                run=self.run_metadata(run_id=100, path="other.yml"),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                prior_run_id=100,
                ready_run_id=300,
                source_pr=self.source_pr(),
            ).ok
        )
        self.assertFalse(
            verifier.verify_ready_run(
                run=self.run_metadata(prs=[{"number": 8}]),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                source_pr=self.source_pr(),
            ).ok
        )

    def test_ready_and_prior_job_shapes_are_exact(self):
        ready_jobs = [
            {
                "name": "ci / ci",
                "conclusion": "success",
                "steps": [
                    {
                        "name": "Record exact-SHA CI evidence reuse",
                        "conclusion": "success",
                    },
                    {
                        "name": "Detect and run pnpm checks",
                        "conclusion": "skipped",
                    },
                    {
                        "name": "Run actions/checkout@pinned",
                        "conclusion": "skipped",
                    },
                    {
                        "name": "Checkout karsift-ai-infra",
                        "conclusion": "skipped",
                    },
                ],
            },
            {"name": "review", "conclusion": "skipped"},
            {
                "name": "ready-for-review-reuse / decide (ready_for_review)",
                "conclusion": "success",
            },
            {"name": "merge-gate / report-status", "conclusion": "success"},
            {"name": "merge-gate / auto-merge", "conclusion": "success"},
        ]
        self.assertTrue(
            verifier.verify_ready_jobs(jobs=ready_jobs, head_ref=AGENT_REF).ok
        )
        blocked_jobs = [dict(job) for job in ready_jobs]
        blocked_jobs[-1]["conclusion"] = "skipped"
        blocked = verifier.verify_ready_jobs(
            jobs=blocked_jobs,
            head_ref=AGENT_REF,
        )
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.reason, "merge_gate_auto_not_successful")
        full_ci_reran = [dict(job) for job in ready_jobs]
        full_ci_reran[0] = {
            **full_ci_reran[0],
            "steps": [
                {
                    "name": "Record exact-SHA CI evidence reuse",
                    "conclusion": "success",
                },
                {
                    "name": "Detect and run pnpm checks",
                    "conclusion": "success",
                },
            ],
        }
        self.assertEqual(
            verifier.verify_ready_jobs(
                jobs=full_ci_reran,
                head_ref=AGENT_REF,
            ).reason,
            "ci_full_validation_not_skipped",
        )
        malformed_ci_steps = [dict(job) for job in ready_jobs]
        malformed_ci_steps[0] = {**malformed_ci_steps[0], "steps": "invalid"}
        self.assertEqual(
            verifier.verify_ready_jobs(
                jobs=malformed_ci_steps,
                head_ref=AGENT_REF,
            ).reason,
            "ci_steps_malformed",
        )
        checkout_reran = [dict(job) for job in ready_jobs]
        checkout_reran[0] = {
            **checkout_reran[0],
            "steps": [
                dict(step, conclusion="success")
                if step["name"].startswith("Run actions/checkout@")
                else dict(step)
                for step in ready_jobs[0]["steps"]
            ],
        }
        self.assertEqual(
            verifier.verify_ready_jobs(
                jobs=checkout_reran,
                head_ref=AGENT_REF,
            ).reason,
            "ci_checkout_not_skipped",
        )
        self.assertTrue(
            verifier.verify_prior_jobs(
                jobs=list(pipeline_run().jobs), head_ref=AGENT_REF
            ).ok
        )
        self.assertFalse(
            verifier.verify_ready_jobs(
                jobs=ready_jobs, head_ref="unsupported/review-branch"
            ).ok
        )
        self.assertFalse(
            verifier.verify_prior_jobs(
                jobs=list(pipeline_run().jobs),
                head_ref="unsupported/review-branch",
            ).ok
        )

    def test_ready_job_requires_workflow_controlled_action_marker(self):
        jobs = [
            {
                "name": "ci / ci",
                "conclusion": "success",
                "steps": [
                    {
                        "name": "Record exact-SHA CI evidence reuse",
                        "conclusion": "success",
                    },
                    {
                        "name": "Detect and run pnpm checks",
                        "conclusion": "skipped",
                    },
                    {
                        "name": "Run actions/checkout@pinned",
                        "conclusion": "skipped",
                    },
                    {
                        "name": "Checkout karsift-ai-infra",
                        "conclusion": "skipped",
                    },
                ],
            },
            {"name": "review", "conclusion": "skipped"},
            {
                "name": "ready-for-review-reuse / decide (synchronize)",
                "conclusion": "success",
            },
            {"name": "merge-gate / report-status", "conclusion": "success"},
            {"name": "merge-gate / auto-merge", "conclusion": "skipped"},
        ]
        self.assertFalse(
            verifier.verify_ready_jobs(jobs=jobs, head_ref=AGENT_REF).ok
        )

    def test_empty_run_association_requires_exact_authenticated_source_pr(self):
        self.assertFalse(
            verifier.verify_ready_run(
                run=self.run_metadata(),
                repository="KARSIFT/example",
                pr_number=9,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_head_ref=AGENT_REF,
                source_pr=self.source_pr(base_sha="c" * 40),
            ).ok
        )

    def test_source_pr_must_be_recorded_as_merged(self):
        for override in (
            {"state": "open", "merged": False, "merged_at": None},
            {"state": "closed", "merged": False, "merged_at": None},
            {"state": "closed", "merged": True, "merged_at": None},
        ):
            with self.subTest(override=override):
                source_pr = {**self.source_pr(), **override}
                result = verifier.verify_ready_run(
                    run=self.run_metadata(),
                    repository="KARSIFT/example",
                    pr_number=9,
                    expected_head_sha=HEAD,
                    expected_base_sha=BASE,
                    expected_head_ref=AGENT_REF,
                    source_pr=source_pr,
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "source_pr_not_merged")


class WorkflowContractTests(unittest.TestCase):
    def test_reuse_workflows_are_read_only_and_merge_gate_revalidates_prior_run(self):
        reuse = (ROOT / ".github/workflows/ready-for-review-reuse.yml").read_text()
        verify = (
            ROOT / ".github/workflows/verify-ready-for-review-reuse.yml"
        ).read_text()
        merge = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        for workflow in (reuse, verify):
            self.assertIn("actions: read", workflow)
            self.assertIn("contents: read", workflow)
            self.assertIn("pull-requests: read", workflow)
            self.assertNotIn("secrets:", workflow)
            self.assertNotIn("actions/upload-artifact", workflow.lower())
            self.assertNotIn("actions/download-artifact", workflow.lower())
        self.assertIn('.path == ".github/workflows/pipeline.yml"', merge)
        self.assertIn(".head_branch == $head_ref", merge)
        self.assertIn('[ "$reuse_prior_run_id" -lt "$current_run_id" ]', merge)
        self.assertIn("prior_jobs_available=true", merge)
        self.assertIn('current_ci_result="${{ inputs.current_ci_result }}"', merge)
        self.assertIn('[ "$current_ci_result" = "success" ]', merge)
        self.assertGreaterEqual(merge.count("else\n              checks_ok=false"), 1)
        self.assertGreaterEqual(merge.count("else\n              review_check_ok=false"), 1)
        self.assertIn("pipeline_run_id:", merge)
        self.assertIn("ref: ${{ job.workflow_sha }}", merge)
        review = (ROOT / ".github/workflows/review.yml").read_text()
        plan_review = (ROOT / ".github/workflows/plan-review.yml").read_text()
        self.assertIn(r"pipeline_run_id: \`${{ github.run_id }}\`", review)
        self.assertIn(r"pipeline_run_id: \`${{ github.run_id }}\`", plan_review)
        self.assertIn('task_binding="task_id: \\`$task_id\\`"', review)
        self.assertIn('index($task)', review)

    def test_review_attestation_filter_behaves_for_task_binding_and_uniqueness(self):
        review = (ROOT / ".github/workflows/review.yml").read_text()
        filter_lines = [
            line.strip()
            for line in review.splitlines()
            if "[.[][] | select" in line
        ]
        self.assertEqual(len(filter_lines), 1)
        jq_filter = filter_lines[0][1:-2]
        binding = f"result_head_sha: `{HEAD}`"
        base_binding = f"base_sha: `{BASE}`"
        task_binding = "task_id: `VOC-104-T00`"

        def comment(task: str = "VOC-104-T00") -> dict:
            return {
                "user": {
                    "login": "karsift-ai-infra-bot[bot]",
                    "type": "Bot",
                },
                "body": "\n".join(
                    [
                        "**Live-evidence reconcile — qualified**",
                        f"task_id: `{task}`",
                        binding,
                        base_binding,
                    ]
                ),
            }

        def classify(comments: list[dict]) -> int:
            completed = subprocess.run(
                [
                    "jq",
                    "--arg",
                    "binding",
                    binding,
                    "--arg",
                    "base",
                    base_binding,
                    "--arg",
                    "task",
                    task_binding,
                    jq_filter,
                ],
                input=json.dumps([comments]),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return int(completed.stdout.strip())

        self.assertEqual(classify([comment()]), 1)
        self.assertEqual(classify([comment("VOC-104-T01")]), 0)
        self.assertEqual(classify([comment(), comment()]), 2)

    def test_template_fails_closed_to_full_path_if_decision_job_fails(self):
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()
        self.assertIn("ready-for-review-reuse:", template)
        self.assertIn("needs: [ready-for-review-reuse]", template)
        self.assertIn("always() &&", template)
        self.assertIn("needs.ci.result == 'success'", template)
        self.assertIn("reuse_prior_run_id:", template)
        ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("reuse_evidence:", ci_workflow)
        self.assertIn("Record exact-SHA CI evidence reuse", ci_workflow)
        self.assertIn("if: ${{ !inputs.reuse_evidence }}", ci_workflow)
        reuse_workflow = (
            ROOT / ".github/workflows/ready-for-review-reuse.yml"
        ).read_text()
        self.assertIn("if: inputs.event_action != 'ready_for_review'", reuse_workflow)
        self.assertIn("if: inputs.event_action == 'ready_for_review'", reuse_workflow)
        self.assertIn(
            "expected_proof_head_sha: ${{ inputs.verify_reuse_proof_head_sha }}",
            template,
        )
        self.assertIn("source_pr_number:", template)
        self.assertIn("expected_source_head_sha:", template)
        self.assertIn("expected_source_base_sha:", template)
        self.assertIn("name: decide (${{ inputs.event_action }})", reuse_workflow)
        self.assertIn("Select the fail-closed full path after evaluation failure", reuse_workflow)
        self.assertIn("steps.fail-closed.outputs.outcome", reuse_workflow)
        self.assertIn("steps.decide.outcome == 'success'", reuse_workflow)
        self.assertGreaterEqual(reuse_workflow.count("continue-on-error: true"), 3)
        merge_match = re.search(
            r"(?ms)^  merge-gate:\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9-]*:\n|\Z)",
            template,
        )
        self.assertIsNotNone(merge_match)
        self.assertIn(
            "needs: [ready-for-review-reuse, ci, review, plan-review]",
            merge_match.group(1),
        )
        self.assertIn(
            "current_ci_result: ${{ needs.ci.result }}",
            merge_match.group(1),
        )

        # A missing output (helper/job failure), unknown output, deterministic
        # full-path, and explicit fail-closed outcome all satisfy the caller's
        # `!= reuse-evidence` guard. Assert every expensive caller job retains
        # `always()` so a failed decision dependency cannot suppress it.
        ci_match = re.search(
            r"(?ms)^  ci:\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9-]*:\n|\Z)",
            template,
        )
        self.assertIsNotNone(ci_match)
        self.assertIn("always() &&", ci_match.group(1))
        self.assertIn(
            "reuse_evidence: ${{ needs.ready-for-review-reuse.outputs.outcome == 'reuse-evidence' }}",
            ci_match.group(1),
        )
        self.assertNotIn(
            "needs.ready-for-review-reuse.outputs.outcome != 'reuse-evidence'",
            ci_match.group(1),
        )
        for job_name in ("extract-package-path", "review", "plan-review"):
            match = re.search(
                rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [A-Za-z0-9][A-Za-z0-9-]*:\n|\Z)",
                template,
            )
            self.assertIsNotNone(match)
            block = match.group(1)
            self.assertIn("always() &&", block)
            self.assertIn(
                "needs.ready-for-review-reuse.outputs.outcome != 'reuse-evidence'",
                block,
            )
        for outcome in ("", "unknown", "full-path", "fail-closed-to-full-path"):
            with self.subTest(outcome=outcome):
                self.assertNotEqual(outcome, "reuse-evidence")


if __name__ == "__main__":
    unittest.main()
