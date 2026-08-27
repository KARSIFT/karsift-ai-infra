"""Behavioral tests for the release caller-checkout fallback."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "config" / "release-checkout-ref-runner.py"
SPEC = importlib.util.spec_from_file_location("release_checkout_ref_runner", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


SHA = "a" * 40


class ReleaseCheckoutRefRunnerTests(unittest.TestCase):
    def test_existing_integration_is_selected_without_reading_production(self):
        with mock.patch.object(
            runner,
            "_gh_json",
            return_value=[
                {"ref": "refs/heads/develop", "object": {"type": "commit", "sha": SHA}}
            ],
        ) as api:
            self.assertEqual(
                runner.resolve_checkout_ref("KARSIFT/caller", "develop", "main"),
                "develop",
            )
        api.assert_called_once_with(
            "repos/KARSIFT/caller/git/matching-refs/heads/develop"
        )

    def test_absent_integration_falls_back_to_live_production(self):
        with mock.patch.object(
            runner,
            "_gh_json",
            side_effect=[[], {"object": {"type": "commit", "sha": SHA}}],
        ) as api:
            self.assertEqual(
                runner.resolve_checkout_ref("KARSIFT/caller", "develop", "main"),
                "main",
            )
        self.assertEqual(
            [call.args[0] for call in api.call_args_list],
            [
                "repos/KARSIFT/caller/git/matching-refs/heads/develop",
                "repos/KARSIFT/caller/git/ref/heads/main",
            ],
        )

    def test_ambiguous_or_malformed_refs_fail_closed(self):
        ambiguous = [
            {"ref": "refs/heads/develop", "object": {"type": "commit", "sha": SHA}},
            {"ref": "refs/heads/develop", "object": {"type": "commit", "sha": SHA}},
        ]
        with mock.patch.object(runner, "_gh_json", return_value=ambiguous):
            with self.assertRaisesRegex(runner.ResolutionError, "integration_ref_ambiguous"):
                runner.resolve_checkout_ref("KARSIFT/caller", "develop", "main")
        with mock.patch.object(
            runner,
            "_gh_json",
            side_effect=[[], {"object": {"type": "commit", "sha": "not-a-sha"}}],
        ):
            with self.assertRaisesRegex(
                runner.ResolutionError, "production_ref_identity_invalid"
            ):
                runner.resolve_checkout_ref("KARSIFT/caller", "develop", "main")

    def test_github_failure_does_not_replay_raw_error(self):
        completed = subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="secret raw error"
        )
        with mock.patch.object(runner.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(runner.ResolutionError, "github_ref_lookup_failed") as error:
                runner._gh_json("repos/KARSIFT/caller/git/matching-refs/heads/develop")
        self.assertNotIn("secret", str(error.exception))

    def test_cli_writes_only_the_selected_ref(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with mock.patch.object(runner, "resolve_checkout_ref", return_value="main"):
                with mock.patch(
                    "sys.argv",
                    [
                        str(RUNNER_PATH),
                        "--repository",
                        "KARSIFT/caller",
                        "--integration-branch",
                        "develop",
                        "--production-branch",
                        "main",
                        "--github-output",
                        str(output),
                    ],
                ):
                    self.assertEqual(runner.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "ref=main\n")


if __name__ == "__main__":
    unittest.main()
