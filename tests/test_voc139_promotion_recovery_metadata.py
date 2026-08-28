"""VOC-139 infrastructure regressions for promotion metadata and hash export."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "templates/project-repo/.github/workflows/pipeline.yml"

METADATA_SCRIPT = """
set -euo pipefail
payload=$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PROMOTION_PR_NUMBER")
base_ref=$(jq -r .base.ref <<<"$payload")
head_ref=$(jq -r .head.ref <<<"$payload")
base_repo=$(jq -r .base.repo.full_name <<<"$payload")
head_repo=$(jq -r .head.repo.full_name <<<"$payload")
state=$(jq -r .state <<<"$payload")
base_sha=$(jq -r .base.sha <<<"$payload")
head_sha=$(jq -r .head.sha <<<"$payload")
if [ "$state" != "open" ]; then
  echo "promotion PR is not open" >&2
  exit 1
fi
if [ "$base_ref" != "main" ] || [ "$head_ref" != "develop" ] ||
   [ "$base_repo" != "$GITHUB_REPOSITORY" ] ||
   [ "$head_repo" != "$GITHUB_REPOSITORY" ]; then
  echo "promotion pair mismatch" >&2
  exit 1
fi
if ! [[ "$base_sha" =~ ^[0-9a-f]{40}$ ]] ||
   ! [[ "$head_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "promotion PR metadata missing immutable SHAs" >&2
  exit 1
fi
echo "pr_base_sha=$base_sha"
echo "pr_head_sha=$head_sha"
"""

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
    def _run_metadata(self, payload, *, repository="KARSIFT/vocanova-platform-sandbox"):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            gh_path = workspace / "gh"
            gh_path.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" != "api" ]; then exit 99; fi\n'
                f"cat <<'EOF'\n{json.dumps(payload)}\nEOF\n"
            )
            gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{workspace}:{environment['PATH']}",
                    "GITHUB_REPOSITORY": repository,
                    "PROMOTION_PR_NUMBER": "1090",
                    "GH_TOKEN": "fixture-token",
                }
            )
            return subprocess.run(
                ["bash", "-c", METADATA_SCRIPT],
                cwd=workspace,
                text=True,
                capture_output=True,
                env=environment,
            )

    def test_metadata_succeeds_in_non_git_directory(self):
        result = self._run_metadata(VALID_PAYLOAD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"pr_base_sha={'0' * 40}", result.stdout)
        self.assertIn(f"pr_head_sha={'1' * 40}", result.stdout)

    def test_metadata_rejects_same_name_fork(self):
        fork_payload = json.loads(json.dumps(VALID_PAYLOAD))
        fork_payload["head"]["repo"]["full_name"] = "evil-fork/vocanova-platform-sandbox"
        result = self._run_metadata(fork_payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion pair mismatch", result.stderr)

    def test_metadata_rejects_closed_pr(self):
        closed_payload = json.loads(json.dumps(VALID_PAYLOAD))
        closed_payload["state"] = "closed"
        result = self._run_metadata(closed_payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion PR is not open", result.stderr)

    def test_metadata_rejects_wrong_refs(self):
        wrong_refs = json.loads(json.dumps(VALID_PAYLOAD))
        wrong_refs["head"]["ref"] = "feature"
        result = self._run_metadata(wrong_refs)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion pair mismatch", result.stderr)

    def test_metadata_rejects_malformed_sha(self):
        malformed = json.loads(json.dumps(VALID_PAYLOAD))
        malformed["head"]["sha"] = "short"
        result = self._run_metadata(malformed)
        self.assertEqual(result.returncode, 1)
        self.assertIn("promotion PR metadata missing immutable SHAs", result.stderr)

    def test_pipeline_metadata_matches_extracted_script(self):
        pipeline_text = PIPELINE.read_text(encoding="utf-8")
        self.assertIn(
            'gh api "repos/$GITHUB_REPOSITORY/pulls/$PROMOTION_PR_NUMBER"',
            pipeline_text,
        )
        self.assertNotIn("headRepository.nameWithOwner", pipeline_text)


if __name__ == "__main__":
    unittest.main()
