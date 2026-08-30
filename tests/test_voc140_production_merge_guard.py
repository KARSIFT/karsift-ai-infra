"""VOC-140 production merge guard token-visible payload and workflow isolation."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from production_merge_guard import (  # noqa: E402
    ProductionMergeGuardError,
    validate_production_merge_guard,
)


REPOSITORY = "KARSIFT/example"
RELEASE_WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
MERGE_GATE_WORKFLOW = (ROOT / ".github/workflows/merge-gate.yml").read_text(encoding="utf-8")


def effective(*, ruleset_id: int = 42):
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": "ci"}],
            },
            "ruleset_source_type": "Repository",
            "ruleset_source": REPOSITORY,
            "ruleset_id": ruleset_id,
        }
    ]


def ruleset(*, bypass=None, omit_bypass: bool = False):
    payload = {
        "id": 42,
        "target": "branch",
        "source_type": "Repository",
        "source": REPOSITORY,
        "enforcement": "active",
        "rules": [
            {"type": "pull_request", "parameters": {}},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "ci"}],
                },
            },
        ],
    }
    if not omit_bypass:
        payload["bypass_actors"] = [] if bypass is None else bypass
    return payload


class Voc140ProductionMergeGuardTests(unittest.TestCase):
    def test_omitted_bypass_actors_fails_payload_incomplete(self):
        with self.assertRaisesRegex(
            ProductionMergeGuardError, "production_merge_guard_payload_incomplete"
        ):
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(),
                rulesets=[ruleset(omit_bypass=True)],
            )

    def test_non_array_bypass_actors_fails_payload_incomplete(self):
        with self.assertRaisesRegex(
            ProductionMergeGuardError, "production_merge_guard_payload_incomplete"
        ):
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(),
                rulesets=[{**ruleset(), "bypass_actors": {}}],
            )

    def test_empty_bypass_array_still_passes(self):
        self.assertEqual(
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(),
                rulesets=[ruleset()],
            ),
            42,
        )

    def test_non_empty_bypass_still_fails_missing(self):
        with self.assertRaisesRegex(
            ProductionMergeGuardError, "production_merge_guard_missing"
        ):
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(),
                rulesets=[
                    ruleset(
                        bypass=[
                            {
                                "actor_id": 1,
                                "actor_type": "Integration",
                                "bypass_mode": "always",
                            }
                        ]
                    )
                ],
            )

    def test_verify_script_subprocess_with_mock_gh_omitted_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            gh_path = workspace / "gh"
            gh_path.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'if [[ "$*" == *rules/branches/* ]]; then\n'
                '  printf \'[{"type":"required_status_checks","ruleset_id":42,"ruleset_source_type":"Repository","ruleset_source":"KARSIFT/example","parameters":{"strict_required_status_checks_policy":true,"required_status_checks":[{"context":"ci"}]}}]\\n\'\n'
                "  exit 0\n"
                "fi\n"
                'if [[ "$*" == *rulesets/42 ]]; then\n'
                '  printf \'{"id":42,"target":"branch","source_type":"Repository","source":"KARSIFT/example","enforcement":"active","rules":[{"type":"pull_request","parameters":{}},{"type":"required_status_checks","parameters":{"strict_required_status_checks_policy":true,"required_status_checks":[{"context":"ci"}]}}]}\\n\'\n'
                "  exit 0\n"
                "fi\n"
                "exit 99\n"
            )
            gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)
            environment = os.environ.copy()
            environment["PATH"] = f"{workspace}:{environment['PATH']}"
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "config/verify-production-merge-guard.sh"),
                    REPOSITORY,
                    "main",
                    str(ROOT / "config"),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("production_merge_guard_payload_incomplete", result.stderr)
        self.assertIn("operator_action=", result.stderr)

    def test_release_mutation_mint_has_exact_permissions_without_administration(self):
        mutation = RELEASE_WORKFLOW.split("Mint App installation token for release mutation", 1)[1]
        mutation = mutation.split("Mint App installation token for production merge guard", 1)[0]
        self.assertIn("permission-contents: write", mutation)
        self.assertIn("permission-issues: write", mutation)
        self.assertIn("permission-pull-requests: write", mutation)
        self.assertNotIn("permission-administration: write", mutation)

    def test_release_guard_mint_is_repository_scoped_administration_only(self):
        guard = RELEASE_WORKFLOW.split(
            "Mint App installation token for production merge guard", 1
        )[1].split("Validate the full roster", 1)[0]
        self.assertIn("owner: ${{ github.repository_owner }}", guard)
        self.assertIn("repositories: ${{ github.event.repository.name }}", guard)
        self.assertIn("permission-administration: write", guard)
        self.assertNotIn("permission-contents: write", guard)
        self.assertNotIn("permission-issues: write", guard)
        self.assertNotIn("permission-pull-requests: write", guard)

    def test_guard_token_not_used_for_merge_or_mutations(self):
        merge = RELEASE_WORKFLOW.split("Perform the single exact-head merge decision", 1)[1]
        merge = merge.split("Synchronize integration to the exact promotion merge", 1)[0]
        self.assertIn("GUARD_TOKEN", merge)
        self.assertIn('GH_TOKEN="$GUARD_TOKEN" bash karsift-ai-infra/config/verify-production-merge-guard.sh', merge)
        self.assertIn('GH_TOKEN="$MUTATION_TOKEN" gh pr merge', merge)
        self.assertNotIn('GH_TOKEN="$GUARD_TOKEN" gh pr merge', merge)

    def test_merge_gate_production_path_uses_two_token_contract(self):
        guard = MERGE_GATE_WORKFLOW.split(
            "Mint App installation token for production merge guard", 1
        )[1].split("Publish immutable reuse transition attestation", 1)[0]
        merge = MERGE_GATE_WORKFLOW.split("Merge automatically", 1)[1].split(
            "Publish task completion marker", 1
        )[0]
        self.assertIn("permission-administration: write", guard)
        self.assertIn('GH_TOKEN="$GUARD_TOKEN" bash karsift-ai-infra/config/verify-production-merge-guard.sh', merge)
        self.assertIn('GH_TOKEN="$MUTATION_TOKEN" gh pr merge', merge)


if __name__ == "__main__":
    unittest.main()
