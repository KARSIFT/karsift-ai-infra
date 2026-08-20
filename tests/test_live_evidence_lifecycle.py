from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


classifier = load_module(
    "classify_review_verdict",
    ROOT / "config/classify-review-verdict.py",
)
decider = load_module(
    "decide_remediation",
    ROOT / "config/decide-remediation.py",
)
head_guard = load_module(
    "verify_expected_head",
    ROOT / "config/verify-expected-head.py",
)


class LiveEvidenceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review_workflow = (ROOT / ".github/workflows/review.yml").read_text()
        cls.remediate_workflow = (
            ROOT / ".github/workflows/remediate.yml"
        ).read_text()
        cls.implement_workflow = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.merge_workflow = (ROOT / ".github/workflows/merge-gate.yml").read_text()
        cls.pipeline_template = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def test_verdict_fixture_matrix_is_fail_dominant(self):
        waiting = "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE"
        failure = "VERDICT: FAIL"
        self.assertEqual(classifier.classify(waiting), "WAITING")
        self.assertEqual(classifier.classify(failure), "FAIL")
        self.assertEqual(classifier.classify(f"{waiting}\n{failure}"), "FAIL")
        self.assertEqual(classifier.classify(f"{failure}\n{waiting}"), "FAIL")
        self.assertEqual(classifier.classify("no machine verdict"), "PENDING")

    def test_waiting_does_not_retry(self):
        self.assertEqual(
            decider.decide(
                expected_sha="a" * 40,
                current_sha="a" * 40,
                review_state="WAITING",
                ci_failed=False,
                review_job_failed=False,
            ),
            "WAITING",
        )

    def test_genuine_and_infrastructure_failures_retry(self):
        common = {"expected_sha": "a" * 40, "current_sha": "a" * 40}
        self.assertEqual(
            decider.decide(
                **common,
                review_state="FAIL",
                ci_failed=False,
                review_job_failed=False,
            ),
            "RETRY",
        )
        self.assertEqual(
            decider.decide(
                **common,
                review_state="WAITING",
                ci_failed=True,
                review_job_failed=False,
            ),
            "RETRY",
        )
        self.assertEqual(
            decider.decide(
                **common,
                review_state="PENDING",
                ci_failed=False,
                review_job_failed=True,
            ),
            "RETRY",
        )

    def test_stale_run_never_retries_even_when_failed(self):
        self.assertEqual(
            decider.decide(
                expected_sha="a" * 40,
                current_sha="b" * 40,
                review_state="FAIL",
                ci_failed=True,
                review_job_failed=True,
            ),
            "STALE",
        )

    def test_exact_head_guard_rejects_missing_invalid_and_changed_heads(self):
        sha_a = "a" * 40
        sha_b = "b" * 40
        self.assertEqual(head_guard.verify("", sha_a), "INVALID_EXPECTED_SHA")
        self.assertEqual(head_guard.verify("not-a-sha", sha_a), "INVALID_EXPECTED_SHA")
        self.assertEqual(head_guard.verify(sha_a, ""), "INVALID_CURRENT_SHA")
        self.assertEqual(head_guard.verify(sha_a, sha_b), "STALE")
        self.assertEqual(head_guard.verify(sha_a, sha_a), "CURRENT")

    def test_exact_sha_is_mandatory_at_reusable_boundaries(self):
        for workflow in (
            self.review_workflow,
            self.remediate_workflow,
            self.merge_workflow,
        ):
            expected_head_block = "\n".join(
                workflow.split("expected_head_sha:", 1)[1].splitlines()[:4]
            )
            self.assertIn("required: true", expected_head_block)
            self.assertNotIn('default: ""', expected_head_block)

    def test_retry_revalidates_head_and_uses_explicit_atomic_lease(self):
        self.assertIn(
            "expected_head_sha: ${{ inputs.expected_head_sha }}",
            self.remediate_workflow,
        )
        self.assertGreaterEqual(
            self.implement_workflow.count("verify-expected-head.py"), 2
        )
        self.assertIn(
            '--force-with-lease="refs/heads/$branch:$expected_head"',
            self.implement_workflow,
        )

    def test_stale_review_skips_model_invocation(self):
        self.assertIn("expected_head_sha:", self.review_workflow)
        self.assertIn('echo "stale=true"', self.review_workflow)
        self.assertRegex(
            self.review_workflow,
            r"- name: Run independent verification\n\s+if: steps\.pr\.outputs\.stale != 'true'",
        )

    def test_caller_template_cancels_superseded_pr_runs_and_binds_sha(self):
        self.assertIn(
            "group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}",
            self.pipeline_template,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.pipeline_template,
        )
        self.assertEqual(
            self.pipeline_template.count(
                "expected_head_sha: ${{ github.event.pull_request.head.sha }}"
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
