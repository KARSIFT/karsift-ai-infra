"""Deterministic VOC-124 publisher token-permission and carrier-text tests."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/implement.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def caller_publish_job(workflow: str) -> str:
    _, remainder = workflow.split("\n  publish:", 1)
    publish_job, _ = remainder.split("\n  publish-source:", 1)
    return publish_job


def publish_source_job(workflow: str) -> str:
    return workflow[workflow.index("\n  publish-source:") :]


def infrastructure_mint_block(source_publisher: str) -> str:
    marker = "- name: Mint least-privilege App token for infrastructure repository"
    start = source_publisher.index(marker)
    uses_index = source_publisher.index("uses: actions/create-github-app-token@", start)
    block_end = source_publisher.find("\n\n", uses_index)
    if block_end == -1:
        block_end = len(source_publisher)
    return source_publisher[start:block_end]


def caller_mint_block(publish_job: str) -> str:
    marker = "- name: Mint least-privilege App token on the clean runner"
    start = publish_job.index(marker)
    uses_index = publish_job.index("uses: actions/create-github-app-token@", start)
    block_end = publish_job.find("\n\n", uses_index)
    if block_end == -1:
        block_end = len(publish_job)
    return publish_job[start:block_end]


class Voc124WorkflowPermissionTests(unittest.TestCase):
    def test_publish_source_mint_requests_workflows_write(self):
        mint = infrastructure_mint_block(publish_source_job(WORKFLOW))
        self.assertIn("permission-workflows: write", mint)
        self.assertIn("permission-contents: write", mint)
        self.assertIn("permission-issues: write", mint)
        self.assertIn("permission-pull-requests: write", mint)
        self.assertIn("repositories: karsift-ai-infra", mint)

    def test_caller_publish_mint_omits_workflows_write(self):
        mint = caller_mint_block(caller_publish_job(WORKFLOW))
        self.assertNotIn("permission-workflows: write", mint)
        self.assertIn("permission-contents: write", mint)
        self.assertIn("permission-issues: write", mint)
        self.assertIn("permission-pull-requests: write", mint)

    def test_caller_publish_still_rejects_workflow_files(self):
        publish_job = caller_publish_job(WORKFLOW)
        self.assertIn("cannot publish workflow-file changes", publish_job)
        self.assertIn(r"grep -E '^\.github/workflows/'", publish_job)

    def test_publish_source_script_has_no_caller_workflow_file_rejection(self):
        source_publisher = publish_source_job(WORKFLOW)
        publish_step = source_publisher.split(
            "- name: Publish exact infrastructure bundle from an isolated bare repository",
            1,
        )[1].split("- name: Open or update infrastructure PR", 1)[0]
        self.assertNotIn("cannot publish workflow-file changes", publish_step)
        self.assertNotIn(r"grep -E '^\.github/workflows/'", publish_step)

    def test_caller_publish_pr_body_no_longer_claims_human_approval_gate(self):
        publish_job = caller_publish_job(WORKFLOW)
        self.assertNotIn("required human approval are still pending", publish_job)
        self.assertIn("Independent", publish_job)
        self.assertIn("exact-revision review is still pending", publish_job)
        self.assertIn("not authorized to", publish_job)
        self.assertIn("merge on its own", publish_job)

    def test_readme_describes_infrastructure_workflows_write_without_caller_permission(self):
        self.assertIn("workflows: write", README)
        self.assertIn("caller `publish` token still", README)
        self.assertIn("omits `workflows: write`", README)
        self.assertIn("still rejects every caller", README)

    def test_historical_changelog_caller_no_workflows_entry_is_preserved(self):
        self.assertIn("has no workflows permission", CHANGELOG)


if __name__ == "__main__":
    unittest.main()
