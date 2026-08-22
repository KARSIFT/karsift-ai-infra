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
        self.assertIn("mergeCommit", self.adopt)
        self.assertIn('.parents[0].sha', self.adopt)
        self.assertIn('base_line="base_sha:', self.adopt)
        self.assertIn('index($base)', self.adopt)
        self.assertIn("bound to commit", self.adopt)
        self.assertIn('data["status"] = "adopted"', self.adopt)
        self.assertIn('impl["authorized"] = True', self.adopt)
        self.assertIn("gh issue list --state all", self.adopt)
        self.assertIn("git diff --cached --quiet", self.adopt)
        self.assertIn("/check-runs?per_page=100", self.adopt)
        self.assertIn('commits/$head_sha/status', self.adopt)
        self.assertNotIn('gh pr checks "${{ inputs.pr_number }}"', self.adopt)
        self.assertIn("checks: read", self.adopt)
        self.assertIn("statuses: read", self.adopt)
        self.assertIn('authoritative-checks-runner.py', self.adopt)
        self.assertIn('--exclude-prefix "adopt /"', self.adopt)
        self.assertIn('--paginate --slurp', self.adopt)
        self.assertIn("/tmp/adopt-authoritative.json", self.adopt)
        self.assertIn('data["adoption_independent_verification_evidence"]', self.adopt)
        self.assertIn('data["adoption_risk"]', self.adopt)
        self.assertIn('data["adoption_resolved_decisions"]', self.adopt)
        self.assertIn('data["adoption_deferred_decisions"]', self.adopt)
        self.assertIn('data["adoption_authority_provenance"]', self.adopt)
        self.assertIn("steps.root-dispatch.outputs.needed", self.adopt)
        self.assertIn("gh pr list --state all --head", self.adopt)
        root_dispatch = self.adopt.split(
            "- name: Determine whether the root task needs dispatch", 1
        )[1].split("  implement-first-task:", 1)[0]
        self.assertIn('issue_state" = "OPEN', root_dispatch)
        self.assertIn('pr_count" = "0', root_dispatch)
        self.assertNotIn("steps.commit.outputs.changed", root_dispatch)
        self.assertIn("git push -u --force-with-lease origin", self.adopt)
        self.assertNotIn("git push -u --force origin", self.adopt)

    def test_caller_template_has_reconciliation_dispatch(self):
        self.assertIn(
            "options: [implement, plan, reconcile, reconcile-release, reconcile-live-evidence, verify-auto-advance-live-evidence, verify-ready-for-review-reuse, verify-remediate-operator-ownership]",
            self.template,
        )
        self.assertIn("plan_pr_number:", self.template)
        self.assertIn("inputs.action == 'reconcile'", self.template)
        self.assertIn("inputs.action == 'reconcile-release'", self.template)

    def test_roster_pr_wait_uses_authoritative_exact_sha_checks(self):
        roster_wait = self.adopt.split("- name: Wait for roster PR checks", 1)[1].split(
            "- name: Merge checked roster PR", 1
        )[0]
        self.assertNotIn("statusCheckRollup", roster_wait)
        self.assertNotIn("gh pr checks", roster_wait)
        self.assertIn("/check-runs?per_page=100", roster_wait)
        self.assertIn("/status?per_page=100", roster_wait)
        self.assertIn("authoritative-checks-runner.py", roster_wait)
        self.assertIn('--workflow-event "pull_request"', roster_wait)
        self.assertIn("/tmp/roster-pr.json", roster_wait)
        self.assertIn("stable_green_count", roster_wait)
        self.assertIn("previous_head_sha", roster_wait)
        self.assertIn("pending_checks", roster_wait)
        self.assertIn("failed_checks", roster_wait)

    def test_checked_roster_merge_deletes_only_confirmed_exact_head(self):
        merge = self.adopt.split("- name: Merge checked roster PR and clean its exact head", 1)[1].split(
            "- name:", 1
        )[0]
        self.assertIn('gh pr merge "$PR_NUMBER" --merge --match-head-commit', merge)
        self.assertNotIn("gh pr merge \"$PR_NUMBER\" --merge --delete-branch", merge)
        self.assertIn('--match-head-commit "$CHECKED_HEAD_SHA"', merge)
        self.assertIn(".merged == true", merge)
        self.assertIn("$remote_sha", merge)
        self.assertIn('--force-with-lease="refs/heads/$CHECKED_HEAD_REF:$CHECKED_HEAD_SHA"', merge)

    def test_adoption_is_serialized_for_the_same_plan_authority(self):
        self.assertIn(
            "group: adopt-${{ github.repository }}-${{ inputs.pr_number }}", self.adopt
        )
        self.assertIn("cancel-in-progress: false", self.adopt)


if __name__ == "__main__":
    unittest.main()
