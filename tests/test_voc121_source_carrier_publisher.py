"""Deterministic publish-source carrier tests for VOC-121-TEST-02."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/implement.yml").read_text(encoding="utf-8")


def run(command, *, cwd, env=None, check=True):
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        raise AssertionError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def git(cwd, *args):
    return run(["git", *args], cwd=cwd).stdout.strip()


def workflow_run_block(step_name):
    lines = WORKFLOW.splitlines()
    marker = f"- name: {step_name}"
    step_index = next(
        index for index, line in enumerate(lines) if line.strip() == marker
    )
    run_index = next(
        index
        for index in range(step_index + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    block = []
    for line in lines[run_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        block.append(line)
    return textwrap.dedent("\n".join(block))


class Voc121SourceCarrierPublisherTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        run(["git", "init", "--bare", str(self.remote)], cwd=self.root)
        run(["git", "init", "-b", "main", str(self.seed)], cwd=self.root)
        self.configure(self.seed)
        (self.seed / "README.md").write_text("integration\n")
        git(self.seed, "add", "README.md")
        git(self.seed, "commit", "-m", "integration base")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "-u", "origin", "main")
        self.integration_sha = git(self.seed, "rev-parse", "HEAD")

    def tearDown(self):
        self.scratch.cleanup()

    @staticmethod
    def configure(repository):
        git(repository, "config", "user.name", "Fixture")
        git(repository, "config", "user.email", "fixture@example.invalid")

    def clone(self, name):
        path = self.root / name
        run(["git", "clone", "--branch", "main", str(self.remote), str(path)], cwd=self.root)
        self.configure(path)
        return path

    def commit(self, repository, relative_path, content, message):
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(repository, "add", relative_path)
        git(repository, "commit", "-m", message)
        return git(repository, "rev-parse", "HEAD")

    def create_source_bundle(self, repository, base_sha, name):
        bundle = self.root / name
        head = git(repository, "rev-parse", "HEAD")
        branch = git(repository, "rev-parse", "--abbrev-ref", "HEAD")
        git(repository, "bundle", "create", str(bundle), f"{base_sha}..{branch}")
        return bundle, head

    def publish_source(
        self,
        *,
        bundle,
        branch,
        head,
        integration_sha,
        attempt,
        expected_source_head="",
    ):
        script = workflow_run_block(
            "Publish exact infrastructure bundle from an isolated bare repository"
        )
        script = script.replace(
            "bundle=/tmp/implementer-source-publish/implementer-source.bundle",
            f"bundle={bundle}",
        )
        script = script.replace("${{ inputs.attempt }}", str(attempt))
        script = script.replace(
            'remote="https://x-access-token:${PUBLISH_TOKEN}@github.com/${SOURCE_REPOSITORY}.git"',
            'remote="$FIXTURE_REMOTE"',
        )
        env = os.environ.copy()
        env.update(
            {
                "PUBLISH_TOKEN": "fixture",
                "PUBLISH_BRANCH": branch,
                "PUBLISH_HEAD_SHA": head,
                "PUBLISH_INTEGRATION_SHA": integration_sha,
                "EXPECTED_SOURCE_HEAD_SHA": expected_source_head,
                "FIXTURE_REMOTE": str(self.remote),
            }
        )
        return run(["bash", "-c", script], cwd=self.root, env=env, check=False)

    def make_source_attempt_one(self):
        work = self.clone("source-attempt-one")
        branch = "agent/voc-121-voc-121-t00"
        base_sha = git(work, "rev-parse", "HEAD")
        git(work, "checkout", "-b", branch)
        self.commit(work, "config/foo.py", "source change\n", "source carrier change")
        bundle, head = self.create_source_bundle(work, base_sha, "source.bundle")
        return branch, head, bundle, base_sha

    def advance_integration(self):
        new_sha = self.commit(
            self.seed, "integration.txt", "new integration\n", "advance integration"
        )
        git(self.seed, "push", "origin", "main")
        return new_sha

    def test_attempt_one_publishes_valid_source_bundle(self):
        branch, head, bundle, base_sha = self.make_source_attempt_one()
        completed = self.publish_source(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=base_sha,
            attempt=1,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            git(self.root, "--git-dir", str(self.remote), "rev-parse", branch), head
        )

    def test_missing_bundle_fails_closed(self):
        branch, head, _, base_sha = self.make_source_attempt_one()
        missing = self.root / "missing.bundle"
        completed = self.publish_source(
            bundle=missing,
            branch=branch,
            head=head,
            integration_sha=base_sha,
            attempt=1,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Nested source bundle is missing", completed.stderr)

    def test_stale_live_head_on_attempt_one_fails_closed(self):
        branch, head, bundle, base_sha = self.make_source_attempt_one()
        first = self.publish_source(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=base_sha,
            attempt=1,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        stale_repo = self.clone("stale-head")
        git(stale_repo, "checkout", "-B", branch)
        self.commit(stale_repo, "config/other.py", "other\n", "unexpected live head")
        git(stale_repo, "push", "--force", "origin", branch)

        completed = self.publish_source(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=base_sha,
            attempt=1,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("changed after implementer binding", completed.stderr)

    def test_remediation_updates_only_the_exact_bound_source_head(self):
        branch, head, bundle, base_sha = self.make_source_attempt_one()
        first = self.publish_source(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=base_sha,
            attempt=1,
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        remediation = self.clone("source-remediation")
        git(remediation, "checkout", "-B", branch, f"origin/{branch}")
        remediation_head = self.commit(
            remediation,
            "config/foo.py",
            "source remediation\n",
            "source carrier remediation",
        )
        remediation_bundle, _ = self.create_source_bundle(
            remediation, base_sha, "source-remediation.bundle"
        )
        completed = self.publish_source(
            bundle=remediation_bundle,
            branch=branch,
            head=remediation_head,
            integration_sha=base_sha,
            attempt=2,
            expected_source_head=head,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            git(self.root, "--git-dir", str(self.remote), "rev-parse", branch),
            remediation_head,
        )

    def test_unverifiable_lineage_fails_closed(self):
        branch, head, bundle, base_sha = self.make_source_attempt_one()
        advanced = self.advance_integration()
        completed = self.publish_source(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=advanced,
            attempt=1,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("authorized commit lineage", completed.stderr)

    def test_integration_history_race_fails_closed(self):
        branch, head, bundle, _base_sha = self.make_source_attempt_one()
        self.advance_integration()
        completed = self.publish_source(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha="f" * 40,
            attempt=1,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unverifiable bundle", completed.stderr)


if __name__ == "__main__":
    unittest.main()
