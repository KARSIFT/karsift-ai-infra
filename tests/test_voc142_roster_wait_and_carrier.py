"""VOC-142 roster wait completeness and roster PR carrier reuse tests."""

from __future__ import annotations

import json
import sys
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from authoritative_checks import evaluate, select_authoritative  # noqa: E402
from roster_carrier import (  # noqa: E402
    ROSTER_CARRIER_METADATA_CONVERGENCE_TIMEOUT_SECONDS,
    RosterCarrierFailure,
    RosterCarrierResult,
    open_carrier_metadata_lag_is_transient,
    resolve_roster_carrier,
)
from roster_pr_wait import (  # noqa: E402
    ROSTER_REQUIRED_CONTEXTS,
    missing_required_roster_contexts,
    roster_required_set_complete,
    roster_wait_snapshot,
)


HEAD_SHA = "98dd0936a73b64a6b548da6cf2000a6d000917ac"
STALE_HEAD_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BASE_SHA = "bb4ffdf5d53d27baf4c25c28caf3acfeda9e07a2"
REPOSITORY = "KARSIFT/vocanova-platform-sandbox"
HEAD_REF = "karsift/roster-voc-141"
BASE_REF = "develop"
PR_NUMBER = 1112


def load_runner(filename: str, module_name: str):
    path = ROOT / "config" / filename
    spec = spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROSTER_WAIT_RUNNER = load_runner("roster-pr-wait-runner.py", "roster_pr_wait_runner")
ROSTER_CARRIER_RUNNER = load_runner("roster-carrier-runner.py", "roster_carrier_runner")
AUTHORITATIVE_CHECKS_RUNNER = load_runner(
    "authoritative-checks-runner.py", "authoritative_checks_runner"
)


def pull_request(**overrides) -> dict:
    payload = {
        "number": PR_NUMBER,
        "head": {
            "ref": HEAD_REF,
            "sha": HEAD_SHA,
            "repo": {"full_name": REPOSITORY},
        },
        "base": {
            "ref": BASE_REF,
            "sha": BASE_SHA,
            "repo": {"full_name": REPOSITORY},
        },
    }
    payload.update(overrides)
    return payload


def check_run(
    identifier: int,
    name: str,
    *,
    status: str = "completed",
    conclusion: str = "success",
    second: int = 1,
    details_url: str | None = None,
) -> dict:
    item = {
        "id": identifier,
        "name": name,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "repository": REPOSITORY,
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "pr_number": PR_NUMBER,
        "app": {"slug": "github-actions"},
        "started_at": f"2026-08-30T23:57:{second:02d}Z",
    }
    if details_url is not None:
        item["details_url"] = details_url
    return item


def evaluate_checks(checks: list[dict]) -> dict:
    selected = select_authoritative(
        checks,
        [],
        expected={
            "repository": REPOSITORY,
            "head_sha": HEAD_SHA,
            "base_sha": BASE_SHA,
            "pr_number": PR_NUMBER,
        },
    )
    return evaluate(selected)


def pr_record(
    number: int,
    *,
    state: str = "OPEN",
    head_sha: str = HEAD_SHA,
    head_ref: str = HEAD_REF,
    base_ref: str = BASE_REF,
    merged_at: str | None = None,
    repository: str = REPOSITORY,
) -> dict:
    return {
        "number": number,
        "state": state,
        "merged_at": merged_at,
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"full_name": repository},
        },
        "base": {
            "ref": base_ref,
            "repo": {"full_name": repository},
        },
    }


class Voc142RosterWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adopt = (ROOT / ".github/workflows/adopt.yml").read_text()

    def test_in_progress_ci_keeps_required_set_incomplete(self):
        result = evaluate_checks(
            [
                check_run(1, "governance-policy"),
                check_run(2, "validate"),
                check_run(
                    3,
                    "ci / ci",
                    status="in_progress",
                    conclusion="",
                    second=21,
                    details_url=(
                        f"https://github.com/{REPOSITORY}/actions/runs/33343147453/job/99342299218"
                    ),
                ),
            ]
        )
        snapshot = roster_wait_snapshot(result)
        self.assertFalse(snapshot["required_complete"])
        self.assertIn("ci / ci", snapshot["missing_required"])
        self.assertGreater(snapshot["pending"], 0)

    def test_two_stable_subset_snapshots_remain_incomplete_without_ci(self):
        subset = evaluate_checks(
            [
                check_run(1, "governance-policy"),
                check_run(2, "validate"),
            ]
        )
        first = roster_wait_snapshot(subset)
        second = roster_wait_snapshot(subset)
        for snapshot in (first, second):
            self.assertFalse(snapshot["required_complete"])
            self.assertEqual(snapshot["missing_required"], ["ci / ci"])
            self.assertEqual(snapshot["pending"], 0)

    def test_complete_required_set_is_success_only(self):
        complete = evaluate_checks(
            [
                check_run(1, "governance-policy"),
                check_run(2, "validate"),
                check_run(3, "ci / ci", second=16),
            ]
        )
        snapshot = roster_wait_snapshot(complete)
        self.assertTrue(snapshot["required_complete"])
        self.assertEqual(snapshot["missing_required"], [])
        self.assertTrue(roster_required_set_complete(complete))

    def test_missing_required_contexts_matches_ruleset_set(self):
        self.assertEqual(
            list(ROSTER_REQUIRED_CONTEXTS),
            ["ci / ci", "governance-policy", "validate"],
        )
        self.assertEqual(
            missing_required_roster_contexts(
                [
                    {"name": "governance-policy", "state": "SUCCESS"},
                    {"name": "validate", "state": "SUCCESS"},
                ]
            ),
            ["ci / ci"],
        )

    def test_roster_wait_runner_augments_authoritative_output(self):
        def fake_runner(command, check=False):
            output_index = command.index("--output") + 1
            Path(command[output_index]).write_text(
                json.dumps(
                    {
                        "total_count": 2,
                        "pending": 0,
                        "failed": 0,
                        "successful": 2,
                        "skipped": 0,
                        "checks": [
                            {"name": "governance-policy", "state": "SUCCESS"},
                            {"name": "validate", "state": "SUCCESS"},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return mock.Mock(returncode=0)

        with mock.patch.object(ROSTER_WAIT_RUNNER.subprocess, "run", side_effect=fake_runner):
            with tempfile_paths() as paths:
                argv = [
                    "roster-pr-wait-runner.py",
                    "--check-runs-file",
                    str(paths["check_runs"]),
                    "--statuses-file",
                    str(paths["statuses"]),
                    "--pull-request-file",
                    str(paths["pull_request"]),
                    "--repository",
                    REPOSITORY,
                    "--head-sha",
                    HEAD_SHA,
                    "--base-sha",
                    BASE_SHA,
                    "--pr-number",
                    str(PR_NUMBER),
                    "--output",
                    str(paths["output"]),
                    "--infra-config-dir",
                    str(ROOT / "config"),
                ]
                with mock.patch.object(sys, "argv", argv):
                    exit_code = ROSTER_WAIT_RUNNER.main()
                self.assertEqual(exit_code, 0)
                payload = json.loads(paths["output"].read_text(encoding="utf-8"))
                self.assertFalse(payload["required_complete"])
                self.assertEqual(payload["missing_required"], ["ci / ci"])


class Voc142RosterCarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adopt = (ROOT / ".github/workflows/adopt.yml").read_text()

    def test_exact_open_carrier_is_reused(self):
        result = resolve_roster_carrier(
            repository=REPOSITORY,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            base_ref=BASE_REF,
            open_pulls=[pr_record(PR_NUMBER)],
            merged_pulls=[],
        )
        self.assertEqual(result, RosterCarrierResult(action="reuse_open", pr_number=str(PR_NUMBER)))

    def test_exact_merged_carrier_is_reused_without_create(self):
        result = resolve_roster_carrier(
            repository=REPOSITORY,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            base_ref=BASE_REF,
            open_pulls=[],
            merged_pulls=[
                pr_record(
                    PR_NUMBER,
                    state="closed",
                    merged_at="2026-08-30T23:59:16Z",
                )
            ],
        )
        self.assertEqual(
            result,
            RosterCarrierResult(action="reuse_merged", pr_number=str(PR_NUMBER)),
        )

    def test_runner_queries_closed_rest_records_and_reuses_merged_carrier(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[
                [],
                [[pr_record(PR_NUMBER, state="closed", merged_at="2026-08-30T23:59:16Z")]],
            ],
        ) as gh_json:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 0)

            self.assertEqual(
                json.loads(paths["output"].read_text(encoding="utf-8")),
                {"action": "reuse_merged", "pr_number": str(PR_NUMBER)},
            )
            for call in gh_json.call_args_list:
                command = call.args[0]
                self.assertEqual(command[0:4], ["gh", "api", "-X", "GET"])
                self.assertNotIn(f"base={BASE_REF}", command)
            self.assertIn("state=closed", gh_json.call_args_list[1].args[0])

    def test_runner_rejects_same_head_carrier_with_mismatched_base(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[[[pr_record(PR_NUMBER, base_ref="main")]], []],
        ) as gh_json:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 1)

            for call in gh_json.call_args_list:
                self.assertNotIn(f"base={BASE_REF}", call.args[0])

    def test_roster_gate_keeps_exact_in_progress_pipeline_parent_visible(self):
        item = check_run(
            3,
            "ci / ci",
            status="in_progress",
            conclusion="",
            details_url=(
                f"https://github.com/{REPOSITORY}/actions/runs/33343147453/job/99342299218"
            ),
        )
        run = {
            "event": "pull_request",
            "repository": {"full_name": REPOSITORY},
            "head_sha": HEAD_SHA,
            "path": ".github/workflows/pipeline.yml",
            "head_branch": HEAD_REF,
            "status": "in_progress",
            "conclusion": None,
            "pull_requests": [pull_request()],
        }

        def fake_run(command, **_kwargs):
            if command[-1].endswith("/jobs?per_page=100"):
                return mock.Mock(returncode=0, stdout=json.dumps([{"jobs": []}]))
            return mock.Mock(returncode=0, stdout=json.dumps(run))

        with mock.patch.object(
            AUTHORITATIVE_CHECKS_RUNNER.subprocess, "run", side_effect=fake_run
        ):
            selected = AUTHORITATIVE_CHECKS_RUNNER._workflow_runs(
                [item],
                REPOSITORY,
                {"pull_request"},
                pr_number=PR_NUMBER,
                roster_pr_gate=True,
                pull_request=pull_request(),
            )
        self.assertEqual(selected, [item])
        self.assertEqual(selected[0]["run_id"], 33343147453)

    def test_zero_matches_may_create(self):
        result = resolve_roster_carrier(
            repository=REPOSITORY,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            base_ref=BASE_REF,
            open_pulls=[],
            merged_pulls=[],
        )
        self.assertEqual(result, RosterCarrierResult(action="create"))

    def test_ambiguous_open_carriers_fail_closed(self):
        result = resolve_roster_carrier(
            repository=REPOSITORY,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            base_ref=BASE_REF,
            open_pulls=[pr_record(1112), pr_record(1113)],
            merged_pulls=[],
        )
        self.assertEqual(result, RosterCarrierFailure("AMBIGUOUS_OPEN_CARRIER"))

    def test_mismatched_head_sha_fails_closed(self):
        result = resolve_roster_carrier(
            repository=REPOSITORY,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            base_ref=BASE_REF,
            open_pulls=[pr_record(PR_NUMBER, head_sha="a" * 40)],
            merged_pulls=[],
        )
        self.assertEqual(result, RosterCarrierFailure("MISMATCHED_OPEN_CARRIER"))

    def test_mismatched_base_ref_fails_closed(self):
        result = resolve_roster_carrier(
            repository=REPOSITORY,
            head_ref=HEAD_REF,
            head_sha=HEAD_SHA,
            base_ref=BASE_REF,
            open_pulls=[pr_record(PR_NUMBER, base_ref="main")],
            merged_pulls=[],
        )
        self.assertEqual(result, RosterCarrierFailure("MISMATCHED_OPEN_CARRIER"))

    def test_transient_metadata_lag_requires_matching_repo_ref_and_base(self):
        stale_open = [pr_record(PR_NUMBER, head_sha=STALE_HEAD_SHA)]
        self.assertTrue(
            open_carrier_metadata_lag_is_transient(
                repository=REPOSITORY,
                head_ref=HEAD_REF,
                head_sha=HEAD_SHA,
                base_ref=BASE_REF,
                open_pulls=stale_open,
            )
        )
        self.assertFalse(
            open_carrier_metadata_lag_is_transient(
                repository=REPOSITORY,
                head_ref=HEAD_REF,
                head_sha=HEAD_SHA,
                base_ref=BASE_REF,
                open_pulls=[pr_record(PR_NUMBER, head_sha=STALE_HEAD_SHA, base_ref="main")],
            )
        )
        self.assertFalse(
            open_carrier_metadata_lag_is_transient(
                repository=REPOSITORY,
                head_ref=HEAD_REF,
                head_sha=HEAD_SHA,
                base_ref=BASE_REF,
                open_pulls=[
                    pr_record(
                        PR_NUMBER,
                        head_sha=STALE_HEAD_SHA,
                        repository="other/repo",
                    )
                ],
            )
        )
        self.assertFalse(
            open_carrier_metadata_lag_is_transient(
                repository=REPOSITORY,
                head_ref=HEAD_REF,
                head_sha=HEAD_SHA,
                base_ref=BASE_REF,
                open_pulls=[
                    pr_record(1112, head_sha=STALE_HEAD_SHA),
                    pr_record(1113, head_sha=STALE_HEAD_SHA),
                ],
            )
        )

    def test_runner_reuses_open_carrier_after_stale_head_converges(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[
                [[pr_record(PR_NUMBER, head_sha=STALE_HEAD_SHA)]],
                [],
                [[pr_record(PR_NUMBER)]],
                [],
            ],
        ) as gh_json, mock.patch.object(ROSTER_CARRIER_RUNNER.time, "sleep"):
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 0)

            self.assertEqual(
                json.loads(paths["output"].read_text(encoding="utf-8")),
                {"action": "reuse_open", "pr_number": str(PR_NUMBER)},
            )
            self.assertEqual(len(gh_json.call_args_list), 4)

    def test_runner_immediate_exact_match_queries_once(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[
                [[pr_record(PR_NUMBER)]],
                [],
            ],
        ) as gh_json:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 0)

            self.assertEqual(len(gh_json.call_args_list), 2)

    def test_runner_stable_head_sha_mismatch_times_out(self):
        stale_pages = [[pr_record(PR_NUMBER, head_sha=STALE_HEAD_SHA)]]
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[
                stale_pages,
                [],
                stale_pages,
                [],
            ],
        ) as gh_json, mock.patch.object(
            ROSTER_CARRIER_RUNNER.time,
            "monotonic",
            side_effect=[0.0, 0.0, float(ROSTER_CARRIER_METADATA_CONVERGENCE_TIMEOUT_SECONDS + 1)],
        ), mock.patch.object(ROSTER_CARRIER_RUNNER.time, "sleep"):
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 1)

            self.assertGreaterEqual(len(gh_json.call_args_list), 4)

    def test_runner_api_failure_fails_closed_without_retry(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=RuntimeError("gh command failed"),
        ) as gh_json, mock.patch.object(ROSTER_CARRIER_RUNNER.time, "sleep") as sleep:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 1)

            self.assertEqual(len(gh_json.call_args_list), 1)
            sleep.assert_not_called()

    def test_runner_ambiguous_stale_open_carriers_fail_without_retry(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[
                [
                    [
                        pr_record(1112, head_sha=STALE_HEAD_SHA),
                        pr_record(1113, head_sha=STALE_HEAD_SHA),
                    ]
                ],
                [],
            ],
        ) as gh_json, mock.patch.object(ROSTER_CARRIER_RUNNER.time, "sleep") as sleep:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 1)

            self.assertEqual(len(gh_json.call_args_list), 2)
            sleep.assert_not_called()

    def test_runner_ambiguous_open_carrier_fails_without_retry(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[
                [[pr_record(1112), pr_record(1113)]],
                [],
            ],
        ) as gh_json, mock.patch.object(ROSTER_CARRIER_RUNNER.time, "sleep") as sleep:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 1)

            self.assertEqual(len(gh_json.call_args_list), 2)
            sleep.assert_not_called()

    def test_runner_mismatched_base_ref_fails_without_retry(self):
        with tempfile_paths() as paths, mock.patch.object(
            ROSTER_CARRIER_RUNNER,
            "gh_json",
            side_effect=[[[pr_record(PR_NUMBER, base_ref="main")]], []],
        ) as gh_json, mock.patch.object(ROSTER_CARRIER_RUNNER.time, "sleep") as sleep:
            argv = [
                "roster-carrier-runner.py",
                "--repository",
                REPOSITORY,
                "--head-ref",
                HEAD_REF,
                "--head-sha",
                HEAD_SHA,
                "--base-ref",
                BASE_REF,
                "--output",
                str(paths["output"]),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(ROSTER_CARRIER_RUNNER.main(), 1)

            self.assertEqual(len(gh_json.call_args_list), 2)
            sleep.assert_not_called()

    def test_adopt_open_step_resolves_carrier_before_create(self):
        open_step = self.adopt.split("- name: Open roster PR", 1)[1].split(
            "- name: Wait for roster PR checks", 1
        )[0]
        self.assertIn("roster-carrier-runner.py", open_step)
        self.assertIn("metadata to converge", self.adopt)
        self.assertIn("carrier_action", open_step)
        self.assertIn("reuse_open", open_step)
        self.assertIn("reuse_merged", open_step)
        create_branch = open_step.split("create)", 1)[0]
        self.assertNotIn("gh pr create", create_branch)

    def test_adopt_wait_requires_complete_required_set(self):
        roster_wait = self.adopt.split("- name: Wait for roster PR checks", 1)[1].split(
            "- name: Merge checked roster PR", 1
        )[0]
        self.assertIn("roster-pr-wait-runner.py", roster_wait)
        self.assertIn("--roster-pr-gate", (ROOT / "config" / "roster-pr-wait-runner.py").read_text())
        self.assertIn("required_complete", roster_wait)
        self.assertNotIn("gh pr checks", roster_wait)
        self.assertNotIn("statusCheckRollup", roster_wait)

    def test_reused_merged_carrier_skips_wait_and_merge(self):
        self.assertIn(
            "steps.roster-pr.outputs.carrier_action != 'reuse_merged'",
            self.adopt,
        )
        self.assertIn(
            "steps.roster-pr.outputs.carrier_action == 'reuse_merged'",
            self.adopt,
        )

    def test_root_dispatch_suppresses_duplicate_when_pr_exists(self):
        root_dispatch = self.adopt.split(
            "- name: Determine whether the root task needs dispatch", 1
        )[1].split("  implement-first-task:", 1)[0]
        self.assertIn('issue_state" = "OPEN', root_dispatch)
        self.assertIn('pr_count" = "0', root_dispatch)
        self.assertNotIn("steps.commit.outputs.changed", root_dispatch)


class tempfile_paths:
    def __enter__(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self.paths = {
            "check_runs": base / "check-runs.json",
            "statuses": base / "statuses.json",
            "pull_request": base / "pr.json",
            "output": base / "out.json",
            "authoritative": base / "out.authoritative.json",
        }
        for path in (self.paths["check_runs"], self.paths["statuses"], self.paths["pull_request"]):
            path.write_text("{}\n", encoding="utf-8")
        return self.paths

    def __exit__(self, exc_type, exc, tb):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
