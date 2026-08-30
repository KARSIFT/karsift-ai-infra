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
WORKFLOW_ROOT = ROOT / ".github/workflows"
RELEASE_WORKFLOW = (WORKFLOW_ROOT / "release.yml").read_text(encoding="utf-8")
MERGE_GATE_WORKFLOW = (WORKFLOW_ROOT / "merge-gate.yml").read_text(encoding="utf-8")
ALLOWED_ADMINISTRATION_MINTS = {
    (
        "release.yml",
        "Mint App installation token for production merge guard",
    ),
    (
        "merge-gate.yml",
        "Mint App installation token for production merge guard",
    ),
}


def app_token_mints(workflow_path: Path) -> list[tuple[str, str]]:
    """Return every named create-github-app-token step and its complete block."""

    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    mints: list[tuple[str, str]] = []
    for use_index, line in enumerate(lines):
        if "uses: actions/create-github-app-token@" not in line:
            continue
        step_index = use_index
        step_indent = -1
        step_name = ""
        while step_index >= 0:
            candidate = lines[step_index]
            stripped = candidate.lstrip()
            if stripped.startswith("- name: "):
                step_indent = len(candidate) - len(stripped)
                step_name = stripped.removeprefix("- name: ").strip()
                break
            if stripped.startswith("- uses: "):
                step_indent = len(candidate) - len(stripped)
                break
            step_index -= 1
        if step_indent < 0 or not step_name:
            raise AssertionError(f"unnamed App-token mint in {workflow_path}")
        end_index = step_index + 1
        while end_index < len(lines):
            candidate = lines[end_index]
            stripped = candidate.lstrip()
            if (
                len(candidate) - len(stripped) == step_indent
                and stripped.startswith("- ")
            ):
                break
            end_index += 1
        mints.append((step_name, "\n".join(lines[step_index:end_index])))
    return mints


def permission_inputs(block: str) -> set[str]:
    return {
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("permission-")
    }


def mint_block(workflow_path: Path, step_name: str) -> str:
    matches = [
        block
        for name, block in app_token_mints(workflow_path)
        if name == step_name
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {step_name!r} mint in {workflow_path}, got {len(matches)}"
        )
    return matches[0]


def step_names(workflow: str) -> list[str]:
    return [
        line.strip().removeprefix("- name: ")
        for line in workflow.splitlines()
        if line.strip().startswith("- name: ")
    ]


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


def run_verify_script(ruleset_payload: dict) -> subprocess.CompletedProcess[str]:
    effective_payload = effective()
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        gh_path = workspace / "gh"
        gh_path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "$*" == *rules/branches/* ]]; then\n'
            f"  printf '%s\\n' '{json.dumps(effective_payload, separators=(',', ':'))}'\n"
            "  exit 0\n"
            "fi\n"
            'if [[ "$*" == *rulesets/42 ]]; then\n'
            f"  printf '%s\\n' '{json.dumps(ruleset_payload, separators=(',', ':'))}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 99\n"
        )
        gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)
        environment = os.environ.copy()
        environment["PATH"] = f"{workspace}:{environment['PATH']}"
        return subprocess.run(
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

    def test_real_verifier_subprocess_covers_every_bypass_payload_shape(self):
        cases = (
            ("omitted", ruleset(omit_bypass=True), 1, "payload_incomplete"),
            ("non-array", ruleset(bypass={}), 1, "payload_incomplete"),
            ("empty", ruleset(), 0, "ok ruleset_id=42"),
            (
                "non-empty",
                ruleset(
                    bypass=[
                        {
                            "actor_id": 1,
                            "actor_type": "Integration",
                            "bypass_mode": "always",
                        }
                    ]
                ),
                1,
                "production_merge_guard_missing",
            ),
        )
        for name, payload, returncode, message in cases:
            with self.subTest(name=name):
                result = run_verify_script(payload)
                self.assertEqual(result.returncode, returncode, result.stderr)
                self.assertIn(message, result.stdout + result.stderr)
                if name in {"omitted", "non-array"}:
                    self.assertIn("operator_action=", result.stderr)

    def test_release_mutation_mint_has_exact_permissions_without_administration(self):
        mutation = mint_block(
            WORKFLOW_ROOT / "release.yml",
            "Mint App installation token for release mutation",
        )
        self.assertEqual(
            permission_inputs(mutation),
            {
                "permission-contents: write",
                "permission-issues: write",
                "permission-pull-requests: write",
            },
        )

    def test_merge_gate_mutation_mint_has_exact_permissions(self):
        mutation = mint_block(
            WORKFLOW_ROOT / "merge-gate.yml", "Mint App installation token"
        )
        self.assertEqual(
            permission_inputs(mutation),
            {
                "permission-contents: write",
                "permission-issues: write",
                "permission-pull-requests: write",
            },
        )

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
        self.assertEqual(
            [line for line in merge.splitlines() if "$GUARD_TOKEN" in line],
            [
                '              GH_TOKEN="$GUARD_TOKEN" bash karsift-ai-infra/config/verify-production-merge-guard.sh \\'
            ],
        )

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
        self.assertEqual(
            [line for line in merge.splitlines() if "$GUARD_TOKEN" in line],
            [
                '              GH_TOKEN="$GUARD_TOKEN" bash karsift-ai-infra/config/verify-production-merge-guard.sh \\'
            ],
        )

    def test_guard_mint_is_immediately_before_each_merge_decision(self):
        for workflow, merge_name in (
            (RELEASE_WORKFLOW, "Perform the single exact-head merge decision"),
            (MERGE_GATE_WORKFLOW, "Merge automatically"),
        ):
            with self.subTest(merge=merge_name):
                names = step_names(workflow)
                merge_index = names.index(merge_name)
                self.assertGreater(merge_index, 0)
                self.assertEqual(
                    names[merge_index - 1],
                    "Mint App installation token for production merge guard",
                )

    def test_only_two_named_guard_mints_request_administration(self):
        found_admin: set[tuple[str, str]] = set()
        workflow_paths = set(WORKFLOW_ROOT.rglob("*.yml")) | set(
            WORKFLOW_ROOT.rglob("*.yaml")
        )
        for workflow_path in sorted(workflow_paths):
            for step_name, block in app_token_mints(workflow_path):
                permissions = permission_inputs(block)
                identity = (workflow_path.name, step_name)
                if "permission-administration: write" not in permissions:
                    continue
                found_admin.add(identity)
                self.assertIn(identity, ALLOWED_ADMINISTRATION_MINTS)
                self.assertEqual(
                    permissions, {"permission-administration: write"}
                )
                self.assertIn("owner: ${{ github.repository_owner }}", block)
                self.assertIn(
                    "repositories: ${{ github.event.repository.name }}", block
                )
        self.assertEqual(found_admin, ALLOWED_ADMINISTRATION_MINTS)


if __name__ == "__main__":
    unittest.main()
