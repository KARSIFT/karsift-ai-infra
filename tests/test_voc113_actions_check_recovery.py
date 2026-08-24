from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from actions_check_recovery import (  # noqa: E402
    RecoveryError,
    missing_contexts,
    missing_push_workflow_runs,
    plan_recovery_dispatches,
    recovery_complete,
    required_contexts,
    select_gate_evidence,
    validate_mode,
)
from verify_post_promotion_workflow import (  # noqa: E402
    verify_post_promotion_run,
    verify_promotion_merged,
)
from verify_promotion_check_recovery import (  # noqa: E402
    verify_promotion_pr_identity,
    verify_required_checks,
)


HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
REPOSITORY = "KARSIFT/example"


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
        self.assertIn("inputs.action == 'recover-promotion-pr-checks'", ci_block)
        self.assertNotIn("\n  recover-promotion-pr-checks:", template)

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
