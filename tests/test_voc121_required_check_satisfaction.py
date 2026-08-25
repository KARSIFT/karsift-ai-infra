from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from required_check_satisfaction import (
    missing_required_pr_contexts,
    parse_gh_pr_checks_json,
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


if __name__ == "__main__":
    unittest.main()
