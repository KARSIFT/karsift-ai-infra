from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


class LifecycleWorkflowPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adopt = (ROOT / ".github/workflows/adopt.yml").read_text()
        cls.merge = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.advance = (ROOT / ".github/workflows/auto-advance.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.prompt = (ROOT / "prompts/implement.md").read_text()
        cls.plan_prompt = (ROOT / "prompts/plan.md").read_text()
        cls.readme = (ROOT / "README.md").read_text()
        cls.template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def test_checkout_uses_verified_node24_sha_without_persisted_credentials(self):
        checkout_steps = []
        workflow_paths = sorted(
            path
            for path in (ROOT / ".github/workflows").iterdir()
            if path.suffix in {".yml", ".yaml"}
        )
        for workflow_path in workflow_paths:
            lines = workflow_path.read_text().splitlines()
            for line_index, line in enumerate(lines):
                match = re.search(r"uses: actions/checkout@([^\s]+)", line)
                if not match:
                    continue

                step_start = line_index
                step_indent = None
                for candidate in range(line_index, -1, -1):
                    step_match = re.match(r"^(\s*)-\s+", lines[candidate])
                    if step_match and len(step_match.group(1)) < len(line) - len(
                        line.lstrip()
                    ):
                        step_start = candidate
                        step_indent = step_match.group(1)
                        break
                    if candidate == line_index and step_match:
                        step_indent = step_match.group(1)
                        break
                self.assertIsNotNone(
                    step_indent, f"cannot locate checkout step in {workflow_path}"
                )

                step_end = len(lines)
                for candidate in range(step_start + 1, len(lines)):
                    if re.match(rf"^{re.escape(step_indent)}-\s+", lines[candidate]):
                        step_end = candidate
                        break
                checkout_steps.append(
                    (
                        workflow_path,
                        match.group(1),
                        "\n".join(lines[step_start:step_end]),
                    )
                )

        self.assertGreater(len(checkout_steps), 0)
        for workflow_path, revision, step in checkout_steps:
            self.assertEqual(revision, CHECKOUT_SHA, str(workflow_path))
            self.assertIn("persist-credentials: false", step, str(workflow_path))
            self.assertNotIn("allow-unsafe-pr-checkout", step, str(workflow_path))

    def test_authoritative_selector_is_used_by_adopt_merge_and_release(self):
        release = (ROOT / ".github/workflows/release.yml").read_text()
        for workflow in (self.adopt, self.merge, release):
            self.assertIn("authoritative-checks-runner.py", workflow)
            self.assertIn("--paginate --slurp", workflow)
            self.assertIn("--pull-request-file", workflow)

    def test_marker_is_published_after_merge_and_shared_by_consumers(self):
        merge_command = self.merge.index('gh pr merge "$PR_NUMBER"')
        publish_command = self.merge.index("task-completion-runner.py publish")
        self.assertLess(merge_command, publish_command)
        self.assertIn("task-completion-runner.py validate-task", self.advance)
        self.assertIn(
            "if ! python3 karsift-ai-infra/config/task-completion-runner.py validate-task",
            self.advance,
        )
        self.assertIn("safe no-op", self.advance)

    def test_ephemeral_plan_task_and_roster_merges_delete_head_branches(self):
        self.assertIn(
            'gh pr merge "$PR_NUMBER" --squash --delete-branch', self.merge
        )
        self.assertIn(
            '--force-with-lease="refs/heads/$CHECKED_HEAD_REF:$CHECKED_HEAD_SHA"',
            self.adopt,
        )
        self.assertIn("remaining_sha=$(git ls-remote --heads origin", self.adopt)
        self.assertIn("Leased roster branch deletion failed and the ref still exists", self.adopt)
        self.assertIn("Recover cleanup for an already-merged roster", self.adopt)
        self.assertIn("Current roster ref is not uniquely proven as an already-merged exact head", self.adopt)

    def test_post_merge_task_marker_is_task_branch_scoped(self):
        marker = "- name: Publish task completion marker and close linked task issue"
        marker_block = self.merge.split(marker, 1)[1].split("- name:", 1)[0]
        self.assertIn(
            "if: startsWith(github.event.pull_request.head.ref, 'agent/')",
            marker_block,
        )
        self.assertNotIn("github.event.pull_request.body", marker_block)
        self.assertNotIn("PR_BODY", marker_block)
        for stale_argument in ("--package-path", "--task-id", "--issue-number"):
            self.assertNotIn(stale_argument, marker_block)
        self.assertIn("--reviewed-base-sha", marker_block)
        self.assertIn("fetches the live merged PR body", self.merge)

    def test_local_closing_binding_and_cross_repo_policy_both_remain(self):
        self.assertIn("Closes #${{ inputs.issue_number }}", self.implement)
        self.assertIn("Relates to OWNER/CALLER#N", self.prompt)
        self.assertIn("reject_foreign_repository_closing_text", self.merge)
        for keyword in ("close", "fix", "resolve"):
            self.assertIn(keyword, self.prompt)

    def test_authority_docs_treat_issue_close_as_a_wake_hint_only(self):
        combined = "\n".join(
            (self.readme, self.plan_prompt, self.template, self.advance, self.adopt)
        )
        for stale in (
            "roster closes",
            "issue closing is what triggers promotion",
            "release-approval issue",
            "release promotion (one human decision",
        ):
            self.assertNotIn(stale, combined)
        self.assertIn("closed state alone cannot advance", combined)
        self.assertIn("App-authored completion marker", combined)
        self.assertNotIn("check-completion", self.advance)
        self.assertIn("serialized convergence evaluator", self.advance)


if __name__ == "__main__":
    unittest.main()
