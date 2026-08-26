from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ownership = load("remediation_ownership", ROOT / "config/remediation-ownership.py")
decision = load("decide_remediation", ROOT / "config/decide-remediation.py")
verifier_runner = load(
    "verify_remediate_operator_ownership_runner",
    ROOT / "config/verify-remediate-operator-ownership-runner.py",
)
verifier = load(
    "verify_remediate_operator_ownership",
    ROOT / "config/verify_remediate_operator_ownership.py",
)


def contract(task_id: str, owner: str = "operator") -> str:
    return f"""schema_version: 1
task_id: {task_id}
ownership: {owner}
workflow_file: pipeline.yml
job_names:
  - verify-remediation-ownership / verify
events:
  - workflow_dispatch
branch: agent/voc-106-voc-106-t01
sha_lineage:
  mode: exact_pr_head
conclusion: success
"""


class RemediationOwnershipTests(unittest.TestCase):
    def make_package(self, root: Path, tasks: str) -> Path:
        package = root / "specs/changes/VOC-106-example"
        package.mkdir(parents=True)
        (package / "tasks.md").write_text(tasks, encoding="utf-8")
        return package

    def test_caller_template_exposes_read_only_remediation_verifier(self):
        template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline-verify.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "verify-post-promotion-workflow]",
            template,
        )
        self.assertIn("  verify-remediate-operator-ownership:", template)
        verifier_block = template.split(
            "  verify-remediate-operator-ownership:", 1
        )[1].split("\n  verify-promotion-check-recovery:", 1)[0]
        self.assertIn("actions: read", verifier_block)
        self.assertIn("contents: read", verifier_block)
        self.assertIn("issues: read", verifier_block)
        self.assertIn("pull-requests: read", verifier_block)

    def test_ordinary_task_keeps_bounded_retry(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            self.make_package(root, "## VOC-106-T00 — ordinary\n")
            result = ownership.classify(root, "specs/changes/VOC-106-example", "VOC-106-T00")
            self.assertEqual(result.decision, "implement")
            self.assertEqual(
                decision.decide(
                    expected_sha="a", current_sha="a", review_state="FAIL",
                    ci_failed=False, review_job_failed=False, ownership_state="ORDINARY",
                ),
                "RETRY",
            )

    def test_valid_operator_contract_suppresses_review_and_ci_retry(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            package = self.make_package(
                root,
                "## VOC-106-T01 — proof\n\n- Automation ownership: operator\n",
            )
            contracts = package / ".karsift/live-evidence"
            contracts.mkdir(parents=True)
            (contracts / "VOC-106-T01.yaml").write_text(contract("VOC-106-T01"), encoding="utf-8")
            result = ownership.classify(root, "specs/changes/VOC-106-example", "VOC-106-T01")
            self.assertEqual(result.decision, "prepare-live-evidence")
            for ci_failed, review_state in ((False, "FAIL"), (True, "PENDING")):
                self.assertEqual(
                    decision.decide(
                        expected_sha="a", current_sha="a", review_state=review_state,
                        ci_failed=ci_failed, review_job_failed=False, ownership_state="OPERATOR",
                    ),
                    "ESCALATE_OPERATOR",
                )

    def test_malformed_or_conflicting_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            package = self.make_package(
                root,
                "## VOC-106-T01 — proof\n\n- Automation ownership: operator\n",
            )
            contracts = package / ".karsift/live-evidence"
            contracts.mkdir(parents=True)
            (contracts / "VOC-106-T01.yaml").write_text(
                contract("VOC-106-T01", "live-actions"), encoding="utf-8"
            )
            result = ownership.classify(root, "specs/changes/VOC-106-example", "VOC-106-T01")
            self.assertEqual(result.decision, "fail-closed")
            self.assertEqual(result.reason, "marker_contract_conflict")
            self.assertEqual(
                decision.decide(
                    expected_sha="a", current_sha="a", review_state="FAIL",
                    ci_failed=False, review_job_failed=False, ownership_state="FAIL_CLOSED",
                ),
                "ESCALATE_OPERATOR",
            )

    def test_path_escape_and_missing_tasks_fail_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            self.assertEqual(ownership.classify(root, "../outside", "VOC-106-T01").reason, "invalid_package_path")
            self.assertEqual(
                ownership.classify(root, "specs/changes/missing", "VOC-106-T01").reason,
                "missing_tasks_file",
            )

    def test_stale_waiting_and_review_infrastructure_semantics_remain(self):
        self.assertEqual(
            decision.decide(
                expected_sha="old", current_sha="new", review_state="FAIL",
                ci_failed=True, review_job_failed=False, ownership_state="ORDINARY",
            ),
            "STALE",
        )
        self.assertEqual(
            decision.decide(
                expected_sha="a", current_sha="a", review_state="WAITING",
                ci_failed=False, review_job_failed=False, ownership_state="OPERATOR",
            ),
            "WAITING",
        )
        self.assertEqual(
            decision.decide(
                expected_sha="a", current_sha="a", review_state="PENDING",
                ci_failed=False, review_job_failed=True, ownership_state="FAIL_CLOSED",
            ),
            "REVIEW_INFRA_FAILURE",
        )

    def test_hosted_verifier_extracts_base_sha_without_crashing(self):
        base_sha = "b" * 40
        valid = {
            "pull_requests": [
                {"number": 7, "head": {"sha": "a" * 40}, "base": {"sha": base_sha}}
            ]
        }
        self.assertEqual(verifier_runner.associated_base_sha(valid), base_sha)
        for malformed in (
            {},
            {"pull_requests": []},
            {"pull_requests": [None]},
            {"pull_requests": [{"base": None}]},
        ):
            self.assertEqual(verifier_runner.associated_base_sha(malformed), "")

    def test_authority_issue_line_accepts_carrier_punctuation_without_ambiguity(self):
        pattern = r"^Closes #([1-9][0-9]*)\.?$"
        self.assertEqual(re.findall(pattern, "Closes #885.", re.MULTILINE), ["885"])
        self.assertEqual(re.findall(pattern, "Closes #885", re.MULTILINE), ["885"])
        self.assertEqual(re.findall(pattern, "Closes #0.", re.MULTILINE), [])
        self.assertEqual(re.findall(pattern, "Closes #885 extra", re.MULTILINE), [])

    def test_hosted_verifier_binds_source_to_later_carrier_head(self):
        source = "a" * 40
        carrier = "c" * 40
        run = {
            "head_sha": source,
            "repository": {"full_name": "KARSIFT/example"},
            "name": "pipeline",
            "path": ".github/workflows/pipeline.yml",
            "event": "pull_request",
            "status": "completed",
            "pull_requests": [
                {
                    "number": 7,
                    "head": {"sha": carrier},
                    "base": {"sha": "b" * 40},
                }
            ],
        }
        self.assertEqual(verifier_runner.immutable_run_head_sha(run), source)
        self.assertTrue(
            verifier.verify_source_run(
                run=run,
                repository="KARSIFT/example",
                pr_number=7,
                expected_head_sha=source,
                expected_base_sha="b" * 40,
            ).ok
        )
        self.assertEqual(
            verifier_runner.evidence_path_for_task(Path("/tmp/pkg"), "VOC-106-T01"),
            Path("/tmp/pkg/t01-evidence.md"),
        )
        comparison = {
            "status": "ahead",
            "merge_base_commit": {"sha": source},
            "base_commit": {"sha": source},
            "commits": [{"sha": carrier}],
        }
        self.assertTrue(
            verifier.verify_source_to_carrier_lineage(
                comparison=comparison,
                source_head_sha=source,
                carrier_head_sha=carrier,
            ).ok
        )
        self.assertFalse(
            verifier.verify_source_to_carrier_lineage(
                comparison=comparison,
                source_head_sha=carrier,
                carrier_head_sha=carrier,
            ).ok
        )

        evidence = "\n".join(
            [
                "gate_status: source-proof-complete",
                "source_run_id: `123`",
                f"source_head_sha: `{source}`",
                "source_pipeline_conclusion: `success`",
                "should_retry: `false`",
                "implementer_job: `skipped`",
                "operator_escalation_marker: `present`",
                "ordinary_retry_fixture: `passed`",
            ]
        )
        self.assertTrue(
            verifier.verify_source_evidence(
                evidence,
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )
        self.assertFalse(
            verifier.verify_source_evidence(
                evidence.replace("should_retry: `false`", "should_retry: `true`"),
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )

        marker = {
            "author": {"login": "github-actions"},
            "body": "\n".join(
                [
                    f"{verifier.OPERATOR_ESCALATION_MARKER_PREFIX} `VOC-106-T01`",
                    "should_retry: `false`",
                    "task_id: `VOC-106-T01`",
                    "package_path: `specs/changes/VOC-106-example`",
                    "pr_number: `7`",
                    "run_id: `123`",
                    f"head_sha: `{source}`",
                ]
            ),
        }
        self.assertTrue(
            verifier.verify_escalation_marker(
                [marker],
                task_id="VOC-106-T01",
                package_path="specs/changes/VOC-106-example",
                pr_number=7,
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )
        self.assertFalse(
            verifier.verify_escalation_marker(
                [{**marker, "author": {"login": "untrusted-user"}}],
                task_id="VOC-106-T01",
                package_path="specs/changes/VOC-106-example",
                pr_number=7,
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )
        later_marker = {
            **marker,
            "body": marker["body"]
            .replace("run_id: `123`", "run_id: `124`")
            .replace(source, carrier),
        }
        self.assertTrue(
            verifier.verify_escalation_marker(
                [marker, later_marker],
                task_id="VOC-106-T01",
                package_path="specs/changes/VOC-106-example",
                pr_number=7,
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )
        self.assertFalse(
            verifier.verify_escalation_marker(
                [marker, dict(marker)],
                task_id="VOC-106-T01",
                package_path="specs/changes/VOC-106-example",
                pr_number=7,
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )
        self.assertFalse(
            verifier.verify_escalation_marker(
                [later_marker],
                task_id="VOC-106-T01",
                package_path="specs/changes/VOC-106-example",
                pr_number=7,
                source_run_id=123,
                source_head_sha=source,
            ).ok
        )

    def test_source_run_policy_rejects_malformed_associations_cleanly(self):
        common = {
            "repository": {"full_name": "KARSIFT/example"},
            "name": "pipeline",
            "path": ".github/workflows/pipeline.yml",
            "event": "pull_request",
            "status": "completed",
            "head_sha": "a" * 40,
        }
        for source_pr in (None, {"number": 7, "head": None, "base": None}):
            result = verifier.verify_source_run(
                run={**common, "pull_requests": [source_pr]},
                repository="KARSIFT/example",
                pr_number=7,
                expected_head_sha="a" * 40,
                expected_base_sha="b" * 40,
            )
            self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
