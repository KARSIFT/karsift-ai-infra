"""VOC-126 workflow_dispatch input-count regression."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_WORKFLOWS = ROOT / "templates/project-repo/.github/workflows"
MAX_DISPATCH_INPUTS = 25
INVALID_VOC125_TEMPLATE_SHA = "1f1705dbad41729563b0ad1e878e4154e5511e93"

PIPELINE_MUTATING_OPTIONS = (
    "implement, plan, reconcile, reconcile-release, reconcile-production-change, reconcile-live-evidence, "
    "recover-integration-push, recover-promotion-pr-checks"
)
PIPELINE_VERIFY_OPTIONS = (
    "verify-auto-advance-live-evidence, verify-ready-for-review-reuse, "
    "verify-remediate-operator-ownership, verify-promotion-check-recovery, "
    "verify-post-promotion-workflow"
)


def count_workflow_dispatch_inputs(text: str) -> int:
    in_inputs = False
    count = 0
    for line in text.splitlines():
        if re.match(r"^  workflow_dispatch:\s*$", line):
            in_inputs = False
            continue
        if re.match(r"^    inputs:\s*$", line):
            in_inputs = True
            continue
        if in_inputs and re.match(r"^    [A-Za-z]", line) and not line.startswith("      "):
            in_inputs = False
        if in_inputs and re.match(r"^      (\w+):", line):
            count += 1
    return count


class Voc126WorkflowDispatchInputLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline_template = (
            TEMPLATE_WORKFLOWS / "pipeline.yml"
        ).read_text(encoding="utf-8")
        cls.pipeline_verify_template = (
            TEMPLATE_WORKFLOWS / "pipeline-verify.yml"
        ).read_text(encoding="utf-8")

    def test_template_workflow_dispatch_blocks_stay_within_github_limit(self):
        for path in sorted(TEMPLATE_WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "workflow_dispatch:" not in text:
                continue
            count = count_workflow_dispatch_inputs(text)
            self.assertLessEqual(
                count,
                MAX_DISPATCH_INPUTS,
                f"{path.name} declares {count} workflow_dispatch inputs",
            )

    def test_template_pipeline_keeps_mutating_actions_only(self):
        self.assertIn(f"options: [{PIPELINE_MUTATING_OPTIONS}]", self.pipeline_template)
        for action in PIPELINE_VERIFY_OPTIONS.split(", "):
            self.assertNotIn(f"inputs.action == '{action}'", self.pipeline_template)

    def test_template_pipeline_verify_exposes_read_only_verifiers(self):
        self.assertIn(f"options: [{PIPELINE_VERIFY_OPTIONS}]", self.pipeline_verify_template)
        for job in (
            "verify-auto-advance-live-evidence",
            "verify-ready-for-review-reuse",
            "verify-remediate-operator-ownership",
            "verify-promotion-check-recovery",
            "verify-post-promotion-workflow",
        ):
            self.assertIn(f"  {job}:", self.pipeline_verify_template)
            self.assertIn(
                f"inputs.action == '{job}'",
                self.pipeline_verify_template,
            )

    def test_template_pipeline_exposes_existing_pr_number_only(self):
        dispatch = self.pipeline_template.split("workflow_dispatch:", 1)[1].split(
            "\n# A synchronize", 1
        )[0]
        self.assertIn("existing_pr_number:", dispatch)
        self.assertNotIn("expected_head_sha:", dispatch)
        self.assertNotIn("expected_base_sha:", dispatch)
        implement = self.pipeline_template.split("  implement:", 1)[1].split(
            "  plan:", 1
        )[0]
        self.assertIn("existing_pr_number: ${{ inputs.existing_pr_number }}", implement)

    def test_template_pipeline_verify_stays_read_only(self):
        self.assertNotIn("secrets: inherit", self.pipeline_verify_template)
        self.assertNotIn("actions: write", self.pipeline_verify_template)
        self.assertNotIn("create-github-app-token", self.pipeline_verify_template)
        self.assertIn("actions: read", self.pipeline_verify_template)

if __name__ == "__main__":
    unittest.main()
