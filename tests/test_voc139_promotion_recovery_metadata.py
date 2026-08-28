"""VOC-139 infrastructure regressions for promotion metadata and hash export."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "templates/project-repo/.github/workflows/pipeline.yml"

VALID_PAYLOAD = {
    "state": "open",
    "base": {
        "ref": "main",
        "sha": "0" * 40,
        "repo": {"full_name": "KARSIFT/vocanova-platform-sandbox"},
    },
    "head": {
        "ref": "develop",
        "sha": "1" * 40,
        "repo": {"full_name": "KARSIFT/vocanova-platform-sandbox"},
    },
    "headRepository": {"id": 1, "name": "vocanova-platform-sandbox"},
}


class Voc139PromotionRecoveryMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pipeline = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        steps = pipeline["jobs"]["promotion-pr-metadata"]["steps"]
        cls.metadata_script = next(
            step["run"]
            for step in steps
            if step.get("name") == "Resolve immutable promotion PR metadata"
        )

    def _run_metadata(
        self,
        payload,
        *,
        repository="KARSIFT/vocanova-platform-sandbox",
        gh_exit=0,
    ):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            gh_path = workspace / "gh"
            gh_path.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'expected="repos/${GITHUB_REPOSITORY}/pulls/${PROMOTION_PR_NUMBER}"\n'
                'if [ "$#" -ne 2 ] || [ "$1" != "api" ] || '
                '[ "$2" != "$expected" ]; then\n'
                "  exit 99\n"
                "fi\n"
                'if [ "$GH_FIXTURE_EXIT" -ne 0 ]; then\n'
                '  exit "$GH_FIXTURE_EXIT"\n'
                "fi\n"
                "printf '%s\\n' \"$GH_FIXTURE_PAYLOAD\"\n"
            )
            gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{workspace}:{environment['PATH']}",
                    "PROMOTION_PR_NUMBER": "1090",
                    "GH_TOKEN": "fixture-token",
                    "GH_FIXTURE_PAYLOAD": json.dumps(payload),
                    "GH_FIXTURE_EXIT": str(gh_exit),
                    "GITHUB_OUTPUT": str(workspace / "github-output"),
                }
            )
            if repository is None:
                environment.pop("GITHUB_REPOSITORY", None)
            else:
                environment["GITHUB_REPOSITORY"] = repository
            result = subprocess.run(
                ["bash", "-c", self.metadata_script],
                cwd=workspace,
                text=True,
                capture_output=True,
                env=environment,
            )
            output_path = Path(environment["GITHUB_OUTPUT"])
            result.github_output = (
                output_path.read_text(encoding="utf-8")
                if output_path.exists()
                else ""
            )
            return result

    def test_metadata_succeeds_in_non_git_directory(self):
        result = self._run_metadata(VALID_PAYLOAD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"pr_base_sha={'0' * 40}", result.github_output)
        self.assertIn(f"pr_head_sha={'1' * 40}", result.github_output)

    def test_metadata_rejects_same_name_fork(self):
        fork_payload = json.loads(json.dumps(VALID_PAYLOAD))
        fork_payload["head"]["repo"]["full_name"] = "evil-fork/vocanova-platform-sandbox"
        result = self._run_metadata(fork_payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion pair mismatch", result.stderr)

    def test_metadata_rejects_wrong_base_repository(self):
        wrong_base = json.loads(json.dumps(VALID_PAYLOAD))
        wrong_base["base"]["repo"]["full_name"] = "other/vocanova-platform-sandbox"
        result = self._run_metadata(wrong_base)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion pair mismatch", result.stderr)

    def test_metadata_rejects_missing_repository_identity(self):
        for side in ("base", "head"):
            with self.subTest(side=side):
                missing = json.loads(json.dumps(VALID_PAYLOAD))
                del missing[side]["repo"]["full_name"]
                result = self._run_metadata(missing)
                self.assertEqual(result.returncode, 1)
                self.assertIn("promotion pair mismatch", result.stderr)

    def test_metadata_rejects_closed_pr(self):
        closed_payload = json.loads(json.dumps(VALID_PAYLOAD))
        closed_payload["state"] = "closed"
        result = self._run_metadata(closed_payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion PR is not open", result.stderr)

    def test_metadata_rejects_wrong_refs(self):
        for side, value in (("base", "production"), ("head", "feature")):
            with self.subTest(side=side):
                wrong_refs = json.loads(json.dumps(VALID_PAYLOAD))
                wrong_refs[side]["ref"] = value
                result = self._run_metadata(wrong_refs)
                self.assertEqual(result.returncode, 1)
                self.assertIn("promotion pair mismatch", result.stderr)

    def test_metadata_rejects_malformed_sha(self):
        for side in ("base", "head"):
            with self.subTest(side=side):
                malformed = json.loads(json.dumps(VALID_PAYLOAD))
                malformed[side]["sha"] = "short"
                result = self._run_metadata(malformed)
                self.assertEqual(result.returncode, 1)
                self.assertIn(
                    "promotion PR metadata missing immutable SHAs", result.stderr
                )

    def test_metadata_requires_explicit_repository_context(self):
        result = self._run_metadata(VALID_PAYLOAD, repository=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GITHUB_REPOSITORY", result.stderr)

    def test_metadata_fails_closed_when_pr_lookup_is_missing(self):
        result = self._run_metadata({}, gh_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.github_output, "")

    def test_extracted_pipeline_metadata_uses_supported_rest_fields(self):
        pipeline_text = self.metadata_script
        self.assertIn(
            'gh api "repos/$GITHUB_REPOSITORY/pulls/$PROMOTION_PR_NUMBER"',
            pipeline_text,
        )
        self.assertNotIn("headRepository.nameWithOwner", pipeline_text)

    def test_promotion_rejects_divergent_base_and_head(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(
                ["git", "config", "user.name", "test"], cwd=repository, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repository,
                check=True,
            )
            (repository / "root.txt").write_text("root\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "root"], cwd=repository, check=True
            )
            root = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            (repository / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"], cwd=repository, check=True
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            subprocess.run(
                ["git", "checkout", "-q", "--detach", root],
                cwd=repository,
                check=True,
            )
            (repository / "head.txt").write_text("head\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "head"], cwd=repository, check=True
            )
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "config/run-app-checks.sh"),
                    "--pr-base-sha",
                    base,
                    "--pr-head-sha",
                    head,
                    "--promotion-pr",
                ],
                cwd=repository,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "promotion PR base must be an ancestor of its head", result.stderr
            )


if __name__ == "__main__":
    unittest.main()
