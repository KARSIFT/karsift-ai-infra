import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AppCheckContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (ROOT / "config/run-app-checks.sh").read_text()
        cls.ci = (ROOT / ".github/workflows/ci.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()

    def test_runner_binds_exact_pr_context_without_fetching_evidence(self):
        self.assertIn('--pr-base-sha SHA --pr-head-sha SHA', self.runner)
        self.assertIn('git cat-file -e "${validation_base_sha}^{commit}"', self.runner)
        self.assertIn('git merge-base "$validation_base_sha" "$validation_head_sha"', self.runner)
        self.assertIn('validation_mode="pr-validation"', self.runner)
        self.assertIn('validation_mode="pr-ancestry"', self.runner)
        self.assertIn('export PR_BASE_SHA="$validation_base_sha"', self.runner)
        self.assertNotIn("git fetch", self.runner)

    def test_fixture_changes_select_strict_ancestry(self):
        fixture = "scripts/foundation/fixtures/voc112-navigation-benchmark-traces.json"
        self.assertIn(f'capture_fixture="{fixture}"', self.runner)
        self.assertIn(
            'git diff --quiet "$validation_base_sha" "$validation_head_sha" -- "$capture_fixture"',
            self.runner,
        )

    def _run_fixture_transition(self, transition):
        fixture = Path(
            "scripts/foundation/fixtures/voc112-navigation-benchmark-traces.json"
        )
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
            target = repository / fixture
            target.parent.mkdir(parents=True)
            if transition != "added":
                target.write_text('{"capture":"base"}\n')
            else:
                (repository / "base.txt").write_text("base")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"], cwd=repository, check=True
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()

            if transition == "modified":
                target.write_text('{"capture":"head"}\n')
            elif transition == "deleted":
                target.unlink()
            elif transition == "added":
                target.write_text('{"capture":"head"}\n')
            elif transition != "unchanged":
                self.fail(f"unknown transition: {transition}")
            unrelated = repository / "unrelated.txt"
            unrelated.write_text(transition)
            subprocess.run(["git", "add", "-A"], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", transition],
                cwd=repository,
                check=True,
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
                ],
                cwd=repository,
                check=True,
                text=True,
                capture_output=True,
            )
            return result.stdout

    def test_unchanged_fixture_uses_pr_validation(self):
        self.assertIn(
            "application-check provenance mode: pr-validation",
            self._run_fixture_transition("unchanged"),
        )

    def test_modified_fixture_uses_pr_ancestry(self):
        self.assertIn(
            "application-check provenance mode: pr-ancestry",
            self._run_fixture_transition("modified"),
        )

    def test_added_fixture_uses_pr_ancestry(self):
        self.assertIn(
            "application-check provenance mode: pr-ancestry",
            self._run_fixture_transition("added"),
        )

    def test_deleted_fixture_uses_pr_ancestry(self):
        self.assertIn(
            "application-check provenance mode: pr-ancestry",
            self._run_fixture_transition("deleted"),
        )

    def test_fixture_diff_error_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(
                ["git", "config", "user.name", "test"], cwd=repository, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repository,
                check=True,
            )
            (repository / "base.txt").write_text("base")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"], cwd=repository, check=True
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            (repository / "head.txt").write_text("head")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "head"], cwd=repository, check=True
            )
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()

            wrappers = Path(directory) / "bin"
            wrappers.mkdir()
            wrapper = wrappers / "git"
            wrapper.write_text(
                "#!/usr/bin/env bash\n"
                'if [ "$1" = "diff" ]; then exit 2; fi\n'
                f'exec "{shutil.which("git")}" "$@"\n'
            )
            wrapper.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{wrappers}:{environment['PATH']}"
            result = subprocess.run(
                [
                    "bash",
                    str(ROOT / "config/run-app-checks.sh"),
                    "--pr-base-sha",
                    base,
                    "--pr-head-sha",
                    head,
                ],
                cwd=repository,
                text=True,
                capture_output=True,
                env=environment,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr.strip(), "capture fixture comparison failed")

    def test_ci_passes_event_exact_shas_and_recovery_mode(self):
        checkout = self.ci.split(
            "- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            1,
        )[1].split("- name: Checkout karsift-ai-infra", 1)[0]
        self.assertIn("fetch-depth: 0", checkout)
        self.assertIn('EVENT_BASE_SHA: ${{ github.event.pull_request.base.sha }}', self.ci)
        self.assertIn('EVENT_HEAD_SHA: ${{ github.event.pull_request.head.sha }}', self.ci)
        self.assertIn('--pr-base-sha "$EVENT_BASE_SHA"', self.ci)
        self.assertIn('--pr-head-sha "$EVENT_HEAD_SHA"', self.ci)
        self.assertIn('run-app-checks.sh --squash-safe-push', self.ci)

    def test_implementer_uses_integration_anchor_and_live_committed_head(self):
        self.assertGreaterEqual(
            self.implement.count(
                '--pr-base-sha "${{ steps.branch.outputs.integration_sha }}"'
            ),
            2,
        )
        self.assertGreaterEqual(
            self.implement.count('--pr-head-sha "$(git rev-parse HEAD)"'),
            2,
        )


if __name__ == "__main__":
    unittest.main()
