import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from cross_repo_reference import (  # noqa: E402
    issue_reference,
    reject_cross_repository_closing_text,
    reject_foreign_repository_closing_text,
)


class CrossRepositoryReferenceTests(unittest.TestCase):
    def test_local_reference_closes_and_cross_repo_reference_never_does(self):
        self.assertEqual(issue_reference("KARSIFT/caller", "KARSIFT/caller", 17), "Closes #17.")
        self.assertEqual(
            issue_reference("KARSIFT/caller", "KARSIFT/infra", 17),
            "Relates to KARSIFT/caller#17.",
        )

    def test_all_github_closing_keyword_variants_are_rejected_cross_repo(self):
        for keyword in (
            "close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved"
        ):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    reject_cross_repository_closing_text(
                        f"{keyword} KARSIFT/caller#17",
                        authority_repository="KARSIFT/caller",
                        target_repository="KARSIFT/infra",
                    )

    def test_foreign_qualified_closures_are_rejected_at_merge_gate(self):
        reject_foreign_repository_closing_text(
            "Closes KARSIFT/infra#17", target_repository="KARSIFT/infra"
        )
        reject_foreign_repository_closing_text(
            "Relates to KARSIFT/caller#17", target_repository="KARSIFT/infra"
        )
        with self.assertRaises(ValueError):
            reject_foreign_repository_closing_text(
                "Fixes KARSIFT/caller#17", target_repository="KARSIFT/infra"
            )


if __name__ == "__main__":
    unittest.main()
