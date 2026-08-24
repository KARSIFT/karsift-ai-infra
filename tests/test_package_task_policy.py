"""Regression coverage for largest-safe-coherent task policy (VOC-115)."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
if str(CONFIG) not in sys.path:
    sys.path.insert(0, str(CONFIG))

from package_task_policy import PackageTaskPolicyError, validate_package_tasks  # noqa: E402


def package(*task_sections: str, justification: str = "") -> str:
    return "\n".join(("# VOC-900 — Tasks", justification, *task_sections))


FIRST = """## VOC-900-T00 — Deliver the complete outcome

Keep backend, frontend, contracts, tests, docs, configuration, and evidence together.
"""


class PackageTaskPolicyTests(unittest.TestCase):
    def test_one_large_task_and_related_skills_pass(self):
        sections = validate_package_tasks(FIRST, "VOC-900")
        self.assertEqual([item.task_id for item in sections], ["VOC-900-T00"])
        skills = FIRST.replace(
            "complete outcome",
            "related skills, adapters, configuration, docs, and tests",
        )
        self.assertEqual(len(validate_package_tasks(skills, "VOC-900")), 1)

    def test_extra_task_needs_allowed_reason_and_concrete_explanation(self):
        missing = package(FIRST, "## VOC-900-T01 — Follow-up")
        with self.assertRaises(PackageTaskPolicyError) as caught:
            validate_package_tasks(missing, "VOC-900")
        self.assertEqual(caught.exception.code, "missing_split_reason")

        invalid = package(
            FIRST,
            """## VOC-900-T01 — Docs

- Split reason: docs-vs-code — keeping file types separate is convenient.
""",
        )
        with self.assertRaises(PackageTaskPolicyError) as caught:
            validate_package_tasks(invalid, "VOC-900")
        self.assertEqual(caught.exception.code, "invalid_split_reason_slug")

        unexplained = package(
            FIRST,
            """## VOC-900-T01 — Runtime

- Split reason: merge-order-dependency
""",
        )
        with self.assertRaises(PackageTaskPolicyError) as caught:
            validate_package_tasks(unexplained, "VOC-900")
        self.assertEqual(caught.exception.code, "split_reason_requires_explanation")

    def test_real_boundary_passes_and_preserves_order(self):
        text = package(
            FIRST,
            """## VOC-900-T01 — Runtime

- Split reason: merge-order-dependency — runtime cannot land until the independently merged schema exists.
""",
        )
        sections = validate_package_tasks(text, "VOC-900")
        self.assertEqual(
            [item.task_id for item in sections],
            ["VOC-900-T00", "VOC-900-T01"],
        )

    def test_review_size_needs_specific_explanation(self):
        vague = package(
            FIRST,
            """## VOC-900-T01 — More work

- Split reason: single-pr-review-size-boundary — this is a large change to review.
""",
        )
        with self.assertRaises(PackageTaskPolicyError) as caught:
            validate_package_tasks(vague, "VOC-900")
        self.assertEqual(caught.exception.code, "split_reason_requires_explanation")

    def test_more_than_three_tasks_needs_package_justification(self):
        extra = tuple(
            f"""## VOC-900-T0{index} — Boundary {index}

- Split reason: merge-order-dependency — task {index} cannot begin until the prior independently merged contract exists.
"""
            for index in range(1, 4)
        )
        with self.assertRaises(PackageTaskPolicyError) as caught:
            validate_package_tasks(package(FIRST, *extra), "VOC-900")
        self.assertEqual(caught.exception.code, "missing_multi_task_justification")

        justification = """## Package-level multi-task justification

All four hard merge-order boundaries use separately owned contracts that cannot safely land together.
"""
        self.assertEqual(
            len(validate_package_tasks(package(FIRST, *extra, justification=justification), "VOC-900")),
            4,
        )


if __name__ == "__main__":
    unittest.main()
