import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from promotion_evaluator import promotion_decision  # noqa: E402


class PromotionEvaluatorTests(unittest.TestCase):
    def test_terminal_or_already_promoted_is_successful_noop(self):
        gates = {"total_count": 1, "pending": 0, "failed": 0}
        for state in ("MERGED", "CLOSED"):
            self.assertEqual(
                promotion_decision(pr={"state": state}, expected_head="a", gates=gates, production_contains_integration=False),
                "already-terminal",
            )
        self.assertEqual(
            promotion_decision(pr=None, expected_head="a", gates=gates, production_contains_integration=True),
            "already-promoted",
        )

    def test_exact_head_and_latest_gates_control_merge(self):
        pr = {"state": "OPEN", "isDraft": False, "headRefOid": "a"}
        self.assertEqual(
            promotion_decision(pr=pr, expected_head="a", gates={"total_count": 2, "pending": 0, "failed": 0}, production_contains_integration=False),
            "merge",
        )
        self.assertEqual(
            promotion_decision(pr=pr, expected_head="a", gates={"total_count": 2, "pending": 1, "failed": 0}, production_contains_integration=False),
            "pending",
        )
        self.assertEqual(
            promotion_decision(pr=pr, expected_head="a", gates={"total_count": 2, "pending": 0, "failed": 1}, production_contains_integration=False),
            "blocked",
        )
        self.assertEqual(
            promotion_decision(pr=pr, expected_head="b", gates={"total_count": 2, "pending": 0, "failed": 0}, production_contains_integration=False),
            "stale",
        )


if __name__ == "__main__":
    unittest.main()
