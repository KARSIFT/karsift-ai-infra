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
            result = mock.Mock(returncode=0, stderr="")
            if command[-1] == f"repos/{repository}/pulls/947":
                result.stdout = json.dumps(
                    {
                        "number": 947,
                        "state": "open",
                        "head": {"sha": head_sha, "ref": "develop"},
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


if __name__ == "__main__":
    unittest.main()
