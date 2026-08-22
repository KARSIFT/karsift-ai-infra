import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from authoritative_checks import (  # noqa: E402
    EvidenceError,
    evaluate,
    flatten_check_runs,
    flatten_statuses,
    select_authoritative,
)


IDENTITY = {
    "repository": "KARSIFT/example",
    "head_sha": "a" * 40,
    "base_sha": "b" * 40,
    "pr_number": 42,
}


def check(identifier, status, conclusion, second, name="ci / test", **overrides):
    item = {
        **IDENTITY,
        "id": identifier,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": "github-actions"},
        "started_at": f"2026-08-22T00:00:{second:02d}Z",
    }
    item.update(overrides)
    return item


def status(identifier, state, second, name="legacy", **overrides):
    item = {
        **IDENTITY,
        "id": identifier,
        "context": name,
        "state": state,
        "creator": {"login": "trusted-bot"},
        "updated_at": f"2026-08-22T00:00:{second:02d}Z",
    }
    item.update(overrides)
    return item


class AuthoritativeCheckTests(unittest.TestCase):
    def select(self, checks, statuses=()):
        return evaluate(select_authoritative(checks, statuses, expected=IDENTITY))

    def test_later_pass_supersedes_historical_failure(self):
        result = self.select(
            [check(1, "completed", "failure", 1), check(2, "completed", "success", 2)]
        )
        self.assertEqual((result["failed"], result["successful"]), (0, 1))

    def test_later_failure_or_pending_supersedes_pass(self):
        for terminal, conclusion, expected in (
            ("completed", "failure", "FAILURE"),
            ("completed", "cancelled", "FAILURE"),
            ("completed", "timed_out", "FAILURE"),
            ("in_progress", None, "PENDING"),
        ):
            with self.subTest(terminal=terminal, conclusion=conclusion):
                result = self.select(
                    [check(1, "completed", "success", 1), check(2, terminal, conclusion, 2)]
                )
                self.assertEqual(result["checks"][0]["state"], expected)

    def test_identity_mismatch_and_ambiguity_fail_closed(self):
        for field, value in (
            ("repository", "foreign/repo"),
            ("head_sha", "c" * 40),
            ("base_sha", "d" * 40),
            ("pr_number", 99),
        ):
            with self.subTest(field=field):
                with self.assertRaises(EvidenceError):
                    self.select([check(1, "completed", "success", 1, **{field: value})])
        with self.assertRaises(EvidenceError):
            self.select(
                [check(1, "completed", "success", 1)],
                [status(2, "success", 2, name="ci / test")],
            )
        with self.assertRaises(EvidenceError):
            self.select(
                [
                    check(1, "completed", "success", 1),
                    check(2, "completed", "success", 2, app={"slug": "foreign-app"}),
                ]
            )

    def test_pagination_over_one_hundred_and_truncation(self):
        items = [check(i + 1, "completed", "success", i % 60, name=f"gate-{i}") for i in range(101)]
        pages = [
            {"total_count": 101, "check_runs": items[:100]},
            {"total_count": 101, "check_runs": items[100:]},
        ]
        self.assertEqual(len(flatten_check_runs(pages)), 101)
        with self.assertRaises(EvidenceError):
            flatten_check_runs(pages[:1])
        status_pages = [{"total_count": 1, "statuses": [status(1, "success", 1)]}]
        self.assertEqual(len(flatten_statuses(status_pages)), 1)

    def test_id_breaks_timestamp_tie_and_self_prefix_is_excluded(self):
        selected = select_authoritative(
            [
                check(1, "completed", "failure", 1),
                check(2, "completed", "success", 1),
                check(3, "in_progress", None, 3, name="adopt / adopt"),
            ],
            [],
            expected=IDENTITY,
            exclude_prefixes=("adopt /",),
        )
        self.assertEqual(
            selected,
            [{
                "name": "ci / test",
                "state": "SUCCESS",
                "kind": "check_run",
                "id": 2,
                "workflow": "github-actions",
                "run_id": 0,
            }],
        )


if __name__ == "__main__":
    unittest.main()
