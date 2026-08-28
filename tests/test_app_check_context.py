import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AppCheckContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (ROOT / "config/run-app-checks.sh").read_text()
        cls.ci = (ROOT / ".github/workflows/ci.yml").read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.self_ci = (ROOT / ".github/workflows/self-ci.yml").read_text()
        cls.pipeline = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def test_runner_binds_exact_pr_context_without_fetching_evidence(self):
        self.assertIn('--pr-base-sha SHA --pr-head-sha SHA', self.runner)
        self.assertIn('--promotion-pr', self.runner)
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

    def _run_fixture_transition(self, transition, *, promotion_pr=False):
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
            command = [
                "bash",
                str(ROOT / "config/run-app-checks.sh"),
                "--pr-base-sha",
                base,
                "--pr-head-sha",
                head,
            ]
            if promotion_pr:
                command.append("--promotion-pr")
            result = subprocess.run(
                command,
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

    def test_promotion_modified_fixture_uses_pr_validation(self):
        self.assertIn(
            "application-check provenance mode: pr-validation",
            self._run_fixture_transition("modified", promotion_pr=True),
        )

    def test_promotion_requires_one_exact_non_conflicting_comparison(self):
        cases = (
            ["--promotion-pr"],
            ["--promotion-pr", "--squash-safe-push"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "config/run-app-checks.sh"),
                        *arguments,
                    ],
                    cwd=directory,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(
                    result.stderr.strip(),
                    "promotion PR validation requires one non-conflicting exact base/head pair",
                )

    def test_resolvable_nonancestor_fixture_subject_keeps_paths_separate(self):
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
            (repository / "subject.txt").write_text("nonancestor")
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "subject"],
                cwd=repository,
                check=True,
            )
            subject = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()

            subprocess.run(
                ["git", "checkout", "-q", "--orphan", "comparison"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "rm", "-q", "--cached", "subject.txt"],
                cwd=repository,
                check=True,
            )
            (repository / "subject.txt").unlink()
            target = repository / fixture
            target.parent.mkdir(parents=True)
            target.write_text(f'{{"subject_revision":"{subject}","capture":"base"}}\n')
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"], cwd=repository, check=True
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            target.write_text(f'{{"subject_revision":"{subject}","capture":"head"}}\n')
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "head"], cwd=repository, check=True
            )
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()

            common = [
                "bash",
                str(ROOT / "config/run-app-checks.sh"),
                "--pr-base-sha",
                base,
                "--pr-head-sha",
                head,
            ]
            ordinary = subprocess.run(
                common, cwd=repository, check=True, text=True, capture_output=True
            )
            promotion = subprocess.run(
                [*common, "--promotion-pr"],
                cwd=repository,
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn("provenance mode: pr-ancestry", ordinary.stdout)
            self.assertIn("provenance mode: pr-validation", promotion.stdout)
            self.assertEqual(
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", subject, head],
                    cwd=repository,
                ).returncode,
                1,
            )

    def test_promotion_fixture_change_ignores_missing_subject(self):
        fixture = Path(
            "scripts/foundation/fixtures/voc112-navigation-benchmark-traces.json"
        )
        missing_subject = "f9d11e232a07c7d7a9c433d02c9267912543ba10"
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
            target.write_text(
                '{"capture":"base","subject_revision":"'
                + missing_subject
                + '"}\n'
            )
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "base"], cwd=repository, check=True
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repository, text=True
            ).strip()
            target.write_text(
                '{"capture":"head","subject_revision":"'
                + missing_subject
                + '"}\n'
            )
            (repository / "unrelated.txt").write_text("promotion")
            subprocess.run(["git", "add", "-A"], cwd=repository, check=True)
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
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn(
                "application-check provenance mode: pr-validation",
                result.stdout,
            )
            self.assertFalse(
                subprocess.run(
                    ["git", "cat-file", "-e", f"{missing_subject}^{{commit}}"],
                    cwd=repository,
                    capture_output=True,
                ).returncode == 0
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
        self.assertIn('EVENT_BASE_REF: ${{ github.event.pull_request.base.ref }}', self.ci)
        self.assertIn('EVENT_HEAD_REF: ${{ github.event.pull_request.head.ref }}', self.ci)
        self.assertIn('EVENT_HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}', self.ci)
        self.assertIn('INPUT_PROMOTION_PR: ${{ inputs.promotion_pr }}', self.ci)
        self.assertIn(
            "promotion PR validation requires immutable base/head metadata", self.ci
        )
        self.assertIn('--pr-base-sha "$EVENT_BASE_SHA"', self.ci)
        self.assertIn('--pr-head-sha "$EVENT_HEAD_SHA"', self.ci)
        self.assertIn('--promotion-pr', self.ci)
        self.assertIn('run-app-checks.sh --squash-safe-push', self.ci)

    def test_recovery_ci_requires_successful_exact_pr_metadata(self):
        parsed_pipeline = yaml.safe_load(self.pipeline)
        self.assertEqual(
            parsed_pipeline["run-name"],
            "${{ github.event_name == 'workflow_dispatch' && "
            "inputs.action == 'recover-promotion-pr-checks' && "
            "format('promotion-pr-validation PR #{0}', "
            "inputs.promotion_pr_number) || github.event.pull_request.title || "
            "github.workflow }}",
        )
        self.assertIn(
            "format('promotion-pr-validation PR #{0}', inputs.promotion_pr_number)",
            self.pipeline,
        )
        self.assertIn(
            "templates/project-repo/.github/workflows/*.yml", self.self_ci
        )
        self.assertIn(
            "inputs.action == 'recover-promotion-pr-checks' && "
            "needs.promotion-pr-metadata.result == 'success'",
            self.pipeline,
        )

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
