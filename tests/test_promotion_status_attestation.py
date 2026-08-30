from __future__ import annotations

import sys
import json
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from promotion_status_attestation import (  # noqa: E402
    AttestationError,
    EXPECTED_WORKFLOWS,
    attestable_contexts,
)


def load_runner():
    path = ROOT / "config/promotion-status-attestation-runner.py"
    spec = spec_from_file_location("promotion_status_attestation_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner module from {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def summary(*, override: dict | None = None) -> dict:
    checks = [
        {
            "name": context,
            "state": "SUCCESS",
            "kind": "check_run",
            "workflow": workflow,
            "run_id": index,
            "conclusion": "success",
        }
        for index, (context, workflow) in enumerate(EXPECTED_WORKFLOWS.items(), 1)
    ]
    if override:
        checks[0].update(override)
    return {"checks": checks}


class PromotionStatusAttestationTests(unittest.TestCase):
    def test_accepts_only_the_three_expected_successful_workflows(self):
        self.assertEqual(
            attestable_contexts(summary()),
            (("governance-policy", 1), ("validate", 2), ("ci / ci", 3)),
        )

    def test_rejects_failed_foreign_or_non_actions_evidence(self):
        for override in (
            {"state": "FAILURE"},
            {"conclusion": "neutral"},
            {"workflow": ".github/workflows/foreign.yml"},
            {"kind": "status"},
            {"run_id": 0},
        ):
            with self.subTest(override=override):
                with self.assertRaises(AttestationError):
                    attestable_contexts(summary(override=override))

    def test_rejects_missing_or_duplicate_required_context(self):
        missing = summary()
        missing["checks"].pop()
        with self.assertRaises(AttestationError):
            attestable_contexts(missing)
        duplicate = summary()
        duplicate["checks"].append(dict(duplicate["checks"][0]))
        with self.assertRaises(AttestationError):
            attestable_contexts(duplicate)

    def test_runner_revalidates_pr_and_publishes_only_required_contexts(self):
        head_sha = "a" * 40
        repository = "KARSIFT/example"

        def completed(command, **kwargs):
            joined = " ".join(command)
            result = mock.Mock(returncode=0, stderr="")
            if command[-1] == f"repos/{repository}/pulls/947":
                result.stdout = json.dumps(
                    {
                        "number": 947,
                        "state": "open",
                        "base": {
                            "sha": "b" * 40,
                            "ref": "main",
                            "repo": {"full_name": repository},
                        },
                        "head": {
                            "sha": head_sha,
                            "ref": "develop",
                            "repo": {"full_name": repository},
                        },
                    }
                )
            elif joined.startswith("gh pr checks"):
                result.stdout = json.dumps(
                    [
                        {"name": "governance-policy", "state": "SUCCESS"},
                        {"name": "validate", "state": "SUCCESS"},
                        {"name": "ci / ci", "state": "SUCCESS"},
                    ]
                )
            elif "/actions/runs/3/jobs?per_page=100" in joined:
                result.stdout = json.dumps(
                    [
                        {
                            "jobs": [
                                {
                                    "name": "release / converge",
                                    "conclusion": "skipped",
                                }
                            ]
                        }
                    ]
                )
            elif "/actions/runs/" in joined:
                run_id = int(command[-1].rsplit("/", 1)[-1])
                workflow = list(EXPECTED_WORKFLOWS.values())[run_id - 1]
                event = "pull_request"
                if run_id == 3:
                    event = "workflow_dispatch"
                display_title = (
                    "promotion-pr-validation PR #947"
                    if event == "workflow_dispatch"
                    else "pipeline"
                )
                result.stdout = json.dumps(
                    {
                        "id": run_id,
                        "event": event,
                        "path": workflow,
                        "display_title": display_title,
                        "status": "completed",
                        "conclusion": "success",
                        "head_sha": head_sha,
                        "head_branch": "develop",
                        "repository": {"full_name": repository},
                        "pull_requests": [
                            {
                                "number": 947,
                                "base": {
                                    "sha": "b" * 40,
                                    "ref": "main",
                                    "repo": {"full_name": repository},
                                },
                                "head": {
                                    "sha": head_sha,
                                    "ref": "develop",
                                    "repo": {"full_name": repository},
                                },
                            }
                        ],
                    }
                )
            else:
                result.stdout = "{}"
            return result

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "authoritative.json"
            evidence.write_text(json.dumps(summary()), encoding="utf-8")
            argv = [
                "promotion-status-attestation-runner.py",
                "--authoritative-file",
                str(evidence),
                "--repository",
                repository,
                "--pr-number",
                "947",
                "--head-sha",
                head_sha,
                "--branch-ref",
                "develop",
                "--target-url",
                f"https://github.com/{repository}/actions/runs/123",
                "--github-token",
                "job-token",
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                runner.subprocess, "run", side_effect=completed
            ) as run_mock:
                self.assertEqual(runner.main(), 0)

        posts = [
            call
            for call in run_mock.call_args_list
            if "--method" in call.args[0]
        ]
        self.assertEqual(len(posts), 3)
        payloads = [json.loads(call.kwargs["input"]) for call in posts]
        self.assertEqual(
            [payload["context"] for payload in payloads],
            ["governance-policy", "validate", "ci / ci"],
        )
        self.assertTrue(all(payload["state"] == "success" for payload in payloads))
        self.assertTrue(
            all(call.kwargs["env"]["GH_TOKEN"] == "job-token" for call in posts)
        )
        job_reads = [
            call
            for call in run_mock.call_args_list
            if "/actions/runs/3/jobs?per_page=100" in " ".join(call.args[0])
        ]
        self.assertEqual(len(job_reads), 1)
        self.assertIn("--paginate", job_reads[0].args[0])
        self.assertIn("--slurp", job_reads[0].args[0])

    def test_runner_fails_closed_when_required_pr_view_cannot_be_read(self):
        head_sha = "a" * 40
        repository = "KARSIFT/example"

        def completed(command, **kwargs):
            result = mock.Mock(returncode=0, stdout="{}", stderr="")
            if command[:3] == ["gh", "pr", "checks"]:
                result.returncode = 1
                result.stderr = "untrusted provider detail"
            return result

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "authoritative.json"
            evidence.write_text(json.dumps(summary()), encoding="utf-8")
            argv = [
                "promotion-status-attestation-runner.py",
                "--authoritative-file",
                str(evidence),
                "--repository",
                repository,
                "--pr-number",
                "947",
                "--head-sha",
                head_sha,
                "--branch-ref",
                "develop",
                "--target-url",
                f"https://github.com/{repository}/actions/runs/123",
                "--github-token",
                "job-token",
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                runner.subprocess, "run", side_effect=completed
            ) as run_mock:
                self.assertEqual(runner.main(), 1)

        self.assertFalse(
            any("--method" in call.args[0] for call in run_mock.call_args_list)
        )

    def test_runner_passes_fetched_jobs_to_ci_semantics_verification(self):
        head_sha = "a" * 40
        repository = "KARSIFT/example"
        jobs = [{"name": "release / converge", "conclusion": "skipped"}]
        pull_request = {
            "number": 947,
            "state": "open",
            "base": {
                "sha": "b" * 40,
                "ref": "main",
                "repo": {"full_name": repository},
            },
            "head": {
                "sha": head_sha,
                "ref": "develop",
                "repo": {"full_name": repository},
            },
        }

        def api(_token, _repository, endpoint, **kwargs):
            if endpoint.endswith("/pulls/947"):
                return pull_request
            if "/actions/runs/" in endpoint:
                return {}
            if "/statuses/" in endpoint and kwargs.get("method") == "POST":
                return {}
            raise AssertionError(f"unexpected endpoint: {endpoint}")

        checks_result = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"name": "governance-policy", "state": "SUCCESS"},
                    {"name": "validate", "state": "SUCCESS"},
                    {"name": "ci / ci", "state": "SUCCESS"},
                ]
            ),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "authoritative.json"
            evidence.write_text(json.dumps(summary()), encoding="utf-8")
            argv = [
                "promotion-status-attestation-runner.py",
                "--authoritative-file",
                str(evidence),
                "--repository",
                repository,
                "--pr-number",
                "947",
                "--head-sha",
                head_sha,
                "--branch-ref",
                "develop",
                "--target-url",
                f"https://github.com/{repository}/actions/runs/123",
                "--github-token",
                "job-token",
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                runner.subprocess, "run", return_value=checks_result
            ), mock.patch.object(runner, "gh_api", side_effect=api), mock.patch.object(
                runner, "gh_api_paginated_jobs", return_value=jobs
            ) as jobs_mock, mock.patch.object(
                runner, "verify_promotion_required_run_semantics"
            ) as verify_mock:
                self.assertEqual(runner.main(), 0)

        jobs_mock.assert_called_once_with("job-token", repository, 3)
        ci_calls = [
            call
            for call in verify_mock.call_args_list
            if call.kwargs["context"] == "ci / ci"
        ]
        self.assertEqual(len(ci_calls), 1)
        self.assertEqual(ci_calls[0].kwargs["jobs"], jobs)

    def test_runner_parses_nonzero_failed_check_payload_but_never_attests_it(self):
        head_sha = "a" * 40
        repository = "KARSIFT/example"

        def completed(command, **kwargs):
            result = mock.Mock(returncode=0, stdout="{}", stderr="")
            if command[:3] == ["gh", "pr", "checks"]:
                result.returncode = 1
                result.stdout = json.dumps(
                    [{"name": "governance-policy", "state": "FAILURE"}]
                )
            return result

        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "authoritative.json"
            evidence.write_text(json.dumps(summary()), encoding="utf-8")
            argv = [
                "promotion-status-attestation-runner.py",
                "--authoritative-file",
                str(evidence),
                "--repository",
                repository,
                "--pr-number",
                "947",
                "--head-sha",
                head_sha,
                "--branch-ref",
                "develop",
                "--target-url",
                f"https://github.com/{repository}/actions/runs/123",
                "--github-token",
                "job-token",
            ]
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                runner.subprocess, "run", side_effect=completed
            ) as run_mock:
                self.assertEqual(runner.main(), 1)

        self.assertFalse(
            any("--method" in call.args[0] for call in run_mock.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
