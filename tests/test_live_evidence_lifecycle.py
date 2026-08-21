from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
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

    @staticmethod
    def _run_block(workflow, step_name):
        lines = workflow.splitlines()
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

    def _execute_missing_sha_path(self, workflow, step_name):
        script = self._run_block(workflow, step_name)
        script = script.replace("${{ inputs.pr_number }}", "1")
        script = script.replace("${{ github.event.pull_request.number }}", "1")
        script = script.replace("${{ inputs.expected_head_sha }}", "")
        script = script.replace("${{ inputs.expected_base_sha }}", "")
        gh_stub = """
        gh() {
          if [ "$1 $2 $3" = "pr view 1" ]; then
            printf '%s\\n' '{"body":"Risk classification: R1","title":"fixture","author":{"login":"fixture"},"headRefOid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","baseRefOid":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","isDraft":false}'
            return 0
          fi
          printf 'unexpected gh invocation: %s\\n' "$*" >&2
          return 97
        }
        """
        with tempfile.TemporaryDirectory() as scratch:
            script = script.replace("/tmp/pr.json", f"{scratch}/pr.json")
            script = script.replace("/tmp/pr.diff", f"{scratch}/pr.diff")
            with tempfile.NamedTemporaryFile(dir=scratch) as output:
                env = os.environ.copy()
                env["GITHUB_OUTPUT"] = output.name
                completed = subprocess.run(
                    ["bash", "-c", textwrap.dedent(gh_stub) + script],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                output.seek(0)
                return completed, output.read().decode()

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

    def test_ready_for_review_rechecks_unchanged_draft_sha(self):
        self.assertIn(
            "types: [opened, synchronize, reopened, ready_for_review, closed]",
            self.pipeline_template,
        )
        self.assertIn("github.event.action != 'closed'", self.pipeline_template)

    def test_code_and_ci_failures_retry_but_review_infrastructure_does_not(self):
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
            "REVIEW_INFRA_FAILURE",
        )
        self.assertEqual(
            decider.decide(
                **common,
                review_state="FAIL",
                ci_failed=False,
                review_job_failed=True,
            ),
            "RETRY",
            "an existing exact-SHA signed FAIL remains actionable",
        )
        self.assertEqual(
            decider.decide(
                **common,
                review_state="PENDING",
                ci_failed=True,
                review_job_failed=True,
            ),
            "RETRY",
            "a real CI failure remains actionable even if review infrastructure also failed",
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

    def test_omitted_sha_is_transition_compatible_but_runtime_fail_closed(self):
        for workflow in (
            self.review_workflow,
            self.remediate_workflow,
            self.merge_workflow,
        ):
            expected_head_block = "\n".join(
                workflow.split("expected_head_sha:", 1)[1].splitlines()[:5]
            )
            self.assertIn("required: false", expected_head_block)
            self.assertIn('default: ""', expected_head_block)

        self.assertIn(
                "Caller omitted or supplied an invalid expected PR base/head SHA. Skipping reviewer model invocation.",
            self.review_workflow,
        )
        self.assertIn(
            'if [ "$head_state" != "CURRENT" ]; then',
            self.remediate_workflow,
        )
        self.assertIn(
                "Caller omitted or supplied an invalid expected PR base/head SHA. Refusing to reuse checks or review state.",
            self.merge_workflow,
        )
        for workflow in (
            self.review_workflow,
            self.remediate_workflow,
            self.merge_workflow,
        ):
            self.assertNotIn("${expected:-live}", workflow)

        review_result, review_output = self._execute_missing_sha_path(
            self.review_workflow,
            "Fetch PR diff, metadata, and exact SHA",
        )
        self.assertEqual(review_result.returncode, 0, review_result.stderr)
        self.assertIn("stale=true", review_output)
        self.assertIn("Skipping reviewer model invocation", review_result.stdout)

        merge_result, merge_output = self._execute_missing_sha_path(
            self.merge_workflow,
            "Determine risk class, checks, and verification status",
        )
        self.assertEqual(merge_result.returncode, 0, merge_result.stderr)
        self.assertIn("checks_ok=false", merge_output)
        self.assertIn("verdict=PENDING", merge_output)
        self.assertIn("Refusing to reuse checks", merge_result.stdout)

    def test_retry_revalidates_head_and_uses_explicit_atomic_lease(self):
        self.assertIn(
            "expected_head_sha: ${{ inputs.expected_head_sha }}",
            self.remediate_workflow,
        )
        self.assertGreaterEqual(
            self.implement_workflow.count("verify-expected-head.py"), 1
        )
        self.assertIn('[ "$live_head" != "$EXPECTED_OLD_HEAD" ]', self.implement_workflow)
        self.assertIn(
            '--force-with-lease="$lease"',
            self.implement_workflow,
        )

    def test_stale_review_skips_model_invocation(self):
        self.assertIn("expected_head_sha:", self.review_workflow)
        self.assertIn("expected_base_sha:", self.review_workflow)
        self.assertIn('echo "stale=true"', self.review_workflow)
        self.assertNotIn("gh pr diff", self.review_workflow)
        self.assertIn(
            "git --no-pager diff --no-ext-diff --no-textconv --find-renames",
            self.review_workflow,
        )
        self.assertIn("baseRefOid,state", self.review_workflow)
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
            "cancel-in-progress: ${{ github.event_name == 'pull_request' && github.event.action != 'closed' }}",
            self.pipeline_template,
        )
        self.assertEqual(
            self.pipeline_template.count(
                "expected_head_sha: ${{ github.event.pull_request.head.sha }}"
            ),
            4,
        )
        self.assertEqual(
            self.pipeline_template.count(
                "expected_base_sha: ${{ github.event.pull_request.base.sha }}"
            ),
            4,
        )


if __name__ == "__main__":
    unittest.main()
