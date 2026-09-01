"""VOC-117 role bindings and parameterized Cursor routing regressions."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
if str(CONFIG) not in sys.path:
    sys.path.insert(0, str(CONFIG))

from prepare_cursor_model import (  # noqa: E402
    CursorModelError,
    prepare_cursor_model,
)


VOC117_BINDINGS = {
    "implementer": "cursor/composer-2.5",
    "implementer_escalation": "cursor/composer-2.5",
    "planner": "cursor/grok-4.6[effort=high,fast=false]",
    "reviewer": "cursor/grok-4.6[effort=high,fast=false]",
    "reviewer_fast_retry": "cursor/grok-4.6[effort=high,fast=false]",
    "plan_reviewer": "cursor/grok-4.6[effort=high,fast=false]",
}


def active_role(config: str, role: str) -> str:
    matches = re.findall(rf"^{re.escape(role)}:\s*(\S+)\s*$", config, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError(f"expected one active {role} binding, found {matches}")
    return matches[0]


class Voc117RoleBindingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = (CONFIG / "roles.yml").read_text(encoding="utf-8")
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text(
            encoding="utf-8"
        )
        cls.plan = (ROOT / ".github/workflows/plan.yml").read_text(encoding="utf-8")
        cls.review = (ROOT / ".github/workflows/review.yml").read_text(
            encoding="utf-8"
        )
        cls.plan_review = (ROOT / ".github/workflows/plan-review.yml").read_text(
            encoding="utf-8"
        )
        cls.remediate = (ROOT / ".github/workflows/remediate.yml").read_text(
            encoding="utf-8"
        )
        cls.merge_gate = (ROOT / ".github/workflows/merge-gate.yml").read_text(
            encoding="utf-8"
        )
        cls.recovery = (CONFIG / "actions_check_recovery.py").read_text(
            encoding="utf-8"
        )

    def test_voc117_test_00_has_six_exact_role_bindings(self):
        for role, expected in VOC117_BINDINGS.items():
            with self.subTest(role=role):
                self.assertEqual(active_role(self.roles, role), expected)

        active_values = [
            line.split(":", 1)[1].strip()
            for line in self.roles.splitlines()
            if line and not line.startswith("#") and ":" in line
        ]
        self.assertTrue(active_values)
        self.assertTrue(all(value.startswith("cursor/") for value in active_values))

    def test_voc117_tests_01_and_02_preserve_parameterized_models(self):
        self.assertEqual(
            prepare_cursor_model(VOC117_BINDINGS["planner"]),
            "grok-4.6[effort=high,fast=false]",
        )
        self.assertEqual(
            prepare_cursor_model(VOC117_BINDINGS["plan_reviewer"]),
            "grok-4.6[effort=high,fast=false]",
        )
        for role in ("reviewer", "reviewer_fast_retry"):
            self.assertEqual(
                prepare_cursor_model(VOC117_BINDINGS[role]),
                "grok-4.6[effort=high,fast=false]",
            )
        for role in ("implementer", "implementer_escalation"):
            self.assertEqual(
                prepare_cursor_model(VOC117_BINDINGS[role]),
                "composer-2.5",
            )

    def test_voc117_tests_01_and_02_wire_every_cursor_workflow(self):
        for workflow in (self.implement, self.plan, self.review, self.plan_review):
            with self.subTest(workflow=workflow[:30]):
                self.assertIn("prepare_cursor_model.py", workflow)
                self.assertIn("--require-api-key", workflow)
                self.assertIn("if ! MODEL=$(python3", workflow)
                self.assertNotIn('${MODEL#cursor/}', workflow)

    def test_voc117_test_03_missing_api_key_fails_closed_without_value(self):
        env = os.environ.copy()
        env.pop("CURSOR_API_KEY", None)
        sentinel = "must-not-appear"
        env["UNRELATED_SENTINEL"] = sentinel
        result = subprocess.run(
            [
                sys.executable,
                str(CONFIG / "prepare_cursor_model.py"),
                "--require-api-key",
                VOC117_BINDINGS["planner"],
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing_cursor_api_key", result.stderr)
        self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_voc117_test_03_rejects_unsupported_or_invalid_configuration(self):
        invalid = (
            "openai/codex-action",
            "grok-4.6[fast=false]",
            "cursor/",
            "cursor/grok-4.6[fast=false",
            "cursor/grok-4.6[fast=false,fast=true]",
            "cursor/grok-4.6[fast=]",
            "cursor/grok-4.6",
            "cursor/grok-4.6[fast=false]",
        )
        for stored in invalid:
            with self.subTest(stored=stored), self.assertRaises(CursorModelError):
                prepare_cursor_model(stored)
        self.assertIn("Unsupported implementer provider prefix", self.implement)

    def test_voc117_test_04_current_state_is_not_obsolete_routing(self):
        self.assertIn("VOC-117", self.roles)
        self.assertIn("Historical provider/model transitions", self.roles)
        self.assertNotIn("cursor/auto", self.roles)
        self.assertNotIn("cursor-grok-4.5", self.roles)
        self.assertNotRegex(
            self.roles,
            r"(?i)(current|active).*openai|openai.*(current|active)",
        )

    def test_voc117_test_05_safety_controls_remain_explicit(self):
        self.assertIn("expected_head_sha:", self.review)
        self.assertIn('if [ "$actual_head" != "$EXPECTED_HEAD_SHA" ]; then', self.review)
        self.assertIn("expected_head_sha:", self.merge_gate)
        self.assertIn("Risk classification:", self.merge_gate)
        for context in ("governance-policy", "validate", "ci / ci"):
            self.assertIn(context, self.recovery)
        self.assertIn('if [ "$next_attempt" -gt 2 ]; then', self.remediate)
        self.assertIn("Stopping - not retrying automatically", self.remediate)
        self.assertIn("no attempt 3", self.implement)


if __name__ == "__main__":
    unittest.main()
