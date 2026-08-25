from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/implement.yml").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "config"))

from implementer_source_carrier import (  # noqa: E402
    CarrierError,
    build_source_pr_body,
    nested_worktree_has_changes,
    validate_no_gitlink_paths,
)
from prepare_cursor_model import CursorModelError, prepare_cursor_model  # noqa: E402


class Voc121ImplementPolicyTests(unittest.TestCase):
    @staticmethod
    def git(directory: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(directory), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_workflow_preserves_helpers_before_nested_removal(self):
        self.assertIn("HELPER_DIR=/tmp/karsift-implement-helpers", WORKFLOW)
        self.assertIn(
            'cp karsift-ai-infra/config/prepare_cursor_model.py "$HELPER_DIR/prepare_cursor_model.py"',
            WORKFLOW,
        )
        self.assertIn("/tmp/karsift-implement-helpers/prepare_cursor_model.py", WORKFLOW)
        self.assertNotIn(
            "merge-gate.yml fails closed (requires founder approval)",
            WORKFLOW,
        )

    def test_workflow_bundles_nested_edits_before_removal(self):
        self.assertIn("git -C karsift-ai-infra bundle create /tmp/implementer-source.bundle", WORKFLOW)
        self.assertIn("has_source_changes=true", WORKFLOW)
        self.assertIn("publish-source:", WORKFLOW)
        self.assertIn(
            'git -C karsift-ai-infra reset --soft \\\n'
            '            "${{ steps.source-branch.outputs.model_base_sha }}"',
            WORKFLOW,
        )

    def test_source_publisher_requires_app_token_without_caller_token_fallback(self):
        source_publisher = WORKFLOW[WORKFLOW.index("\n  publish-source:") :]
        self.assertIn(
            "Infrastructure publication requires GitHub App credentials",
            source_publisher,
        )
        self.assertIn(
            "PUBLISH_TOKEN: ${{ steps.app-token.outputs.token }}",
            source_publisher,
        )
        self.assertNotIn("|| github.token", source_publisher)

    def test_source_publisher_refuses_stale_or_racing_branch_heads(self):
        source_publisher = WORKFLOW[WORKFLOW.index("\n  publish-source:") :]
        self.assertIn(
            'if [ "$live_head" != "$EXPECTED_SOURCE_HEAD_SHA" ]',
            source_publisher,
        )
        self.assertIn(
            'lease="refs/heads/$PUBLISH_BRANCH:$EXPECTED_SOURCE_HEAD_SHA"',
            source_publisher,
        )
        self.assertIn(
            '--force-with-lease="$lease"',
            source_publisher,
        )
        self.assertIn(
            '"$PUBLISH_INTEGRATION_SHA" "$PUBLISH_HEAD_SHA"',
            source_publisher,
        )
        self.assertIn(
            "Bind nested infrastructure carrier to its exact remote head",
            WORKFLOW,
        )
        self.assertIn(
            'if [ "$fetched_head" != "$live_head" ]',
            WORKFLOW,
        )
        self.assertIn(
            'git -C karsift-ai-infra rebase',
            WORKFLOW,
        )

    def test_exact_source_lease_rejects_a_racing_remote_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin.git"
            seed = root / "seed"
            publisher = root / "publisher"
            racer = root / "racer"
            branch = "agent/voc-121-voc-121-t00"

            subprocess.run(
                ["git", "init", "--bare", str(origin)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "init", str(seed)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.git(seed, "config", "user.name", "test")
            self.git(seed, "config", "user.email", "test@example.invalid")
            (seed / "carrier.txt").write_text("main\n", encoding="utf-8")
            self.git(seed, "add", "carrier.txt")
            self.git(seed, "commit", "-m", "main")
            self.git(seed, "branch", "-M", "main")
            self.git(seed, "remote", "add", "origin", str(origin))
            self.git(seed, "push", "origin", "main")
            self.git(seed, "checkout", "-b", branch)
            (seed / "carrier.txt").write_text("attempt one\n", encoding="utf-8")
            self.git(seed, "commit", "-am", "attempt one")
            self.git(seed, "push", "origin", branch)
            expected = self.git(seed, "rev-parse", "HEAD").stdout.strip()

            for clone in (publisher, racer):
                subprocess.run(
                    ["git", "clone", "--branch", branch, str(origin), str(clone)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.git(clone, "config", "user.name", "test")
                self.git(clone, "config", "user.email", "test@example.invalid")

            (publisher / "carrier.txt").write_text(
                "publisher update\n", encoding="utf-8"
            )
            self.git(publisher, "commit", "-am", "publisher update")
            publish_head = self.git(publisher, "rev-parse", "HEAD").stdout.strip()

            (racer / "carrier.txt").write_text("racing update\n", encoding="utf-8")
            self.git(racer, "commit", "-am", "racing update")
            self.git(racer, "push", "origin", branch)
            racing_head = self.git(racer, "rev-parse", "HEAD").stdout.strip()

            push = self.git(
                publisher,
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected}",
                "origin",
                f"{publish_head}:refs/heads/{branch}",
                check=False,
            )
            self.assertNotEqual(push.returncode, 0)
            live = self.git(
                publisher, "ls-remote", "--heads", "origin", branch
            ).stdout.split()[0]
            self.assertEqual(live, racing_head)

    def test_nested_gitlink_paths_are_rejected(self):
        with self.assertRaises(CarrierError):
            validate_no_gitlink_paths(["karsift-ai-infra"])

    def test_source_pr_body_is_non_closing_cross_repo_reference(self):
        body = build_source_pr_body(
            authority_repository="KARSIFT/vocanova-platform-sandbox",
            issue_number=996,
            change_id="VOC-121",
            task_id="VOC-121-T00",
            attempt=1,
        )
        self.assertIn("Relates to KARSIFT/vocanova-platform-sandbox#996.", body)
        self.assertNotRegex(body, r"(?i)\bcloses\b")

    def test_prepare_cursor_model_runs_from_preserved_copy_after_deletion(self):
        scratch = tempfile.TemporaryDirectory()
        helper_dir = Path(scratch.name) / "helpers"
        helper_dir.mkdir()
        helper_script = helper_dir / "prepare_cursor_model.py"
        helper_script.write_text(
            (ROOT / "config/prepare_cursor_model.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.pop("CURSOR_API_KEY", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(helper_script),
                "--require-api-key",
                "cursor/composer-2.5",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing_cursor_api_key", completed.stderr)
        self.assertNotIn("CURSOR_API_KEY", completed.stderr)

    def test_preserved_helper_still_validates_cursor_models(self):
        self.assertEqual(
            prepare_cursor_model("cursor/composer-2.5"),
            "composer-2.5",
        )
        with self.assertRaises(CursorModelError):
            prepare_cursor_model("opencode-go/foo")

    def test_nested_change_detection(self):
        self.assertTrue(nested_worktree_has_changes(" M config/foo.py\n"))
        self.assertFalse(nested_worktree_has_changes(""))

    def tearDown(self):
        if hasattr(self, "_scratch"):
            self._scratch.cleanup()


if __name__ == "__main__":
    unittest.main()
