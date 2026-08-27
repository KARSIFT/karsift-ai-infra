"""Real-Git regression tests for the VOC-123 named source-bundle tip."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/implement.yml").read_text(encoding="utf-8")
PLAN_WORKFLOW = (ROOT / ".github/workflows/plan.yml").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "config"))

from implementer_source_carrier import (  # noqa: E402
    CarrierError,
    SOURCE_BUNDLE_REF,
    create_verified_source_bundle,
    verify_bundle_heads,
)


class Voc123SourceBundleTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.repository = self.root / "repository"
        self.git("init", "-b", "main", str(self.repository), cwd=self.root)
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repository / "policy.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "policy.txt")
        self.git("commit", "-m", "base")
        self.base_sha = self.git("rev-parse", "HEAD").stdout.strip()
        (self.repository / "policy.txt").write_text("change\n", encoding="utf-8")
        self.git("commit", "-am", "change")
        self.head_sha = self.git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self):
        self.scratch.cleanup()

    def git(self, *args: str, cwd: Path | None = None, check: bool = True):
        completed = subprocess.run(
            ["git", *(args if cwd is not None else ("-C", str(self.repository), *args))],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        if check and completed.returncode:
            self.fail(
                f"git {' '.join(args)} failed ({completed.returncode})\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return completed

    def create_bundle(self, name: str = "source.bundle") -> Path:
        bundle = self.root / name
        create_verified_source_bundle(
            repository=self.repository,
            base_sha=self.base_sha,
            head_sha=self.head_sha,
            bundle_path=bundle,
        )
        return bundle

    def test_raw_sha_positive_tip_reproduces_empty_bundle(self):
        completed = self.git(
            "bundle",
            "create",
            str(self.root / "raw.bundle"),
            f"{self.base_sha}..{self.head_sha}",
            check=False,
        )
        self.assertEqual(completed.returncode, 128)
        self.assertIn("Refusing to create empty bundle", completed.stderr)
        self.assertNotIn(
            '"${{ steps.infra-checkout.outputs.base_sha }}..$SOURCE_HEAD_SHA"',
            WORKFLOW,
        )

    def test_named_ref_bundle_advertises_only_exact_head_and_cleans_ref(self):
        self.git("branch", "unrelated", self.head_sha)
        self.git("tag", "unrelated-tag", self.head_sha)
        self.git("switch", "--orphan", "unrelated-history")
        (self.repository / "other.txt").write_text("other\n", encoding="utf-8")
        self.git("add", "other.txt")
        self.git("commit", "-m", "unrelated object")
        unrelated_sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("switch", "main")
        bundle = self.create_bundle()
        self.assertGreater(bundle.stat().st_size, 0)
        self.assertEqual(
            self.git("bundle", "list-heads", str(bundle)).stdout.splitlines(),
            [f"{self.head_sha} {SOURCE_BUNDLE_REF}"],
        )
        self.assertNotEqual(
            self.git(
                "show-ref",
                "--verify",
                "--quiet",
                SOURCE_BUNDLE_REF,
                check=False,
            ).returncode,
            0,
        )
        self.assertNotIn("refs/heads/unrelated", self.git("bundle", "list-heads", str(bundle)).stdout)
        self.assertNotIn("refs/tags/unrelated-tag", self.git("bundle", "list-heads", str(bundle)).stdout)
        receiver = self.root / "receiver.git"
        self.git("init", "--bare", str(receiver), cwd=self.root)
        self.git(
            "--git-dir",
            str(receiver),
            "fetch",
            str(self.repository),
            f"{self.base_sha}:refs/heads/main",
            cwd=self.root,
        )
        self.git(
            "--git-dir",
            str(receiver),
            "fetch",
            str(bundle),
            f"{self.head_sha}:refs/heads/carrier",
            cwd=self.root,
        )
        self.assertNotEqual(
            self.git(
                "--git-dir",
                str(receiver),
                "cat-file",
                "-e",
                unrelated_sha,
                cwd=self.root,
                check=False,
            ).returncode,
            0,
        )

    def test_missing_wrong_and_multiple_advertised_heads_fail_closed(self):
        missing = self.root / "missing.bundle"
        with self.assertRaisesRegex(CarrierError, "source_bundle_missing_or_empty"):
            verify_bundle_heads(
                repository=self.repository,
                bundle_path=missing,
                expected_head_sha=self.head_sha,
            )

        bundle = self.create_bundle()
        with self.assertRaisesRegex(CarrierError, "unexpected_source_bundle_heads"):
            verify_bundle_heads(
                repository=self.repository,
                bundle_path=bundle,
                expected_head_sha=self.base_sha,
            )

        self.git("branch", "second-head", self.head_sha)
        multiple = self.root / "multiple.bundle"
        self.git(
            "bundle",
            "create",
            str(multiple),
            f"{self.base_sha}..main",
            f"{self.base_sha}..second-head",
        )
        with self.assertRaisesRegex(CarrierError, "unexpected_source_bundle_heads"):
            verify_bundle_heads(
                repository=self.repository,
                bundle_path=multiple,
                expected_head_sha=self.head_sha,
            )

    def test_malformed_sha_and_wrong_prerequisite_fail_before_bundle(self):
        with self.assertRaisesRegex(CarrierError, "invalid_base_sha"):
            create_verified_source_bundle(
                repository=self.repository,
                base_sha="not-a-sha",
                head_sha=self.head_sha,
                bundle_path=self.root / "malformed.bundle",
            )
        with self.assertRaisesRegex(CarrierError, "invalid_head_sha"):
            create_verified_source_bundle(
                repository=self.repository,
                base_sha=self.base_sha,
                head_sha="not-a-sha",
                bundle_path=self.root / "malformed-head.bundle",
            )

        self.git("switch", "--orphan", "unrelated-history")
        (self.repository / "other.txt").write_text("other\n", encoding="utf-8")
        self.git("add", "other.txt")
        self.git("commit", "-m", "unrelated base")
        unrelated_sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("switch", "main")
        with self.assertRaisesRegex(CarrierError, "base_is_not_head_ancestor"):
            create_verified_source_bundle(
                repository=self.repository,
                base_sha=unrelated_sha,
                head_sha=self.head_sha,
                bundle_path=self.root / "wrong-base.bundle",
            )

    def test_verification_failure_removes_bundle_and_temporary_ref(self):
        bundle = self.root / "failed.bundle"
        with mock.patch(
            "implementer_source_carrier.verify_bundle_heads",
            side_effect=CarrierError("injected_verification_failure"),
        ):
            with self.assertRaisesRegex(CarrierError, "injected_verification_failure"):
                create_verified_source_bundle(
                    repository=self.repository,
                    base_sha=self.base_sha,
                    head_sha=self.head_sha,
                    bundle_path=bundle,
                )
        self.assertFalse(bundle.exists())
        self.assertNotEqual(
            self.git(
                "show-ref",
                "--verify",
                "--quiet",
                SOURCE_BUNDLE_REF,
                check=False,
            ).returncode,
            0,
        )

    def test_preexisting_temporary_ref_is_not_overwritten_or_deleted(self):
        self.git("update-ref", SOURCE_BUNDLE_REF, self.base_sha)
        with self.assertRaisesRegex(CarrierError, "temporary_ref_already_exists"):
            self.create_bundle()
        self.assertEqual(
            self.git("rev-parse", SOURCE_BUNDLE_REF).stdout.strip(),
            self.base_sha,
        )

    def test_caller_and_planner_head_ranges_advertise_expected_head(self):
        self.assertIn(
            'git bundle create /tmp/implementer-work.bundle '
            '"${{ steps.branch.outputs.integration_sha }}..HEAD"',
            WORKFLOW,
        )
        self.assertIn(
            'git bundle create /tmp/planner-work.bundle '
            '"${{ steps.branch.outputs.base_sha }}..HEAD"',
            PLAN_WORKFLOW,
        )
        for detached in (False, True):
            if detached:
                self.git("switch", "--detach", self.head_sha)
            for name in ("caller", "planner"):
                bundle = self.root / f"{name}-{detached}.bundle"
                self.git("bundle", "create", str(bundle), f"{self.base_sha}..HEAD")
                self.assertEqual(
                    self.git("bundle", "list-heads", str(bundle)).stdout.splitlines(),
                    [f"{self.head_sha} HEAD"],
                )

    def test_workflow_calls_verified_helper_and_preserves_publisher_contract(self):
        self.assertIn('python3 "$HELPER_DIR/implementer_source_carrier.py" \\', WORKFLOW)
        self.assertIn("create-bundle \\", WORKFLOW)
        self.assertIn('--head-sha "$SOURCE_HEAD_SHA"', WORKFLOW)
        self.assertIn("publish-source:", WORKFLOW)
        self.assertIn('git --git-dir="$git_dir" bundle verify "$bundle"', WORKFLOW)
        self.assertIn(
            '"$PUBLISH_HEAD_SHA:refs/heads/$PUBLISH_BRANCH"',
            WORKFLOW,
        )


if __name__ == "__main__":
    unittest.main()
