from __future__ import annotations

import sys
import unittest
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from actions_check_recovery import (  # noqa: E402
    RecoveryError,
    format_timeout_diagnostics,
    missing_contexts,
    missing_push_workflow_runs,
    plan_recovery_dispatches,
    recovery_complete,
    required_contexts,
    select_gate_evidence,
    suppress_active_or_successful_dispatches,
    staging_deploy_required,
    validate_mode,
    validate_promotion_target,
)
from verify_post_promotion_workflow import (  # noqa: E402
    verify_carrier_ref as verify_post_promotion_carrier_ref,
    verify_post_promotion_run,
    verify_promotion_merged,
)
from verify_promotion_check_recovery import (  # noqa: E402
    verify_carrier_ref as verify_promotion_carrier_ref,
    verify_promotion_pr_identity,
    verify_required_checks,
)


HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
REPOSITORY = "KARSIFT/example"


def load_hosted_runner(filename: str, module_name: str):
    path = ROOT / "config" / filename
    spec = spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def gate_payload(checks: list[dict]) -> dict:
    return evaluate_summary(checks)


def evaluate_summary(checks: list[dict]) -> dict:
    from authoritative_checks import evaluate, select_authoritative

    selected = select_authoritative(
        checks,
        [],
        expected={"head_sha": HEAD_SHA},
    )
    return evaluate(selected)


class ActionsCheckRecoveryTests(unittest.TestCase):
    def test_recovery_reuses_ci_job_id_for_required_context(self):
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text(encoding="utf-8")
        ci_block = template.split("  ci:", 1)[1].split("\n  plan-review:", 1)[0]
        reuse_block = template.split("  ready-for-review-reuse:", 1)[1].split(
            "\n  ci:", 1
        )[0]
        self.assertIn("inputs.action == 'recover-promotion-pr-checks'", ci_block)
        self.assertIn("inputs.action == 'recover-promotion-pr-checks'", reuse_block)
        self.assertIn("event_action:", reuse_block)
        self.assertIn("'recovery'", reuse_block)
        self.assertIn(
            "github.event.pull_request.base.sha || ''",
            reuse_block,
        )
        self.assertNotIn(
            "github.event.pull_request.base.sha || github.sha",
            reuse_block,
        )
        self.assertNotIn("\n  recover-promotion-pr-checks:", template)
        dispatch_inputs = template.split("  workflow_dispatch:", 1)[1].split(
            "\n# A synchronize event", 1
        )[0]
        self.assertLessEqual(
            len(re.findall(r"^      [a-z0-9_]+:$", dispatch_inputs, re.MULTILINE)),
            25,
        )

    def test_scheduled_secondary_wake_retries_exact_integration_head(self):
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text(encoding="utf-8")
        resolver = template.split(
            "  resolve-integration-recovery-target:", 1
        )[1].split("\n  recover-integration-push:", 1)[0]
        recovery = template.split("  recover-integration-push:", 1)[1].split(
            "\n  implement:", 1
        )[0]
        reusable = (ROOT / ".github/workflows/recover-actions-checks.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("inputs.action == 'recover-integration-push'", resolver)
        self.assertIn("git/ref/heads/develop", resolver)
        self.assertIn('[[ "$target_sha" =~ ^[0-9a-f]{40}$ ]]', resolver)
        self.assertIn("recovery_needed:", resolver)
        self.assertIn("has_successful_run", resolver)
        self.assertIn("deploy_required", resolver)
        self.assertIn("outputs.recovery_needed == 'true'", recovery)
        self.assertIn("inputs.action == 'recover-integration-push'", recovery)
        self.assertIn("recovery_mode: integration_push", recovery)
        self.assertIn(
            "target_sha: ${{ needs.resolve-integration-recovery-target.outputs.target_sha }}",
            recovery,
        )
        self.assertIn("actions: write", recovery)
        self.assertIn("actions-check-recovery-${{ inputs.recovery_mode }}-${{ inputs.target_sha }}", reusable)
        self.assertIn("cancel-in-progress: false", reusable)

    def test_operator_can_invoke_integration_recovery_without_free_form_sha(self):
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text(encoding="utf-8")
        dispatch_inputs = template.split("  workflow_dispatch:", 1)[1].split(
            "\n# A synchronize event", 1
        )[0]
        self.assertIn("recover-integration-push", dispatch_inputs)
        self.assertNotIn("integration_recovery_target_sha:", dispatch_inputs)
        resolver = template.split(
            "  resolve-integration-recovery-target:", 1
        )[1].split("\n  recover-integration-push:", 1)[0]
        self.assertIn("git/ref/heads/develop", resolver)

    def test_promotion_required_contexts_are_ruleset_equivalents(self):
        self.assertEqual(
            required_contexts("promotion_pr"),
            ("governance-policy", "validate", "ci / ci"),
        )

    def test_positive_recovery_plan_for_promotion_pr(self):
        plans = plan_recovery_dispatches(
            mode="promotion_pr",
            target_sha=HEAD_SHA,
            branch_ref="develop",
            pr_number=947,
        )
        files = [plan.workflow_file for plan in plans]
        self.assertEqual(
            files,
            [
                "governance-policy.yml",
                "repository-governance.yml",
                "pipeline.yml",
            ],
        )
        self.assertEqual(plans[-1].inputs["action"], "recover-promotion-pr-checks")
        self.assertEqual(plans[-1].inputs["promotion_pr_number"], "947")

    def test_integration_push_plan_dispatches_push_workflows(self):
        plans = plan_recovery_dispatches(
            mode="integration_push",
            target_sha=HEAD_SHA,
            branch_ref="develop",
        )
        self.assertEqual(
            [plan.workflow_file for plan in plans],
            ["repository-governance.yml", "deploy-staging.yml"],
        )
        self.assertTrue(
            all(plan.inputs == {"recovery_target_sha": HEAD_SHA} for plan in plans)
        )
        self.assertNotIn(
            "recover-actions-checks.yml",
            [plan.workflow_file for plan in plans],
        )

    def test_docs_only_integration_recovery_does_not_force_staging_deploy(self):
        self.assertFalse(staging_deploy_required(["docs/operations/example.md"]))
        plans = plan_recovery_dispatches(
            mode="integration_push",
            target_sha=HEAD_SHA,
            branch_ref="develop",
            integration_deploy_required=False,
        )
        self.assertEqual(
            [plan.workflow_file for plan in plans],
            ["repository-governance.yml"],
        )
        self.assertTrue(
            recovery_complete(
                mode="integration_push",
                gate_summary=evaluate_summary([]),
                workflow_runs=[
                    {
                        "head_sha": HEAD_SHA,
                        "path": ".github/workflows/repository-governance.yml",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
                head_sha=HEAD_SHA,
                integration_deploy_required=False,
            )
        )

    def test_runtime_and_root_paths_still_require_staging_deploy(self):
        self.assertTrue(staging_deploy_required(["apps/api/app/api.go"]))
        self.assertTrue(staging_deploy_required(["package.json"]))
        self.assertTrue(
            staging_deploy_required([".github/workflows/deploy-staging.yml"])
        )

    def test_integration_recovery_is_noop_after_exact_sha_runs_succeed(self):
        runs = [
            {
                "head_sha": HEAD_SHA,
                "path": f".github/workflows/{workflow}",
                "status": "completed",
                "conclusion": "success",
            }
            for workflow in ("repository-governance.yml", "deploy-staging.yml")
        ]
        self.assertTrue(
            recovery_complete(
                mode="integration_push",
                gate_summary=evaluate_summary([]),
                workflow_runs=runs,
                head_sha=HEAD_SHA,
            )
        )

    def test_integration_recovery_waits_for_runs_to_succeed(self):
        runs = [
            {
                "head_sha": HEAD_SHA,
                "path": f".github/workflows/{workflow}",
                "status": "in_progress",
                "conclusion": None,
            }
            for workflow in ("repository-governance.yml", "deploy-staging.yml")
        ]
        self.assertFalse(
            recovery_complete(
                mode="integration_push",
                gate_summary=evaluate_summary([]),
                workflow_runs=runs,
                head_sha=HEAD_SHA,
            )
        )

    def test_integration_recovery_rejects_neutral_runs(self):
        runs = [
            {
                "head_sha": HEAD_SHA,
                "path": f".github/workflows/{workflow}",
                "status": "completed",
                "conclusion": "neutral",
            }
            for workflow in ("repository-governance.yml", "deploy-staging.yml")
        ]
        self.assertFalse(
            recovery_complete(
                mode="integration_push",
                gate_summary=evaluate_summary([]),
                workflow_runs=runs,
                head_sha=HEAD_SHA,
            )
        )

    def test_second_wake_waits_without_redispatching_active_exact_sha_runs(self):
        plans = plan_recovery_dispatches(
            mode="integration_push",
            target_sha=HEAD_SHA,
            branch_ref="develop",
        )
        runs = [
            {
                "head_sha": HEAD_SHA,
                "path": f".github/workflows/{plan.workflow_file}",
                "status": "in_progress",
                "conclusion": None,
            }
            for plan in plans
        ]
        self.assertEqual(
            suppress_active_or_successful_dispatches(
                plans, runs, head_sha=HEAD_SHA
            ),
            [],
        )
        self.assertFalse(
            recovery_complete(
                mode="integration_push",
                gate_summary=evaluate_summary([]),
                workflow_runs=runs,
                head_sha=HEAD_SHA,
            )
        )

    def test_neutral_exact_sha_run_is_redispatched(self):
        plans = plan_recovery_dispatches(
            mode="integration_push",
            target_sha=HEAD_SHA,
            branch_ref="develop",
        )
        runs = [
            {
                "head_sha": HEAD_SHA,
                "path": f".github/workflows/{plan.workflow_file}",
                "status": "completed",
                "conclusion": "neutral",
            }
            for plan in plans
        ]
        self.assertEqual(
            suppress_active_or_successful_dispatches(
                plans, runs, head_sha=HEAD_SHA
            ),
            plans,
        )

    def test_missing_contexts_fail_closed(self):
        summary = evaluate_summary(
            [
                {
                    **{"head_sha": HEAD_SHA},
                    "id": 1,
                    "name": "governance-policy",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-24T00:00:00Z",
                }
            ]
        )
        missing = missing_contexts(summary, required_contexts("promotion_pr"))
        self.assertIn("validate", missing)
        self.assertIn("ci / ci", missing)

    def test_neutral_required_check_runs_remain_missing(self):
        summary = evaluate_summary(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": index,
                    "name": name,
                    "status": "completed",
                    "conclusion": "neutral",
                    "app": {"slug": "github-actions"},
                    "started_at": f"2026-08-24T00:00:0{index}Z",
                }
                for index, name in enumerate(required_contexts("promotion_pr"), 1)
            ]
        )
        self.assertEqual(
            missing_contexts(summary, required_contexts("promotion_pr")),
            list(required_contexts("promotion_pr")),
        )

    def test_promotion_verifier_sanitizes_status_api_failures(self):
        runner = (
            ROOT / "config/verify-promotion-check-recovery-runner.py"
        ).read_text(encoding="utf-8")
        self.assertIn("if status_request.returncode != 0:", runner)
        self.assertIn('VerificationError("github_metadata_read_failed")', runner)

    def test_hosted_verifiers_use_environment_repo_context_for_gh_api(self):
        runner_files = (
            "verify-promotion-check-recovery-runner.py",
            "verify-post-promotion-workflow-runner.py",
        )
        for filename in runner_files:
            runner = load_hosted_runner(
                filename,
                filename.removesuffix(".py").replace("-", "_") + "_test",
            )
            with self.subTest(runner=runner.__name__), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="{}"),
            ) as run_mock:
                self.assertEqual(
                    runner.gh_api(
                        "test-token",
                        REPOSITORY,
                        f"repos/{REPOSITORY}/pulls/947",
                    ),
                    {},
                )
                command = run_mock.call_args.args[0]
                self.assertEqual(command[:2], ["gh", "api"])
                self.assertNotIn("--repo", command)
                self.assertEqual(
                    run_mock.call_args.kwargs["env"]["GH_REPO"], REPOSITORY
                )
                self.assertNotIn(
                    '"--repo"',
                    (ROOT / "config" / filename).read_text(encoding="utf-8"),
                )

    def test_wrong_sha_workflow_runs_remain_missing(self):
        runs = [
            {
                "head_sha": "c" * 40,
                "path": ".github/workflows/repository-governance.yml",
                "status": "completed",
                "conclusion": "success",
            }
        ]
        missing = missing_push_workflow_runs(
            runs,
            head_sha=HEAD_SHA,
            required_workflows=("repository-governance.yml",),
        )
        self.assertEqual(missing, ["repository-governance.yml"])

    def test_recovery_complete_requires_success_not_pending(self):
        summary = evaluate_summary(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": 1,
                    "name": "governance-policy",
                    "status": "in_progress",
                    "conclusion": None,
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-24T00:00:00Z",
                }
            ]
        )
        self.assertFalse(
            recovery_complete(
                mode="promotion_pr",
                gate_summary=summary,
                workflow_runs=[],
                head_sha=HEAD_SHA,
            )
        )

    def test_invalid_mode_fails_closed(self):
        with self.assertRaises(RecoveryError):
            validate_mode("fabricate")

    def test_fabricated_commit_status_cannot_satisfy_required_context(self):
        from authoritative_checks import evaluate, select_authoritative

        statuses = [
            {
                "head_sha": HEAD_SHA,
                "id": index,
                "context": name,
                "state": "success",
                "creator": {"login": "fixture"},
                "created_at": f"2026-08-24T00:00:{index:02d}Z",
            }
            for index, name in enumerate(required_contexts("promotion_pr"), start=1)
        ]
        summary = evaluate(
            select_authoritative([], statuses, expected={"head_sha": HEAD_SHA})
        )
        self.assertEqual(
            missing_contexts(summary, required_contexts("promotion_pr")),
            list(required_contexts("promotion_pr")),
        )

    def test_skipped_check_run_cannot_satisfy_required_context(self):
        summary = evaluate_summary(
            [
                {
                    "head_sha": HEAD_SHA,
                    "id": 1,
                    "name": "governance-policy",
                    "status": "completed",
                    "conclusion": "skipped",
                    "app": {"slug": "github-actions"},
                    "started_at": "2026-08-24T00:00:00Z",
                }
            ]
        )
        self.assertIn(
            "governance-policy",
            missing_contexts(summary, required_contexts("promotion_pr")),
        )

    def test_timeout_diagnostics_are_bounded_and_sanitized(self):
        diagnostic = format_timeout_diagnostics(
            mode="promotion_pr",
            target_sha=HEAD_SHA,
            pr_number=947,
            missing=("validate",),
            gate_summary={"pending": 1, "failed": 0, "successful": 2},
            timeout_seconds=1800,
        )
        self.assertIn("timeout_seconds: 1800", diagnostic)
        self.assertIn("missing_checks: validate", diagnostic)
        self.assertNotIn("token", diagnostic.lower())
        self.assertNotIn("log", diagnostic.lower())

    def test_release_duplicate_and_app_token_guards_remain_fail_closed(self):
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("duplicate release audits", release)
        self.assertIn("duplicate promotion PRs", release)
        self.assertIn("steps.app-token.outputs.token", release)
        self.assertIn(
            "GitHub App credentials are required for workflow-triggering merges",
            merge_gate,
        )
        recovery_block = merge_gate.split(
            "Recover missing integration push workflows for merged SHA", 1
        )[1]
        self.assertIn("steps.merge.outcome == 'success'", recovery_block)
        self.assertIn("steps.app-token.outputs.token", recovery_block)

    def test_promotion_recovery_requires_exact_open_pr_head(self):
        valid = {
            "number": 947,
            "state": "open",
            "head": {"sha": HEAD_SHA, "ref": "develop"},
        }
        validate_promotion_target(
            valid,
            target_sha=HEAD_SHA,
            branch_ref="develop",
            pr_number=947,
        )
        for changed in (
            {**valid, "number": 948},
            {**valid, "state": "closed"},
            {**valid, "head": {"sha": "c" * 40, "ref": "develop"}},
            {**valid, "head": {"sha": HEAD_SHA, "ref": "feature"}},
        ):
            with self.assertRaisesRegex(RecoveryError, "promotion_target_mismatch"):
                validate_promotion_target(
                    changed,
                    target_sha=HEAD_SHA,
                    branch_ref="develop",
                    pr_number=947,
                )

        runner = (ROOT / "config/actions-check-recovery-runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("validate_promotion_target(", runner)
        self.assertIn("pulls/{pr_number}", runner)

    def test_verify_promotion_pr_identity_rejects_non_promotion_pair(self):
        pr = {
            "number": 947,
            "state": "open",
            "head": {"sha": HEAD_SHA, "ref": "feature", "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main"},
        }
        result = verify_promotion_pr_identity(
            pr, repository=REPOSITORY, pr_number=947
        )
        self.assertFalse(result.ok)

    def test_verify_promotion_pr_identity_accepts_rest_shape(self):
        pr = {
            "number": 947,
            "state": "open",
            "merged": False,
            "head": {"sha": HEAD_SHA, "ref": "develop", "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main"},
        }
        result = verify_promotion_pr_identity(
            pr, repository=REPOSITORY, pr_number=947
        )
        self.assertTrue(result.ok)

    def test_closed_unmerged_promotion_is_rejected(self):
        pr = {
            "number": 947,
            "state": "closed",
            "merged": False,
            "head": {"sha": HEAD_SHA, "ref": "develop", "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main"},
        }
        result = verify_promotion_pr_identity(
            pr, repository=REPOSITORY, pr_number=947
        )
        self.assertFalse(result.ok)

    def test_verify_required_checks_accepts_complete_gate_summary(self):
        checks = []
        for index, name in enumerate(required_contexts("promotion_pr"), start=1):
            checks.append(
                {
                    "head_sha": HEAD_SHA,
                    "id": index,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"slug": "github-actions"},
                    "started_at": f"2026-08-24T00:00:{index:02d}Z",
                }
            )
        summary = evaluate_summary(checks)
        result = verify_required_checks(summary, head_sha=HEAD_SHA)
        self.assertTrue(result.ok)

    def test_carrier_sha_is_valid_but_distinct_from_promotion_sha(self):
        carrier_sha = "c" * 40
        self.assertNotEqual(carrier_sha, HEAD_SHA)
        self.assertTrue(verify_promotion_carrier_ref(carrier_sha).ok)
        self.assertTrue(verify_post_promotion_carrier_ref(carrier_sha).ok)

    def test_verify_post_promotion_run_requires_single_success(self):
        runs = [
            {
                "head_sha": HEAD_SHA,
                "path": ".github/workflows/deploy-production.yml",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "repository": {"full_name": REPOSITORY},
            }
        ]
        result = verify_post_promotion_run(
            runs, repository=REPOSITORY, merge_sha=HEAD_SHA
        )
        self.assertTrue(result.ok)

    def test_verify_merged_promotion_accepts_rest_shape(self):
        pr = {
            "number": 947,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": HEAD_SHA,
            "head": {"ref": "develop", "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main"},
        }
        result = verify_promotion_merged(
            pr, repository=REPOSITORY, pr_number=947
        )
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
