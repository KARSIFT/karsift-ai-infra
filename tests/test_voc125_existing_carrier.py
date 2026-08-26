"""VOC-125 existing-carrier resume identity tests."""

from __future__ import annotations

import json
import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from bind_existing_carrier import (  # noqa: E402
    BindFailure,
    LsRemoteProbeResult,
    OpenPrListProbeResult,
    bind_existing_carrier,
    interpret_ls_remote_probe,
    interpret_open_pr_list_probe,
    validate_implement_pr_metadata,
    validate_review_comments,
)


def load_bind_runner():
    path = ROOT / "config/bind-existing-carrier-runner.py"
    spec = spec_from_file_location("bind_existing_carrier_runner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BIND_RUNNER = load_bind_runner()

HEAD = "0b7be8c531be8300d5a1d5534acc83bf4d6a1791"
BASE = "e910eb4a21d48bbb5b3e0c30b8ee647d64683dbe"
CHANGE_ID = "VOC-122"
TASK_ID = "VOC-122-T00"
PACKAGE_PATH = (
    "specs/changes/VOC-122-promotion-recovery-must-replan-required-checks"
)
ISSUE = "1003"
REPO = "KARSIFT/vocanova-platform-sandbox"
BRANCH = "agent/voc-122-voc-122-t00"
INTEGRATION = "develop"


def implement_pr_body() -> str:
    return (
        f"Implements task `{TASK_ID}` from `{CHANGE_ID}` (`{PACKAGE_PATH}`).\n\n"
        f"Closes #{ISSUE}.\n\n"
        "Implemented by the implementer role (attempt 1 of 2,\n"
        "model resolved from karsift-ai-infra/config/roles.yml). Independent\n"
        "exact-revision review is still pending - this PR is not authorized to\n"
        "merge on its own.\n\n"
        "Risk classification: R4\n\n"
        f"Package path: `{PACKAGE_PATH}`\n"
    )


def pr_data(**overrides) -> dict:
    data = {
        "number": "1012",
        "state": "OPEN",
        "title": f"{CHANGE_ID}: {TASK_ID}",
        "body": implement_pr_body(),
        "headRefName": BRANCH,
        "baseRefName": INTEGRATION,
        "headRefOid": HEAD,
        "baseRefOid": BASE,
        "repository": REPO,
    }
    data.update(overrides)
    return data


def valid_review_comment() -> dict:
    body = (
        f"**Independent verification - bound to commit `{HEAD}`**\n"
        f"task_id: `{TASK_ID}`\n"
        f"package_path: `{PACKAGE_PATH}`\n"
        f"authority_issue: `{ISSUE}`\n"
        f"base_sha: `{BASE}`\n"
        "VERDICT: FAIL\n"
    )
    return {
        "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
        "body": body,
    }


class Voc125ExistingCarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.remediate = (ROOT / ".github/workflows/remediate.yml").read_text()
        cls.pipeline_template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def test_valid_attempt2_resume_derives_shas_from_pr_number(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=[valid_review_comment()],
        )
        self.assertEqual(result.expected_head_sha, HEAD)
        self.assertEqual(result.expected_base_sha, BASE)

    def test_empty_binding_class_from_issue_1020(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=None,
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("EMPTY_BINDING"))

    def test_attempt3_fails_closed(self):
        result = bind_existing_carrier(
            attempt=3,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("INVALID_ATTEMPT"))

    def test_attempt1_with_existing_carrier_fails_closed(self):
        result = bind_existing_carrier(
            attempt=1,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=None,
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("ATTEMPT1_EXISTING_CARRIER"))

    def test_sha_pr_disagreement_fails_closed(self):
        other = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha=other,
            expected_base_sha=BASE,
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("SHA_PR_DISAGREEMENT"))

    def test_stale_remote_head_fails_closed(self):
        stale = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=stale,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result.code, "STALE_HEAD")

    def test_wrong_branch_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(headRefName="agent/wrong-branch"),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("WRONG_BRANCH"))

    def test_metadata_mismatch_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(body=implement_pr_body().replace(ISSUE, "9999")),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("METADATA_MISMATCH"))

    def test_closed_task_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="CLOSED",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("CLOSED_TASK"))

    def test_foreign_review_fails_closed(self):
        bad = valid_review_comment()
        bad["body"] = bad["body"].replace(TASK_ID, "VOC-999-T00")
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=[bad],
        )
        self.assertEqual(result, BindFailure("FOREIGN_REVIEW"))

    def test_absent_review_allowed_for_ci_failure_class(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=[],
        )
        self.assertEqual(result.expected_head_sha, HEAD)

    def test_automatic_remediate_sha_path_with_matching_pr(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="",
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=[],
        )
        self.assertEqual(result.expected_head_sha, HEAD)
        self.assertEqual(result.expected_base_sha, BASE)

    def test_malformed_sha_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="not-a-sha",
            expected_base_sha=BASE,
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("MALFORMED_SHA"))

    def test_attempt1_with_existing_pr_number_fails_closed(self):
        result = bind_existing_carrier(
            attempt=1,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=None,
            has_remote_branch=False,
            open_pr_number=None,
            pr_data=None,
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("ATTEMPT1_WITH_EXISTING_PR_NUMBER"))

    def test_attempt1_with_sha_inputs_fails_closed(self):
        result = bind_existing_carrier(
            attempt=1,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="",
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            issue_state="OPEN",
            remote_branch_head=None,
            has_remote_branch=False,
            open_pr_number=None,
            pr_data=None,
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("ATTEMPT1_WITH_SHA_INPUTS"))

    def test_wrong_pr_missing_pr_data_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=None,
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("WRONG_PR"))

    def test_wrong_pr_number_mismatch_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(number="1099"),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("WRONG_PR"))

    def test_wrong_pr_closed_state_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(state="CLOSED"),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("WRONG_PR"))

    def test_wrong_pr_other_repository_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(repository="KARSIFT/other-repo"),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("WRONG_PR"))

    def test_wrong_pr_merged_state_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(state="MERGED"),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("WRONG_PR"))

    def test_missing_remote_branch_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=None,
            has_remote_branch=False,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("STALE_HEAD"))

    def test_pr_number_with_matching_supplied_shas_succeeds(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=[valid_review_comment()],
        )
        self.assertEqual(result.expected_head_sha, HEAD)
        self.assertEqual(result.expected_base_sha, BASE)

    def test_changed_head_bind_race_fails_closed(self):
        raced = "cccccccccccccccccccccccccccccccccccccccc"
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=raced,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result.code, "STALE_HEAD")

    def test_review_comments_unavailable_fails_closed(self):
        result = bind_existing_carrier(
            attempt=2,
            change_id=CHANGE_ID,
            package_path=PACKAGE_PATH,
            task_id=TASK_ID,
            issue_number=ISSUE,
            integration_branch=INTEGRATION,
            repository=REPO,
            existing_pr_number="1012",
            expected_head_sha="",
            expected_base_sha="",
            issue_state="OPEN",
            remote_branch_head=HEAD,
            has_remote_branch=True,
            open_pr_number="1012",
            pr_data=pr_data(),
            review_comments=None,
        )
        self.assertEqual(result, BindFailure("REVIEW_COMMENTS_UNAVAILABLE"))

    def test_interpret_ls_remote_probe_absent_success_and_failure(self):
        absent = interpret_ls_remote_probe(returncode=0, stdout="")
        self.assertEqual(absent.status, "OK_ABSENT")
        self.assertIsNone(absent.head_sha)

        present = interpret_ls_remote_probe(
            returncode=0,
            stdout=f"{HEAD}\trefs/heads/{BRANCH}\n",
        )
        self.assertEqual(present.status, "OK_PRESENT")
        self.assertEqual(present.head_sha, HEAD)

        failed = interpret_ls_remote_probe(returncode=128, stdout="")
        self.assertEqual(failed.status, "PROBE_FAILED")

    def test_interpret_open_pr_list_probe_absent_success_and_failure(self):
        absent = interpret_open_pr_list_probe(returncode=0, stdout="[]")
        self.assertEqual(absent.status, "OK_ABSENT")
        self.assertIsNone(absent.pr_number)

        present = interpret_open_pr_list_probe(
            returncode=0,
            stdout=json.dumps([{"number": 1012}]),
        )
        self.assertEqual(present.status, "OK_PRESENT")
        self.assertEqual(present.pr_number, "1012")

        failed = interpret_open_pr_list_probe(returncode=1, stdout="")
        self.assertEqual(failed.status, "PROBE_FAILED")

        invalid = interpret_open_pr_list_probe(returncode=0, stdout="not-json")
        self.assertEqual(invalid.status, "PROBE_FAILED")

    def test_git_ls_remote_command_uses_credential_helper_without_token(self):
        command = BIND_RUNNER.git_ls_remote_command(branch=BRANCH)
        self.assertIn("credential.https://github.com.helper=!gh auth git-credential", command)
        self.assertNotIn("GH_TOKEN", command)
        self.assertNotIn("x-access-token", " ".join(command))

    def test_probe_remote_branch_authenticates_via_env_not_argv(self):
        with mock.patch.object(BIND_RUNNER.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="")
            BIND_RUNNER.probe_remote_branch(
                branch=BRANCH,
                env={"GH_TOKEN": "fixture-token"},
            )
        command = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertIn("gh auth git-credential", " ".join(command))
        self.assertNotIn("fixture-token", " ".join(command))
        self.assertEqual(env["GH_TOKEN"], "fixture-token")

    def test_runner_fails_closed_when_remote_probe_fails(self):
        argv = [
            "bind-existing-carrier-runner.py",
            "--repository",
            REPO,
            "--attempt",
            "1",
            "--change-id",
            CHANGE_ID,
            "--package-path",
            PACKAGE_PATH,
            "--task-id",
            TASK_ID,
            "--issue-number",
            ISSUE,
            "--integration-branch",
            INTEGRATION,
        ]
        with mock.patch.dict("os.environ", {"GH_TOKEN": "fixture-token"}, clear=False):
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    BIND_RUNNER,
                    "gh_json",
                    return_value={"state": "OPEN"},
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_remote_branch",
                    return_value=LsRemoteProbeResult(status="PROBE_FAILED"),
                ),
            ):
                exit_code = BIND_RUNNER.main()
        self.assertEqual(exit_code, 1)

    def test_runner_fails_closed_when_open_pr_probe_fails(self):
        argv = [
            "bind-existing-carrier-runner.py",
            "--repository",
            REPO,
            "--attempt",
            "1",
            "--change-id",
            CHANGE_ID,
            "--package-path",
            PACKAGE_PATH,
            "--task-id",
            TASK_ID,
            "--issue-number",
            ISSUE,
            "--integration-branch",
            INTEGRATION,
        ]
        with mock.patch.dict("os.environ", {"GH_TOKEN": "fixture-token"}, clear=False):
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    BIND_RUNNER,
                    "gh_json",
                    return_value={"state": "OPEN"},
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_remote_branch",
                    return_value=LsRemoteProbeResult(status="OK_ABSENT"),
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_open_pr",
                    return_value=OpenPrListProbeResult(status="PROBE_FAILED"),
                ),
            ):
                exit_code = BIND_RUNNER.main()
        self.assertEqual(exit_code, 1)

    def test_runner_attempt1_succeeds_when_probes_confirm_absence(self):
        argv = [
            "bind-existing-carrier-runner.py",
            "--repository",
            REPO,
            "--attempt",
            "1",
            "--change-id",
            CHANGE_ID,
            "--package-path",
            PACKAGE_PATH,
            "--task-id",
            TASK_ID,
            "--issue-number",
            ISSUE,
            "--integration-branch",
            INTEGRATION,
        ]
        with mock.patch.dict("os.environ", {"GH_TOKEN": "fixture-token"}, clear=False):
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    BIND_RUNNER,
                    "gh_json",
                    return_value={"state": "OPEN"},
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_remote_branch",
                    return_value=LsRemoteProbeResult(status="OK_ABSENT"),
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_open_pr",
                    return_value=OpenPrListProbeResult(status="OK_ABSENT"),
                ),
            ):
                exit_code = BIND_RUNNER.main()
        self.assertEqual(exit_code, 0)

    def test_runner_attempt1_fails_when_probe_shows_existing_carrier(self):
        argv = [
            "bind-existing-carrier-runner.py",
            "--repository",
            REPO,
            "--attempt",
            "1",
            "--change-id",
            CHANGE_ID,
            "--package-path",
            PACKAGE_PATH,
            "--task-id",
            TASK_ID,
            "--issue-number",
            ISSUE,
            "--integration-branch",
            INTEGRATION,
        ]
        with mock.patch.dict("os.environ", {"GH_TOKEN": "fixture-token"}, clear=False):
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    BIND_RUNNER,
                    "gh_json",
                    return_value={"state": "OPEN"},
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_remote_branch",
                    return_value=LsRemoteProbeResult(
                        status="OK_PRESENT",
                        head_sha=HEAD,
                    ),
                ),
                mock.patch.object(
                    BIND_RUNNER,
                    "probe_open_pr",
                    return_value=OpenPrListProbeResult(status="OK_ABSENT"),
                ),
            ):
                exit_code = BIND_RUNNER.main()
        self.assertEqual(exit_code, 1)

    def test_implement_workflow_declares_bind_step_before_branch(self):
        self.assertIn("existing_pr_number:", self.implement)
        self.assertIn("Bind existing-carrier recovery identity", self.implement)
        self.assertIn("bind-existing-carrier-runner.py", self.implement)
        bind_index = self.implement.index("- name: Bind existing-carrier recovery identity")
        branch_index = self.implement.index("- name: Create implementation branch")
        model_index = self.implement.index("- name: Resolve implementer model")
        self.assertLess(bind_index, branch_index)
        self.assertLess(bind_index, model_index)

    def test_remediate_retry_forwards_existing_pr_number(self):
        retry = self.remediate.split("  retry:", 1)[1]
        self.assertIn("existing_pr_number: ${{ inputs.pr_number }}", retry)
        self.assertIn("expected_head_sha: ${{ inputs.expected_head_sha }}", retry)
        self.assertIn("expected_base_sha: ${{ inputs.expected_base_sha }}", retry)

    def test_template_pipeline_exposes_existing_pr_number_only(self):
        dispatch = self.pipeline_template.split("workflow_dispatch:", 1)[1].split(
            "jobs:", 1
        )[0]
        self.assertIn("existing_pr_number:", dispatch)
        self.assertNotIn("expected_head_sha:", dispatch)
        self.assertNotIn("expected_base_sha:", dispatch)
        implement = self.pipeline_template.split("  implement:", 1)[1].split(
            "\n  plan:", 1
        )[0]
        self.assertIn("existing_pr_number: ${{ inputs.existing_pr_number }}", implement)

    def test_validate_implement_pr_metadata_accepts_carrier_body(self):
        self.assertTrue(
            validate_implement_pr_metadata(
                pr_title=f"{CHANGE_ID}: {TASK_ID}",
                pr_body=implement_pr_body(),
                change_id=CHANGE_ID,
                task_id=TASK_ID,
                package_path=PACKAGE_PATH,
                issue_number=ISSUE,
            )
        )

    def test_validate_review_comments_foreign_vs_absent(self):
        self.assertEqual(
            validate_review_comments(
                [],
                head_sha=HEAD,
                base_sha=BASE,
                task_id=TASK_ID,
                package_path=PACKAGE_PATH,
                issue_number=ISSUE,
            ),
            "ABSENT",
        )
        self.assertEqual(
            validate_review_comments(
                [valid_review_comment()],
                head_sha=HEAD,
                base_sha=BASE,
                task_id=TASK_ID,
                package_path=PACKAGE_PATH,
                issue_number=ISSUE,
            ),
            "OK",
        )


if __name__ == "__main__":
    unittest.main()
