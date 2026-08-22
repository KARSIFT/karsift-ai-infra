import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/implement.yml").read_text()


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


class ImplementerBundleTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        run(["git", "init", "--bare", str(self.remote)], cwd=self.root)
        run(["git", "init", "-b", "develop", str(self.seed)], cwd=self.root)
        self.configure(self.seed)
        (self.seed / "README.md").write_text("integration\n")
        git(self.seed, "add", "README.md")
        git(self.seed, "commit", "-m", "integration base")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "-u", "origin", "develop")
        self.integration_sha = git(self.seed, "rev-parse", "HEAD")

    def tearDown(self):
        self.scratch.cleanup()

    @staticmethod
    def configure(repository):
        git(repository, "config", "user.name", "Fixture")
        git(repository, "config", "user.email", "fixture@example.invalid")

    def clone(self, name):
        path = self.root / name
        run(["git", "clone", "--branch", "develop", str(self.remote), str(path)], cwd=self.root)
        self.configure(path)
        return path

    def commit(self, repository, relative_path, content, message):
        target = repository / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(repository, "add", relative_path)
        git(repository, "commit", "-m", message)
        return git(repository, "rev-parse", "HEAD")

    def create_workflow_bundle(self, repository, integration_sha, name):
        bundle = self.root / name
        output = self.root / f"{name}.output"
        output.touch()
        script = workflow_run_block("Bundle committed work as a recovery artifact")
        script = script.replace(
            "${{ steps.branch.outputs.integration_sha }}", integration_sha
        ).replace("/tmp/implementer-work.bundle", str(bundle))
        env = os.environ.copy()
        env["GITHUB_OUTPUT"] = str(output)
        completed = run(["bash", "-c", script], cwd=repository, env=env)
        self.assertIn(
            f"head_sha={git(repository, 'rev-parse', 'HEAD')}",
            output.read_text(),
        )
        return bundle

    def publish(self, *, bundle, branch, head, integration_sha, attempt, old_head=""):
        script = workflow_run_block(
            "Publish exact bundled commit from an isolated bare repository"
        )
        script = script.replace(
            "bundle=/tmp/implementer-publish/implementer-work.bundle",
            f"bundle={bundle}",
        )
        script = script.replace("${{ inputs.integration_branch }}", "develop")
        script = script.replace("${{ inputs.attempt }}", str(attempt))
        script = script.replace(
            'remote="https://x-access-token:${PUBLISH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"',
            'remote="$FIXTURE_REMOTE"',
        )
        env = os.environ.copy()
        env.update(
            {
                "PUBLISH_TOKEN": "fixture",
                "PUBLISH_BRANCH": branch,
                "PUBLISH_HEAD_SHA": head,
                "PUBLISH_INTEGRATION_SHA": integration_sha,
                "EXPECTED_OLD_HEAD": old_head,
                "FIXTURE_REMOTE": str(self.remote),
            }
        )
        return run(["bash", "-c", script], cwd=self.root, env=env, check=False)

    def make_attempt_one(self):
        work = self.clone("attempt-one")
        branch = "agent/voc-test-voc-test-t00"
        git(work, "checkout", "-b", branch)
        head = self.commit(work, "task.txt", "attempt one\n", "task change")
        bundle = self.create_workflow_bundle(work, self.integration_sha, "attempt-one.bundle")
        return work, branch, head, bundle

    def advance_integration(self):
        new_sha = self.commit(
            self.seed, "integration.txt", "new integration\n", "advance integration"
        )
        git(self.seed, "push", "origin", "develop")
        return new_sha

    def test_attempt_one_bundle_verifies_and_publishes_from_clean_bare_repo(self):
        _, branch, head, bundle = self.make_attempt_one()
        completed = self.publish(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=self.integration_sha,
            attempt=1,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            git(self.root, "--git-dir", str(self.remote), "rev-parse", branch), head
        )

    def test_remediation_bundle_contains_locally_rebased_prior_commits(self):
        _, branch, first_head, first_bundle = self.make_attempt_one()
        first = self.publish(
            bundle=first_bundle,
            branch=branch,
            head=first_head,
            integration_sha=self.integration_sha,
            attempt=1,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        integration_sha = self.advance_integration()

        remediation = self.clone("remediation")
        git(remediation, "fetch", "origin", branch)
        git(remediation, "checkout", "-B", branch, f"origin/{branch}")
        git(remediation, "rebase", "origin/develop")
        rebased_pre_model = git(remediation, "rev-parse", "HEAD")
        self.assertNotEqual(rebased_pre_model, first_head)
        head = self.commit(
            remediation, "task.txt", "attempt two\n", "remediation change"
        )
        bundle = self.create_workflow_bundle(
            remediation, integration_sha, "attempt-two.bundle"
        )

        completed = self.publish(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=integration_sha,
            attempt=2,
            old_head=first_head,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            git(self.root, "--git-dir", str(self.remote), "rev-parse", branch), head
        )

    def test_old_pre_model_thin_bundle_fails_clean_verification(self):
        work, branch, first_head, first_bundle = self.make_attempt_one()
        first = self.publish(
            bundle=first_bundle,
            branch=branch,
            head=first_head,
            integration_sha=self.integration_sha,
            attempt=1,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        integration_sha = self.advance_integration()
        git(work, "fetch", "origin", "develop")
        git(work, "rebase", "origin/develop")
        pre_model = git(work, "rev-parse", "HEAD")
        head = self.commit(work, "task.txt", "attempt two\n", "remediation change")
        incomplete = self.root / "incomplete.bundle"
        git(work, "bundle", "create", str(incomplete), f"{pre_model}..HEAD")

        completed = self.publish(
            bundle=incomplete,
            branch=branch,
            head=head,
            integration_sha=integration_sha,
            attempt=2,
            old_head=first_head,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Repository lacks these prerequisite commits", completed.stderr)

    def test_workflow_deny_scans_prior_rebased_task_commits(self):
        work = self.clone("workflow-remediation")
        branch = "agent/voc-test-voc-test-t00"
        git(work, "checkout", "-b", branch)
        old_head = self.commit(
            work,
            ".github/workflows/untrusted.yml",
            "name: untrusted\n",
            "prior workflow change",
        )
        git(work, "push", "origin", branch)
        integration_sha = self.advance_integration()
        git(work, "fetch", "origin", "develop")
        git(work, "rebase", "origin/develop")
        (work / ".github/workflows/untrusted.yml").unlink()
        git(work, "add", ".github/workflows/untrusted.yml")
        git(work, "commit", "-m", "remove prior workflow change")
        head = self.commit(work, "safe.txt", "safe\n", "safe remediation")
        bundle = self.create_workflow_bundle(work, integration_sha, "workflow.bundle")

        completed = self.publish(
            bundle=bundle,
            branch=branch,
            head=head,
            integration_sha=integration_sha,
            attempt=2,
            old_head=old_head,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cannot publish workflow-file changes", completed.stderr)
        self.assertEqual(
            git(self.root, "--git-dir", str(self.remote), "rev-parse", branch),
            old_head,
        )


if __name__ == "__main__":
    unittest.main()
