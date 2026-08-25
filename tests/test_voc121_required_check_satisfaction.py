from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from required_check_satisfaction import (
    missing_required_pr_contexts,
    parse_gh_pr_checks_json,
    plan_required_check_recovery,
    SatisfactionError,
)


class RequiredCheckSatisfactionTests(unittest.TestCase):
    def test_cancelled_required_context_stays_missing_despite_gate_success(self):
        pr_checks = [
            {"name": "governance-policy", "state": "FAILURE"},
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        missing = missing_required_pr_contexts(
            pr_checks,
            ("governance-policy", "validate", "ci / ci"),
        )
        self.assertEqual(missing, ["governance-policy"])

    def test_all_required_success_reports_no_missing(self):
        pr_checks = [
            {"name": "governance-policy", "state": "SUCCESS"},
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        self.assertEqual(
            missing_required_pr_contexts(
                pr_checks,
                ("governance-policy", "validate", "ci / ci"),
            ),
            [],
        )

    def test_duplicate_name_fails_closed_when_any_selected_entry_is_not_success(self):
        pr_checks = [
            {"name": "governance-policy", "state": "SUCCESS"},
            {"name": "governance-policy", "state": "FAILURE"},
        ]
        self.assertEqual(
            missing_required_pr_contexts(pr_checks, ("governance-policy",)),
            ["governance-policy"],
        )

    def test_parse_gh_pr_checks_json_normalizes_payload(self):
        payload = parse_gh_pr_checks_json(
            [{"name": "governance-policy", "state": "PENDING"}]
        )
        self.assertEqual(payload, [{"name": "governance-policy", "state": "PENDING"}])

    def test_invalid_payload_fails_closed(self):
        with self.assertRaises(SatisfactionError):
            parse_gh_pr_checks_json({"name": "governance-policy"})

    def test_cancelled_selected_run_plans_exact_rerun_despite_alternate_success(self):
        checks = [
            {
                "name": "governance-policy",
                "state": "CANCELLED",
                "event": "pull_request",
                "workflow": "Governance policy",
                "link": "https://github.com/KARSIFT/example/actions/runs/123/job/456",
            },
            {
                "name": "governance-policy",
                "state": "SUCCESS",
                "event": "workflow_dispatch",
                "workflow": "Governance policy",
                "link": "https://github.com/KARSIFT/example/actions/runs/789/job/999",
            },
            {"name": "validate", "state": "SUCCESS"},
            {"name": "ci / ci", "state": "SUCCESS"},
        ]
        reruns, dispatches = plan_required_check_recovery(
            checks,
            ("governance-policy", "validate", "ci / ci"),
            repository="KARSIFT/example",
        )
        self.assertEqual([plan.run_id for plan in reruns], [123])
        self.assertEqual(dispatches, [])

    def test_absent_context_dispatches_but_pending_context_waits(self):
        reruns, dispatches = plan_required_check_recovery(
            [{"name": "validate", "state": "PENDING"}],
            ("governance-policy", "validate"),
            repository="KARSIFT/example",
        )
        self.assertEqual(reruns, [])
        self.assertEqual(dispatches, ["governance-policy"])

    def test_failed_status_or_foreign_workflow_cannot_be_rerun(self):
        for check in (
            {
                "name": "governance-policy",
                "state": "FAILURE",
                "event": "",
                "workflow": "",
                "link": "https://github.com/KARSIFT/example/actions/runs/123",
            },
            {
                "name": "governance-policy",
                "state": "FAILURE",
                "event": "pull_request",
                "workflow": "Foreign",
                "link": "https://github.com/KARSIFT/example/actions/runs/123/job/456",
            },
        ):
            with self.subTest(check=check):
                with self.assertRaises(SatisfactionError):
                    plan_required_check_recovery(
                        [check],
                        ("governance-policy",),
                        repository="KARSIFT/example",
                    )


if __name__ == "__main__":
    unittest.main()
