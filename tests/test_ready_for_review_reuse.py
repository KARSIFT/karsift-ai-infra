from pathlib import Path
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
sys.path.insert(0, str(CONFIG))

import ready_for_review_reuse as policy  # noqa: E402
import verify_ready_for_review_reuse as verifier  # noqa: E402


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


def review_comment(*, plan=False, verdict="PASS", login=None, comment_id=1):
    identity = [f"package_path: `{PACKAGE}`"] if plan else [
        "task_id: `VOC-104-T00`",
        f"package_path: `{PACKAGE}`",
        "authority_issue: `875`",
    ]
    return {
        "id": comment_id,
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
        self.assertEqual(
            decide(comments=[review_comment(login="human-user")]).outcome,
            "full-path",
        )
        self.assertEqual(
            decide(pr_body=agent_body("../../unsafe")).outcome,
            "full-path",
        )
        self.assertEqual(
            decide(comments=[review_comment(verdict="FAIL")]).outcome,
            "full-path",
        )
        wrong_run = review_comment()
        wrong_run["body"] = wrong_run["body"].replace(
            "pipeline_run_id: `100`",
            "pipeline_run_id: `99`",
        )
        self.assertEqual(decide(comments=[wrong_run]).outcome, "full-path")

    def test_non_green_unrelated_pr_check_takes_full_path(self):
        self.assertEqual(decide().outcome, "reuse-evidence")
        self.assertEqual(
            decide(
                pr_checks=[
                    {"name": "ci", "state": "PENDING"},
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


class MergeGateReuseTests(unittest.TestCase):
    def setUp(self):
        self.pr_checks = [
            {"name": policy.REQUIRED_CI_JOB, "state": "SUCCESS"},
            {"name": policy.AGENT_PUBLISHER_JOB, "state": "SUCCESS"},
            {"name": "ci", "state": "SKIPPED"},
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
            item for item in self.pr_checks if item["name"] != "ci"
        ]
        self.assertFalse(
            policy.compute_checks_ok_with_reuse(
                pr_checks=without_ci,
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


class ProofVerifierTests(unittest.TestCase):
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
            ).ok
        )

    def test_ready_and_prior_job_shapes_are_exact(self):
        ready_jobs = [
            {"name": "ci", "conclusion": "skipped"},
            {"name": "review", "conclusion": "skipped"},
            {"name": "ready-for-review-reuse / decide", "conclusion": "success"},
            {"name": "merge-gate / report-status", "conclusion": "success"},
            {"name": "merge-gate / auto-merge", "conclusion": "skipped"},
        ]
        self.assertTrue(
            verifier.verify_ready_jobs(jobs=ready_jobs, head_ref=AGENT_REF).ok
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
        self.assertGreaterEqual(merge.count("else\n              checks_ok=false"), 1)
        self.assertGreaterEqual(merge.count("else\n              review_check_ok=false"), 1)
        self.assertIn("pipeline_run_id:", merge)
        review = (ROOT / ".github/workflows/review.yml").read_text()
        plan_review = (ROOT / ".github/workflows/plan-review.yml").read_text()
        self.assertIn(r"pipeline_run_id: \`${{ github.run_id }}\`", review)
        self.assertIn(r"pipeline_run_id: \`${{ github.run_id }}\`", plan_review)

    def test_template_fails_closed_to_full_path_if_decision_job_fails(self):
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()
        self.assertIn("ready-for-review-reuse:", template)
        self.assertIn("needs: [ready-for-review-reuse]", template)
        self.assertIn("always() &&", template)
        self.assertIn("needs.ci.result == 'success'", template)
        self.assertIn("reuse_prior_run_id:", template)
        reuse_workflow = (
            ROOT / ".github/workflows/ready-for-review-reuse.yml"
        ).read_text()
        self.assertIn("if: inputs.event_action != 'ready_for_review'", reuse_workflow)
        self.assertIn("if: inputs.event_action == 'ready_for_review'", reuse_workflow)
        self.assertIn("expected_proof_head_sha: ${{ github.sha }}", template)


if __name__ == "__main__":
    unittest.main()
