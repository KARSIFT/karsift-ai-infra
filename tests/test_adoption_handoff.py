from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AdoptionHandoffPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.adopt = (ROOT / ".github/workflows/adopt.yml").read_text()
        cls.template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def test_founder_comment_is_not_a_merge_path(self):
        self.assertNotIn("approve-and-merge:", self.merge_gate)
        self.assertNotIn("COMMENT_AUTHOR", self.merge_gate)

    def test_r4_uses_the_same_non_human_gates(self):
        auto_merge = self.merge_gate.split("  auto-merge:", 1)[1]
        self.assertNotIn("risk != 'R4'", auto_merge)
        self.assertNotIn("automatic_merge_allowed != 'false'", auto_merge)
        self.assertIn("risk != 'unknown'", auto_merge)
        self.assertIn("verdict != 'PENDING'", auto_merge)

    def test_merge_uses_app_token_before_merging(self):
        mint = self.merge_gate.index("- name: Mint App installation token")
        merge = self.merge_gate.index("- name: Merge automatically")
        self.assertLess(mint, merge)
        merge_block = self.merge_gate[merge:]
        self.assertIn("GH_TOKEN: ${{ steps.app-token.outputs.token }}", merge_block)
        self.assertNotIn("GH_TOKEN: ${{ github.token }}", merge_block)

    def test_adoption_is_exact_revision_verified_and_idempotent(self):
        self.assertIn("--json state,mergedAt,headRefOid", self.adopt)
        self.assertIn("bound to commit", self.adopt)
        self.assertIn('data["status"] = "adopted"', self.adopt)
        self.assertIn('impl["authorized"] = True', self.adopt)
        self.assertIn("gh issue list --state all", self.adopt)
        self.assertIn("git diff --cached --quiet", self.adopt)

    def test_caller_template_has_reconciliation_dispatch(self):
        self.assertIn("options: [implement, plan, reconcile]", self.template)
        self.assertIn("plan_pr_number:", self.template)
        self.assertIn("inputs.action == 'reconcile'", self.template)


if __name__ == "__main__":
    unittest.main()
