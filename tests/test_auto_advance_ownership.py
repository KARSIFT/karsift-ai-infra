from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


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
classifier = load_module(
    "auto_advance_classifier",
    CONFIG / "auto-advance-classifier.py",
)
fail_closed = load_module(
    "auto_advance_fail_closed",
    CONFIG / "auto-advance-fail-closed.py",
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
        cls.project_pipeline_template = (
            REPOSITORY_ROOT
            / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()
        cls.project_pipeline_verify_template = (
            REPOSITORY_ROOT
            / "templates/project-repo/.github/workflows/pipeline-verify.yml"
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
            tasks_md = textwrap.dedent(
                """\
                    ## VOC-000-T01 — ordinary task

                    - Status: pending
                    """
            )
            (package / "tasks.md").write_text(tasks_md, encoding="utf-8")
            result = ownership.classify_next_task(
                str(package), "VOC-000-T01", tasks_md
            )
            self.assertEqual(result.decision, "implement")

    def test_voc102_test_03_missing_tasks_file_fails_closed_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch) / "pkg"
            package.mkdir()
            contract_dir = package / ".karsift/live-evidence"
            contract_dir.mkdir(parents=True)
            (contract_dir / "VOC-000-T01.yaml").write_text(
                contract_yaml("VOC-000-T01"), encoding="utf-8"
            )
            argv = [
                "auto-advance-classifier.py",
                "--package-path",
                str(package),
                "--next-task-id",
                "VOC-000-T01",
                "--change-id",
                "VOC-000",
                "--issue-number",
                "1",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(classifier, "write_output") as write,
            ):
                self.assertEqual(classifier.main(), 0)
            classification = write.call_args.args[0]
            self.assertEqual(classification.decision, "fail-closed")
            self.assertEqual(classification.reason, "missing_tasks_file")

    def test_voc102_test_03_unreadable_tasks_file_uses_sanitized_fail_closed_path(self):
        for read_error in (
            PermissionError("permission fixture"),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "encoding fixture"),
        ):
            with self.subTest(error=type(read_error).__name__):
                with tempfile.TemporaryDirectory() as scratch:
                    package = Path(scratch) / "pkg"
                    package.mkdir()
                    (package / "tasks.md").write_text("# tasks\n", encoding="utf-8")
                    argv = [
                        "auto-advance-classifier.py",
                        "--package-path",
                        str(package),
                        "--next-task-id",
                        "VOC-000-T01",
                        "--change-id",
                        "VOC-000",
                        "--issue-number",
                        "1",
                    ]
                    with (
                        mock.patch.object(sys, "argv", argv),
                        mock.patch.object(
                            classifier.Path, "read_text", side_effect=read_error
                        ),
                        mock.patch.object(classifier, "write_output") as write,
                    ):
                        self.assertEqual(classifier.main(), 0)
                    classification = write.call_args.args[0]
                    self.assertEqual(classification.decision, "fail-closed")
                    self.assertEqual(
                        classification.reason, "unreadable_tasks_file"
                    )

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

            (contract_dir / "VOC-102-T01.yaml").write_text(
                contract_yaml("VOC-102-T99"),
                encoding="utf-8",
            )
            task_mismatch = ownership.classify_next_task(
                str(package), "VOC-102-T01", ""
            )
            self.assertEqual(task_mismatch.decision, "fail-closed")
            self.assertEqual(task_mismatch.reason, "task_id_mismatch")

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
        roster = [
            {"task_id": "VOC-102-T00", "issue": 865},
            {"task_id": "VOC-102-T01", "issue": 866},
        ]
        self.assertEqual(
            ownership.next_roster_task(roster, "VOC-102-T00"),
            ("VOC-102-T01", 866),
        )
        self.assertIsNone(ownership.next_roster_task(roster, "VOC-102-T01"))
        self.assertIsNone(ownership.next_roster_task(roster, "VOC-999-T00"))
        determine = self.auto_advance.split(
            "- name: Determine next task, if any", 1
        )[1].split("  prepare-live-evidence:", 1)[0]
        no_next = determine.index("No next task after")
        classify = determine.index("auto-advance-classifier.py")
        self.assertLess(no_next, classify)
        self.assertNotIn("gh issue create", determine)

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
            risk="R2",
        )
        self.assertIn("Risk classification: R2", body)
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
                risk="R2",
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
        predeclared = "\n".join(
            [
                "# VOC-102-T01 — Evidence (pending operator live evidence)",
                "",
                "Deterministic operator proof stub from the adopted plan.",
                f"Package: `{PACKAGE}`",
                "Change: `VOC-102`",
                "source_run_id: pending",
                "",
            ]
        )
        self.assertTrue(
            ownership.is_valid_predeclared_pending_evidence(
                predeclared,
                task_id="VOC-102-T01",
                change_id="VOC-102",
                package_path=PACKAGE,
            )
        )
        self.assertEqual(
            publisher.evidence_file_action(
                predeclared,
                has_trusted_pr=False,
                pending_body=pending,
                valid_predeclared_pending=True,
            ),
            "normalize",
        )
        adopted_plan_stub = "\n".join(
            [
                "# VOC-102-T01 evidence — live deploy verification",
                "",
                "Pending until the predecessor merges and operator reconciliation runs.",
                "",
                "## gate_status",
                "",
                "pending",
                "",
                "## Required proof",
                "",
                "1. A qualifying workflow reaches conclusion success.",
                "2. No duplicate operational incident is open.",
                "",
            ]
        )
        self.assertTrue(
            ownership.is_valid_predeclared_pending_evidence(
                adopted_plan_stub,
                task_id="VOC-102-T01",
                change_id="VOC-102",
                package_path=PACKAGE,
            )
        )
        self.assertEqual(
            publisher.evidence_file_action(
                adopted_plan_stub,
                has_trusted_pr=False,
                pending_body=pending,
                valid_predeclared_pending=True,
            ),
            "normalize",
        )
        for unsafe in (
            predeclared.replace("source_run_id: pending", "source_run_id: `123`"),
            predeclared + "gate_status: complete\n",
            predeclared.replace("VOC-102-T01", "VOC-999-T01", 1),
            adopted_plan_stub.replace("\npending\n", "\ncomplete\n"),
            adopted_plan_stub + "run_id: 123\n",
            adopted_plan_stub + "source_run_id: 123\n",
            adopted_plan_stub + "## run_id\n\n123\n",
            adopted_plan_stub + "## verdict\n\nPASS\n",
            adopted_plan_stub + f"Package: `{PACKAGE}-other`\n",
            adopted_plan_stub.replace("VOC-102-T01", "VOC-999-T01", 1),
        ):
            self.assertFalse(
                ownership.is_valid_predeclared_pending_evidence(
                    unsafe,
                    task_id="VOC-102-T01",
                    change_id="VOC-102",
                    package_path=PACKAGE,
                )
            )
        allowed = f"{PACKAGE}/t01-evidence.md"
        publisher.validate_carrier_changed_paths({allowed}, allowed)
        with self.assertRaises(publisher.PublisherError):
            publisher.validate_carrier_changed_paths(
                {allowed, ".github/workflows/pipeline.yml"}, allowed
            )

    def test_voc102_test_11_carrier_risk_comes_from_valid_package_metadata(self):
        with tempfile.TemporaryDirectory() as scratch:
            package_root = Path(scratch)
            change = package_root / "change.yaml"
            for source, expected in (
                ("risk: R0\n", "R0"),
                ('risk: "r3"\n', "R3"),
            ):
                with self.subTest(source=source):
                    change.write_text(source, encoding="utf-8")
                    self.assertEqual(publisher.read_package_risk(package_root), expected)

            for source in (
                "title: no risk\n",
                "risk: R5\n",
                "risk: R1\nrisk: R2\n",
                "  risk: R2\n",
            ):
                with self.subTest(invalid_source=source):
                    change.write_text(source, encoding="utf-8")
                    with self.assertRaisesRegex(
                        publisher.PublisherError, "invalid_package_risk"
                    ):
                        publisher.read_package_risk(package_root)

    def test_voc102_test_11_existing_carrier_repairs_marker_without_overwrite(self):
        task_id = "VOC-102-T01"
        evidence_relative = "t01-evidence.md"
        pr_body = ownership.carrier_pr_body(
            change_id="VOC-102",
            task_id=task_id,
            package_path=PACKAGE,
            issue_number=866,
            evidence_relative_path=evidence_relative,
            risk="R4",
        )
        existing_pr = {
            "number": 900,
            "title": "VOC-102: VOC-102-T01",
            "body": pr_body,
            "state": "OPEN",
            "isDraft": True,
            "author": {"login": "app/karsift-ai-infra-bot"},
            "headRefName": "agent/voc-102-voc-102-t01",
            "headRefOid": "a" * 40,
            "baseRefName": "develop",
        }
        with tempfile.TemporaryDirectory() as scratch:
            workdir = Path(scratch) / "carrier-work"
            completed = "# evidence\n\nsource_run_id: `123`\n"

            def fake_clone(command, **_kwargs):
                clone_dir = Path(command[4])
                target = clone_dir / PACKAGE / evidence_relative
                target.parent.mkdir(parents=True)
                target.write_text(completed, encoding="utf-8")
                roster = clone_dir / PACKAGE / ".karsift/tasks.json"
                roster.parent.mkdir(parents=True, exist_ok=True)
                roster.write_text(
                    json.dumps([{"task_id": task_id, "issue": 866}]),
                    encoding="utf-8",
                )
                (clone_dir / PACKAGE / "change.yaml").write_text(
                    "risk: R4\n", encoding="utf-8"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            def fake_git(args, **_kwargs):
                if args[:3] == ["ls-remote", "--heads", "origin"]:
                    return f"{'a' * 40}\trefs/heads/agent/voc-102-voc-102-t01"
                if args[:2] == ["diff", "--name-only"]:
                    return f"{PACKAGE}/{evidence_relative}"
                if args and args[0] == "merge-base":
                    return "b" * 40
                return ""

            with (
                mock.patch.object(publisher, "find_pr_for_branch", return_value=existing_pr),
                mock.patch.object(publisher, "validate_issue_and_roster"),
                mock.patch.object(publisher, "post_deduplicated_comment") as post,
                mock.patch.object(publisher, "run_git", side_effect=fake_git),
                mock.patch.object(publisher.tempfile, "mkdtemp", return_value=str(workdir)),
                mock.patch.object(publisher.subprocess, "run", side_effect=fake_clone),
            ):
                publisher.ensure_carrier(
                    repo="KARSIFT/example",
                    token="masked-fixture-token",
                    integration_branch="develop",
                    change_id="VOC-102",
                    task_id=task_id,
                    package_path=PACKAGE,
                    issue_number=866,
                    evidence_relative_path=evidence_relative,
                )

            self.assertEqual(
                (workdir / "repo" / PACKAGE / evidence_relative).read_text(),
                completed,
            )
            post.assert_called_once()

    def test_voc102_test_11_closed_carrier_fails_closed_for_operator_cleanup(self):
        body = ownership.carrier_pr_body(
            change_id="VOC-102",
            task_id="VOC-102-T01",
            package_path=PACKAGE,
            issue_number=866,
            evidence_relative_path="t01-evidence.md",
            risk="R4",
        )
        with self.assertRaisesRegex(
            publisher.PublisherError, "conflicting_existing_pr"
        ):
            publisher.validate_existing_pr(
                pr={
                    "number": 900,
                    "title": "VOC-102: VOC-102-T01",
                    "body": body,
                    "state": "CLOSED",
                    "isDraft": True,
                    "author": {"login": "app/karsift-ai-infra-bot"},
                    "headRefName": "agent/voc-102-voc-102-t01",
                    "baseRefName": "develop",
                },
                branch="agent/voc-102-voc-102-t01",
                integration_branch="develop",
                change_id="VOC-102",
                task_id="VOC-102-T01",
                package_path=PACKAGE,
                issue_number=866,
                evidence_relative_path="t01-evidence.md",
                risk="R4",
            )

    def test_voc102_test_11_existing_carrier_repairs_missing_evidence_file(self):
        task_id = "VOC-102-T01"
        evidence_relative = "t01-evidence.md"
        evidence_path = f"{PACKAGE}/{evidence_relative}"
        pr_body = ownership.carrier_pr_body(
            change_id="VOC-102",
            task_id=task_id,
            package_path=PACKAGE,
            issue_number=866,
            evidence_relative_path=evidence_relative,
            risk="R4",
        )
        existing_pr = {
            "number": 900,
            "title": "VOC-102: VOC-102-T01",
            "body": pr_body,
            "state": "OPEN",
            "isDraft": True,
            "author": {"login": "app/karsift-ai-infra-bot"},
            "headRefName": "agent/voc-102-voc-102-t01",
            "headRefOid": "a" * 40,
            "baseRefName": "develop",
        }
        with tempfile.TemporaryDirectory() as scratch:
            workdir = Path(scratch) / "carrier-work"

            def fake_clone(command, **_kwargs):
                clone_dir = Path(command[4])
                roster = clone_dir / PACKAGE / ".karsift/tasks.json"
                roster.parent.mkdir(parents=True, exist_ok=True)
                roster.write_text(
                    json.dumps([{"task_id": task_id, "issue": 866}]),
                    encoding="utf-8",
                )
                (clone_dir / PACKAGE / "change.yaml").write_text(
                    "risk: R4\n", encoding="utf-8"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            def fake_git(args, **_kwargs):
                if args[:3] == ["ls-remote", "--heads", "origin"]:
                    return f"{'a' * 40}\trefs/heads/agent/voc-102-voc-102-t01"
                if args[:2] == ["diff", "--name-only"]:
                    return evidence_path
                if args and args[0] == "merge-base":
                    return "b" * 40
                return ""

            with (
                mock.patch.object(publisher, "find_pr_for_branch", return_value=existing_pr),
                mock.patch.object(publisher, "validate_issue_and_roster"),
                mock.patch.object(publisher, "post_deduplicated_comment") as post,
                mock.patch.object(publisher, "run_git", side_effect=fake_git) as git,
                mock.patch.object(publisher.tempfile, "mkdtemp", return_value=str(workdir)),
                mock.patch.object(publisher.subprocess, "run", side_effect=fake_clone),
            ):
                publisher.ensure_carrier(
                    repo="KARSIFT/example",
                    token="masked-fixture-token",
                    integration_branch="develop",
                    change_id="VOC-102",
                    task_id=task_id,
                    package_path=PACKAGE,
                    issue_number=866,
                    evidence_relative_path=evidence_relative,
                )

            target = workdir / "repo" / evidence_path
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                ownership.pending_evidence_body(task_id, "VOC-102", PACKAGE),
            )
            git.assert_any_call(["add", evidence_path], cwd=workdir / "repo")
            post.assert_called_once()

    def test_voc102_test_11_fresh_carrier_creates_file_branch_and_draft_pr(self):
        task_id = "VOC-102-T01"
        evidence_relative = "t01-evidence.md"
        evidence_path = f"{PACKAGE}/{evidence_relative}"
        with tempfile.TemporaryDirectory() as scratch:
            workdir = Path(scratch) / "carrier-work"

            def fake_clone(command, **_kwargs):
                clone_dir = Path(command[4])
                (clone_dir / PACKAGE).mkdir(parents=True, exist_ok=True)
                (clone_dir / PACKAGE / "change.yaml").write_text(
                    "risk: R1\n", encoding="utf-8"
                )
                return mock.Mock(returncode=0, stdout="", stderr="")

            def fake_git(args, **_kwargs):
                if args[:3] == ["ls-remote", "--heads", "origin"]:
                    return ""
                return ""

            with (
                mock.patch.object(publisher, "find_pr_for_branch", return_value=None),
                mock.patch.object(publisher, "validate_issue_and_roster"),
                mock.patch.object(publisher, "post_deduplicated_comment") as post,
                mock.patch.object(publisher, "run_gh") as github,
                mock.patch.object(publisher, "run_git", side_effect=fake_git) as git,
                mock.patch.object(publisher.tempfile, "mkdtemp", return_value=str(workdir)),
                mock.patch.object(publisher.subprocess, "run", side_effect=fake_clone),
            ):
                publisher.ensure_carrier(
                    repo="KARSIFT/example",
                    token="masked-fixture-token",
                    integration_branch="develop",
                    change_id="VOC-102",
                    task_id=task_id,
                    package_path=PACKAGE,
                    issue_number=866,
                    evidence_relative_path=evidence_relative,
                )

            target = workdir / "repo" / evidence_path
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                ownership.pending_evidence_body(task_id, "VOC-102", PACKAGE),
            )
            git.assert_any_call(["add", evidence_path], cwd=workdir / "repo")
            git.assert_any_call(
                ["commit", "-m", f"{task_id}: pending operator live-evidence carrier"],
                cwd=workdir / "repo",
            )
            git.assert_any_call(
                ["push", "-u", "origin", "agent/voc-102-voc-102-t01"],
                cwd=workdir / "repo",
                env=mock.ANY,
            )
            create_args = github.call_args.args[0]
            self.assertEqual(create_args[:2], ["pr", "create"])
            self.assertIn("--draft", create_args)
            self.assertIn(
                "Risk classification: R1",
                create_args[create_args.index("--body") + 1],
            )
            post.assert_called_once()

    def test_voc102_test_12_permission_boundary(self):
        secret_interface = self.auto_advance.split("    secrets:", 1)[1].split(
            "\njobs:", 1
        )[0]
        self.assertEqual(
            set(re.findall(r"^      ([A-Z][A-Z0-9_]+):", secret_interface, re.MULTILINE)),
            {"CURSOR_API_KEY", "KARSIFT_BOT_APP_ID", "KARSIFT_BOT_PRIVATE_KEY"},
        )
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
        self.assertIn("    name: verify", self.verify_workflow)
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

        template = self.project_pipeline_verify_template
        self.assertIn("verify-auto-advance-live-evidence", template)
        self.assertIn("verify-ready-for-review-reuse", template)
        self.assertIn("verify-post-promotion-workflow]", template)
        template_auto_advance = self.project_pipeline_template.split("  auto-advance:", 1)[1].split(
            "  live-evidence-reconcile:", 1
        )[0]
        self.assertNotIn("secrets: inherit", template_auto_advance)
        self.assertEqual(
            set(
                re.findall(
                    r"^      ([A-Z][A-Z0-9_]+):",
                    template_auto_advance.split("    secrets:", 1)[1],
                    re.MULTILINE,
                )
            ),
            {"CURSOR_API_KEY", "KARSIFT_BOT_APP_ID", "KARSIFT_BOT_PRIVATE_KEY"},
        )
        template_verify = template.split(
            "  verify-auto-advance-live-evidence:", 1
        )[1].split("  verify-ready-for-review-reuse:", 1)[0]
        self.assertIn("actions: read", template_verify)
        self.assertNotIn("actions: write", template_verify)
        self.assertNotIn("secrets:", template_verify)
        for field in (
            "verify_source_run_id",
            "verify_waiting_pr_number",
            "verify_change_id",
            "verify_task_id",
            "verify_package_path",
        ):
            self.assertIn(field, template_verify)

    def test_voc102_test_12_fail_closed_marker_is_sanitized_and_deduplicated(self):
        marker = f"{fail_closed.FAIL_CLOSED_MARKER_PREFIX} `VOC-102-T01`"
        trusted = {
            "comments": [
                {
                    "body": marker,
                    "author": {"login": "app/karsift-ai-infra-bot"},
                }
            ]
        }
        with mock.patch.object(
            fail_closed.subprocess,
            "run",
            return_value=mock.Mock(stdout=json.dumps(trusted)),
        ) as run:
            self.assertTrue(
                fail_closed.issue_has_marker(
                    "KARSIFT/example",
                    866,
                    "masked-fixture-token",
                    marker,
                )
            )
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["gh", "issue", "view"])
        self.assertNotIn("masked-fixture-token", command)

        untrusted = {
            "comments": [
                {
                    "body": marker,
                    "author": {"login": "untrusted-user"},
                }
            ]
        }
        with mock.patch.object(
            fail_closed.subprocess,
            "run",
            return_value=mock.Mock(stdout=json.dumps(untrusted)),
        ):
            with self.assertRaisesRegex(ValueError, "untrusted_fail_closed_marker"):
                fail_closed.issue_has_marker(
                    "KARSIFT/example",
                    866,
                    "masked-fixture-token",
                    marker,
                )

        self.assertIsNotNone(fail_closed.SAFE_REASON_RE.fullmatch("invalid_contract"))
        self.assertIsNone(fail_closed.SAFE_REASON_RE.fullmatch("email@example.invalid"))
        self.assertIsNone(fail_closed.SAFE_REASON_RE.fullmatch("contains spaces"))

        argv = [
            "auto-advance-fail-closed.py",
            "--repository",
            "KARSIFT/example",
            "--token",
            "masked-fixture-token",
            "--task-id",
            "VOC-102-T01",
            "--issue-number",
            "866",
            "--reason",
            "invalid_contract",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(fail_closed, "issue_has_marker", return_value=False),
            mock.patch.object(fail_closed.subprocess, "run") as post,
        ):
            self.assertEqual(fail_closed.main(), 0)
        post_command = post.call_args.args[0]
        body = post_command[post_command.index("--body") + 1]
        self.assertIn("invalid_contract", body)
        self.assertIn("No implementer run was started", body)
        self.assertNotIn("masked-fixture-token", body)

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(fail_closed, "issue_has_marker", return_value=True),
            mock.patch.object(fail_closed.subprocess, "run") as duplicate_post,
        ):
            self.assertEqual(fail_closed.main(), 0)
        duplicate_post.assert_not_called()

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
            risk="R4",
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
            risk="R4",
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
            risk="R4",
        )
        self.assertEqual(stale.reason, "stale_or_invalid_carrier")

    def test_voc102_test_13_complete_verifier_rejection_matrix(self):
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

        source_cases = {
            "wrong_repository": {"repository": {"full_name": "KARSIFT/other"}},
            "wrong_workflow": {"name": "other"},
            "wrong_source_issue": {"display_title": "different"},
            "wrong_event": {"event": "workflow_dispatch"},
            "wrong_default_branch": {"head_branch": "develop"},
            "source_run_not_successful": {"conclusion": "failure"},
            "missing_source_timestamp": {"created_at": "not-a-timestamp"},
            "source_run_not_bound_to_close": {
                "created_at": (closed + timedelta(minutes=11)).isoformat().replace(
                    "+00:00", "Z"
                )
            },
        }
        for reason, mutation in source_cases.items():
            with self.subTest(source_reason=reason):
                candidate = dict(source_run)
                candidate.update(mutation)
                result = verifier.verify_source_run(
                    run=candidate,
                    repository="KARSIFT/example",
                    default_branch="main",
                    predecessor_title=source_run["display_title"],
                    predecessor_closed_at=closed_at,
                )
                self.assertEqual(result.reason, reason)

        good_jobs = [
            {"name": "auto-advance / advance", "conclusion": "success"},
            {"name": "auto-advance / prepare-live-evidence", "conclusion": "success"},
            {"name": "auto-advance / fail-closed", "conclusion": "skipped"},
            {"name": "auto-advance / implement", "conclusion": "skipped"},
        ]
        job_cases = {
            "source_job_mismatch": good_jobs[1:],
            "implement_skip_not_observed": good_jobs[:-1],
            "implement_job_executed": [*good_jobs[:-1], {"name": "auto-advance / implement", "conclusion": "success"}],
        }
        for reason, jobs in job_cases.items():
            with self.subTest(job_reason=reason):
                self.assertEqual(verifier.verify_source_jobs(jobs).reason, reason)

        pr_body = ownership.carrier_pr_body(
            change_id="VOC-102",
            task_id="VOC-102-T01",
            package_path=PACKAGE,
            issue_number=866,
            evidence_relative_path="t01-evidence.md",
            risk="R4",
        )
        base_pr = {
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
        base_comments = [{
            "body": ownership.WAITING_MARKER_PREFIX,
            "author": {"login": "app/karsift-ai-infra-bot"},
        }]

        def carrier(**overrides):
            values = {
                "pr": dict(base_pr),
                "prs_on_branch": [{"number": 1}],
                "comments": list(base_comments),
                "change_id": "VOC-102",
                "task_id": "VOC-102-T01",
                "package_path": PACKAGE,
                "issue_number": 866,
                "integration_branch": "develop",
                "evidence_text": "source_run_id: `123`\n",
            "source_run_id": 123,
            "current_ref": "a" * 40,
            "risk": "R4",
        }
            values.update(overrides)
            return verifier.verify_carrier_state(**values)

        carrier_cases = [
            ("invalid_current_ref", {"current_ref": "short"}),
            ("stale_or_invalid_carrier", {"pr": {**base_pr, "isDraft": False}}),
            ("untrusted_carrier_author", {"pr": {**base_pr, "author": {"login": "untrusted-user"}}}),
            ("duplicate_carrier", {"prs_on_branch": [{"number": 1}, {"number": 2}]}),
            ("untrusted_carrier", {"pr": {**base_pr, "body": "wrong"}}),
            ("duplicate_or_missing_marker", {"comments": []}),
            ("duplicate_or_missing_marker", {"comments": [*base_comments, *base_comments]}),
            ("untrusted_waiting_marker", {"comments": [{"body": ownership.WAITING_MARKER_PREFIX, "author": {"login": "untrusted-user"}}]}),
            ("missing_evidence_file", {"evidence_text": None}),
            ("source_run_not_recorded", {"evidence_text": "source_run_id: `999`\n"}),
        ]
        for reason, overrides in carrier_cases:
            with self.subTest(carrier_reason=reason, overrides=overrides):
                self.assertEqual(carrier(**overrides).reason, reason)

        self.assertTrue(verifier.verify_issue_state("OPEN", "OPEN", "wrong_state").ok)
        self.assertEqual(
            verifier.verify_issue_state("CLOSED", "OPEN", "wrong_state").reason,
            "wrong_state",
        )


if __name__ == "__main__":
    unittest.main()
