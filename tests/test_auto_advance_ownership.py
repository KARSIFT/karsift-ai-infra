from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPOSITORY_ROOT / "config"


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = load_module("live_evidence_reconcile", CONFIG / "live_evidence_reconcile.py")
ownership = load_module("auto_advance_ownership", CONFIG / "auto_advance_ownership.py")
verifier = load_module(
    "verify_auto_advance_live_evidence",
    CONFIG / "verify_auto_advance_live_evidence.py",
)
publisher = load_module(
    "auto_advance_carrier_publisher",
    CONFIG / "auto-advance-carrier-publisher.py",
)


PACKAGE = "specs/changes/VOC-102-auto-advance-dispatches-implementer-for-operator"


def contract_yaml(task_id: str, ownership_value: str = "operator") -> str:
    return textwrap.dedent(
        f"""\
        schema_version: 1
        task_id: {task_id}
        ownership: {ownership_value}
        workflow_file: pipeline.yml
        job_names:
          - verify-auto-advance-live-evidence / verify
        events:
          - workflow_dispatch
        branch: agent/voc-102-voc-102-t01
        sha_lineage:
          mode: exact_pr_head
        conclusion: success
        """
    )


class AutoAdvanceOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auto_advance = (
            REPOSITORY_ROOT / ".github/workflows/auto-advance.yml"
        ).read_text()
        cls.verify_workflow = (
            REPOSITORY_ROOT / ".github/workflows/verify-auto-advance-live-evidence.yml"
        ).read_text()

    def test_voc102_test_00_contract_path_is_authoritative(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            (package / "tasks.md").write_text("# tasks\n", encoding="utf-8")
            contract_dir = package / ".karsift/live-evidence"
            contract_dir.mkdir(parents=True)
            (contract_dir / "VOC-102-T01.yaml").write_text(
                contract_yaml("VOC-102-T01"),
                encoding="utf-8",
            )
            result = ownership.classify_next_task(str(package), "VOC-102-T01", "")
            self.assertEqual(result.decision, "prepare-live-evidence")
            self.assertEqual(result.ownership, "operator")

    def test_voc102_test_01_operator_and_live_actions_recognized(self):
        for value in ("operator", "live-actions"):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as scratch:
                    package = Path(scratch) / "pkg"
                    package.mkdir()
                    contract_dir = package / ".karsift/live-evidence"
                    contract_dir.mkdir(parents=True)
                    (contract_dir / "VOC-099-T01.yaml").write_text(
                        contract_yaml("VOC-099-T01", value),
                        encoding="utf-8",
                    )
                    result = ownership.classify_next_task(str(package), "VOC-099-T01", "")
                    self.assertEqual(result.decision, "prepare-live-evidence")
                    self.assertEqual(result.ownership, value)

    def test_voc102_test_02_operator_next_task_does_not_dispatch(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            contract_dir = package / ".karsift/live-evidence"
            contract_dir.mkdir(parents=True)
            (contract_dir / "VOC-102-T01.yaml").write_text(
                contract_yaml("VOC-102-T01"),
                encoding="utf-8",
            )
            result = ownership.classify_next_task(str(package), "VOC-102-T01", "")
            self.assertEqual(result.decision, "prepare-live-evidence")
            self.assertNotEqual(result.decision, "implement")

    def test_voc102_test_03_ordinary_next_task_dispatches(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            (package / "tasks.md").write_text(
                textwrap.dedent(
                    """\
                    ## VOC-000-T01 — ordinary task

                    - Status: pending
                    """
                ),
                encoding="utf-8",
            )
            result = ownership.classify_next_task(str(package), "VOC-000-T01", "")
            self.assertEqual(result.decision, "implement")

    def test_voc102_test_04_malformed_contract_fail_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            contract_dir = package / ".karsift/live-evidence"
            contract_dir.mkdir(parents=True)
            (contract_dir / "VOC-102-T01.yaml").write_text(":\n  bad\n", encoding="utf-8")
            result = ownership.classify_next_task(str(package), "VOC-102-T01", "")
            self.assertEqual(result.decision, "fail-closed")

            (contract_dir / "VOC-102-T01.yaml").write_text(
                contract_yaml("VOC-102-T01") + "evidence_path: t01-evidence.md\n",
                encoding="utf-8",
            )
            invalid_schema = ownership.classify_next_task(
                str(package), "VOC-102-T01", ""
            )
            self.assertEqual(invalid_schema.decision, "fail-closed")

    def test_voc102_test_05_missing_or_unrecognized_ownership_fail_closed(self):
        cases = [
            ("schema_version: 1\ntask_id: VOC-102-T01\n", "missing ownership"),
            (
                contract_yaml("VOC-102-T01").replace("ownership: operator", "ownership: bot"),
                "unrecognized ownership",
            ),
        ]
        for body, label in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as scratch:
                    package = Path(scratch) / "pkg"
                    package.mkdir()
                    contract_dir = package / ".karsift/live-evidence"
                    contract_dir.mkdir(parents=True)
                    (contract_dir / "VOC-102-T01.yaml").write_text(body, encoding="utf-8")
                    result = ownership.classify_next_task(str(package), "VOC-102-T01", "")
                    self.assertEqual(result.decision, "fail-closed")

    def test_voc102_test_06_contradictory_metadata_fail_closed(self):
        tasks_md = textwrap.dedent(
            """\
            ## VOC-102-T01 — operator task

            - Automation ownership: operator
            """
        )
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            (package / "tasks.md").write_text(tasks_md, encoding="utf-8")

            marker_only = ownership.classify_next_task(str(package), "VOC-102-T01", tasks_md)
            self.assertEqual(marker_only.decision, "fail-closed")
            self.assertEqual(marker_only.reason, "marker_without_contract")

            contract_dir = package / ".karsift/live-evidence"
            contract_dir.mkdir(parents=True)
            (contract_dir / "VOC-102-T01.yaml").write_text(
                contract_yaml("VOC-102-T01", "live-actions"),
                encoding="utf-8",
            )
            conflict = ownership.classify_next_task(str(package), "VOC-102-T01", tasks_md)
            self.assertEqual(conflict.decision, "fail-closed")
            self.assertEqual(conflict.reason, "marker_contract_conflict")

            duplicate_tasks = tasks_md + "\n- Automation ownership: live-actions\n"
            duplicate = ownership.classify_next_task(str(package), "VOC-102-T01", duplicate_tasks)
            self.assertEqual(duplicate.decision, "fail-closed")
            self.assertEqual(duplicate.reason, "duplicate_automation_marker")

            prose = tasks_md + "\nNarrative mentions operator ownership in prose only.\n"
            ordinary = ownership.classify_next_task(str(package), "VOC-000-T00", prose)
            self.assertEqual(ordinary.decision, "implement")

    def test_voc102_test_06_invalid_marker_and_structural_heading_fail_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            invalid = ownership.classify_next_task(
                str(package),
                "VOC-102-T01",
                "## VOC-102-T01 — target\n\n- Automation ownership: robot\n",
            )
            self.assertEqual(invalid.reason, "invalid_automation_marker")

            duplicate_stanza = ownership.classify_next_task(
                str(package),
                "VOC-102-T01",
                "## VOC-102-T01\n\n## VOC-102-T01 — duplicate\n",
            )
            self.assertEqual(duplicate_stanza.reason, "duplicate_task_stanza")

            prefix_only = ownership.parse_automation_ownership_markers(
                "## VOC-102-T01a — other\n\n- Automation ownership: operator\n",
                "VOC-102-T01",
            )
            self.assertEqual(prefix_only, ())

    def test_voc102_test_07_last_task_is_out_of_scope_for_classifier(self):
        # The advance job no-ops before classification when there is no next task.
        self.assertIn(
            "No next task after",
            self.auto_advance,
        )

    def test_voc102_test_11_evidence_path_is_strict_and_idempotent_helpers(self):
        self.assertEqual(
            ownership.derive_evidence_relative_path("VOC-102-T01"),
            "t01-evidence.md",
        )
        self.assertEqual(
            ownership.derive_evidence_relative_path("VOC-031-T07a"),
            "t07a-evidence.md",
        )
        with self.assertRaises(ValueError):
            ownership.derive_evidence_relative_path("VOC-102")
        body = ownership.carrier_pr_body(
            change_id="VOC-102",
            task_id="VOC-102-T01",
            package_path=PACKAGE,
            issue_number=866,
            evidence_relative_path="t01-evidence.md",
        )
        self.assertIn(f"Package path: `{PACKAGE}`", body)
        self.assertTrue(
            ownership.is_valid_carrier_pr(
                pr_title="VOC-102: VOC-102-T01",
                pr_body=body,
                change_id="VOC-102",
                task_id="VOC-102-T01",
                package_path=PACKAGE,
                issue_number=866,
                evidence_relative_path="t01-evidence.md",
            )
        )

        pending = ownership.pending_evidence_body(
            "VOC-102-T01", "VOC-102", PACKAGE
        )
        self.assertEqual(
            publisher.evidence_file_action(
                None, has_trusted_pr=True, pending_body=pending
            ),
            "create",
        )
        completed = pending + "\nsource_run_id: `123`\n"
        self.assertEqual(
            publisher.evidence_file_action(
                completed, has_trusted_pr=True, pending_body=pending
            ),
            "preserve",
        )
        with self.assertRaises(publisher.PublisherError):
            publisher.evidence_file_action(
                completed, has_trusted_pr=False, pending_body=pending
            )
        allowed = f"{PACKAGE}/t01-evidence.md"
        publisher.validate_carrier_changed_paths({allowed}, allowed)
        with self.assertRaises(publisher.PublisherError):
            publisher.validate_carrier_changed_paths(
                {allowed, ".github/workflows/pipeline.yml"}, allowed
            )

    def test_voc102_test_12_permission_boundary(self):
        advance_block = self.auto_advance.split("  advance:", 1)[1].split("  prepare-live-evidence:", 1)[0]
        publisher_block = self.auto_advance.split("  prepare-live-evidence:", 1)[1].split("  fail-closed:", 1)[0]
        implement_block = self.auto_advance.split("  implement:", 1)[1]
        self.assertIn("issues: read", advance_block)
        self.assertIn("pull-requests: read", advance_block)
        self.assertIn("contents: read", advance_block)
        self.assertNotIn("actions:", advance_block)
        self.assertIn("create-github-app-token", publisher_block)
        self.assertIn("auto-advance-carrier-publisher.py", publisher_block)
        self.assertNotIn("implement.yml", publisher_block)
        self.assertNotIn("secrets: inherit", publisher_block)
        self.assertIn("uses: KARSIFT/karsift-ai-infra/.github/workflows/implement.yml@main", implement_block)
        fail_block = self.auto_advance.split("  fail-closed:", 1)[1].split("  implement:", 1)[0]
        self.assertIn("permission-issues: write", fail_block)
        self.assertNotIn("permission-contents", fail_block)
        self.assertNotIn("permission-pull-requests", fail_block)
        verify_permissions = self.verify_workflow.split("    permissions:", 1)[1].split("    steps:", 1)[0]
        self.assertIn("actions: read", verify_permissions)
        self.assertNotIn("actions: write", verify_permissions)
        self.assertNotIn("create-github-app-token", self.verify_workflow)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", self.verify_workflow)
        self.assertNotIn("name: verify-auto-advance-live-evidence / verify", self.verify_workflow)

        publisher_source = (
            CONFIG / "auto-advance-carrier-publisher.py"
        ).read_text()
        verifier_runner = (
            CONFIG / "verify-auto-advance-live-evidence-runner.py"
        ).read_text()
        self.assertIn("target = clone_dir / evidence_path", publisher_source)
        self.assertNotIn("target = clone_dir / evidence_relative_path", publisher_source)
        self.assertIn("default_branch", verifier_runner)
        self.assertIn("closedAt", verifier_runner)
        self.assertIn(".karsift/tasks.json", verifier_runner)
        self.assertNotRegex(verifier_runner, r"[\"']logs[\"']")
        self.assertNotRegex(verifier_runner, r"[\"']artifacts[\"']")

    def test_voc102_test_13_verifier_fail_closed_and_exact_head(self):
        closed = datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc)
        closed_at = closed.isoformat().replace("+00:00", "Z")
        source_run = {
            "repository": {"full_name": "KARSIFT/example"},
            "name": "pipeline",
            "display_title": "VOC-102: VOC-102-T00 - implementation",
            "event": "issues",
            "head_branch": "main",
            "path": ".github/workflows/pipeline.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": (closed + timedelta(seconds=2)).isoformat().replace(
                "+00:00", "Z"
            ),
        }
        source_ok = verifier.verify_source_run(
            run=source_run,
            repository="KARSIFT/example",
            default_branch="main",
            predecessor_title=source_run["display_title"],
            predecessor_closed_at=closed_at,
        )
        self.assertTrue(source_ok.ok)

        wrong_branch = dict(source_run)
        wrong_branch["head_branch"] = "develop"
        source_bad = verifier.verify_source_run(
            run=wrong_branch,
            repository="KARSIFT/example",
            default_branch="main",
            predecessor_title=source_run["display_title"],
            predecessor_closed_at=closed_at,
        )
        self.assertEqual(source_bad.reason, "wrong_default_branch")

        jobs = [
            {"name": "auto-advance / advance", "conclusion": "success"},
            {
                "name": "auto-advance / prepare-live-evidence",
                "conclusion": "success",
            },
            {"name": "auto-advance / fail-closed", "conclusion": "skipped"},
            {"name": "auto-advance / implement", "conclusion": "skipped"},
        ]
        self.assertTrue(verifier.verify_source_jobs(jobs).ok)
        jobs[-1]["conclusion"] = "success"
        implement_bad = verifier.verify_source_jobs(jobs)
        self.assertEqual(implement_bad.reason, "implement_job_executed")

        pr_body = ownership.carrier_pr_body(
            change_id="VOC-102",
            task_id="VOC-102-T01",
            package_path=PACKAGE,
            issue_number=866,
            evidence_relative_path="t01-evidence.md",
        )
        carrier_ok = verifier.verify_carrier_state(
            pr={
                "number": 1,
                "title": "VOC-102: VOC-102-T01",
                "body": pr_body,
                "state": "OPEN",
                "isDraft": True,
                "author": {"login": "app/karsift-ai-infra-bot"},
                "headRefName": ownership.branch_name("VOC-102", "VOC-102-T01"),
                "headRefOid": "a" * 40,
                "baseRefName": "develop",
            },
            prs_on_branch=[{"number": 1}],
            comments=[{
                "body": ownership.WAITING_MARKER_PREFIX,
                "author": {"login": "app/karsift-ai-infra-bot"},
            }],
            change_id="VOC-102",
            task_id="VOC-102-T01",
            package_path=PACKAGE,
            issue_number=866,
            integration_branch="develop",
            evidence_text="source_run_id: `123`\n",
            source_run_id=123,
            current_ref="a" * 40,
        )
        self.assertTrue(carrier_ok.ok)

        stale_pr = {
            "number": 1,
            "title": "VOC-102: VOC-102-T01",
            "body": pr_body,
            "state": "OPEN",
            "isDraft": True,
            "author": {"login": "app/karsift-ai-infra-bot"},
            "headRefName": ownership.branch_name("VOC-102", "VOC-102-T01"),
            "headRefOid": "a" * 40,
            "baseRefName": "develop",
        }
        stale = verifier.verify_carrier_state(
            pr=stale_pr,
            prs_on_branch=[{"number": 1}],
            comments=[{
                "body": ownership.WAITING_MARKER_PREFIX,
                "author": {"login": "app/karsift-ai-infra-bot"},
            }],
            change_id="VOC-102",
            task_id="VOC-102-T01",
            package_path=PACKAGE,
            issue_number=866,
            integration_branch="develop",
            evidence_text="source_run_id: `123`\n",
            source_run_id=123,
            current_ref="b" * 40,
        )
        self.assertEqual(stale.reason, "stale_or_invalid_carrier")


if __name__ == "__main__":
    unittest.main()
