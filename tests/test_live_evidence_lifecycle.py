from importlib.util import module_from_spec, spec_from_file_location
import json
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
        cls.plan_review_workflow = (
            ROOT / ".github/workflows/plan-review.yml"
        ).read_text()
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
        script = script.replace(
            "PYTHONPATH=karsift-ai-infra/config", f"PYTHONPATH={ROOT / 'config'}"
        )
        script = script.replace("${{ inputs.pr_number }}", "1")
        script = script.replace("${{ github.event.pull_request.number }}", "1")
        script = script.replace("${{ inputs.expected_head_sha }}", "")
        script = script.replace("${{ inputs.expected_base_sha }}", "")
        script = script.replace("${{ inputs.reuse_outcome }}", "full-path")
        script = script.replace("${{ inputs.reuse_prior_run_id }}", "")
        script = script.replace("${{ inputs.current_ci_result }}", "success")
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
                env["EXPECTED_HEAD_SHA"] = ""
                env["EXPECTED_BASE_SHA"] = ""
                env["GITHUB_REPOSITORY"] = "KARSIFT/fixture"
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

    def test_clean_implement_publisher_opens_pr_outside_git_worktree(self):
        self.assertEqual(
            self.implement_workflow.count('gh issue comment --repo "$GH_REPO"'),
            3,
        )
        script = self._run_block(
            self.implement_workflow,
            "Open or update PR from the clean runner",
        )
        replacements = {
            "${{ inputs.task_id }}": "VOC-TEST-T00",
            "${{ inputs.change_id }}": "VOC-TEST",
            "${{ inputs.package_path }}": "specs/changes/VOC-TEST-fixture",
            "${{ inputs.issue_number }}": "1",
            "${{ inputs.attempt }}": "1",
            "${{ inputs.integration_branch }}": "develop",
        }
        for original, replacement in replacements.items():
            script = script.replace(original, replacement)

        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            bin_path = scratch_path / "bin"
            bin_path.mkdir()
            invocation_log = scratch_path / "gh-invocations"
            update_payload = scratch_path / "update-payload.json"
            gh_stub = bin_path / "gh"
            gh_stub.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    printf '%s %s\\n' "$1" "$2" >> {invocation_log}
                    case "$1 $2" in
                      "pr list")
                        [[ " $* " == *" --repo KARSIFT/fixture "* ]] || exit 81
                        printf '%s\\n' "${{EXISTING_PR:-}}"
                        ;;
                      "pr create"|"pr comment")
                        [[ " $* " == *" --repo KARSIFT/fixture "* ]] || exit 81
                        ;;
                      "api --method")
                        [[ " $* " == *" PATCH repos/KARSIFT/fixture/pulls/42 --input - "* ]] || exit 83
                        cat > {update_payload}
                        ;;
                      *) exit 82 ;;
                    esac
                    """
                )
            )
            gh_stub.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "GH_REPO": "KARSIFT/fixture",
                    "PUBLISH_BRANCH": "agent/voc-test-voc-test-t00",
                    "PACKAGE_RISK": "R1",
                    "PATH": f"{bin_path}:{env['PATH']}",
                }
            )
            cases = [
                ("", ["pr list", "pr create"]),
                ("42", ["pr list", "api --method", "pr comment"]),
            ]
            for existing_pr, expected in cases:
                with self.subTest(existing_pr=existing_pr):
                    invocation_log.write_text("")
                    env["EXISTING_PR"] = existing_pr
                    completed = subprocess.run(
                        ["bash", "-c", script],
                        cwd=scratch_path,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(
                        invocation_log.read_text().splitlines(), expected
                    )
                    if existing_pr:
                        payload = json.loads(update_payload.read_text())
                        self.assertIn("Implements task `VOC-TEST-T00`", payload["body"])

        self.assertNotIn("gh pr edit", script)

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
            self.plan_review_workflow,
            self.remediate_workflow,
            self.merge_workflow,
        ):
            expected_head_block = "\n".join(
                workflow.split("expected_head_sha:", 1)[1].splitlines()[:5]
            )
            self.assertIn("required: false", expected_head_block)
            self.assertIn('default: ""', expected_head_block)

        self.assertIn(
            "Caller omitted or supplied an invalid expected PR base/head SHA; refusing to run plan review.",
            self.plan_review_workflow,
        )
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
            self.plan_review_workflow,
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

        plan_review_result, _ = self._execute_missing_sha_path(
            self.plan_review_workflow,
            "Fetch PR diff, metadata, and exact SHA",
        )
        self.assertNotEqual(plan_review_result.returncode, 0)
        self.assertIn("refusing to run plan review", plan_review_result.stderr)

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
        reuse_block = self.pipeline_template.split(
            "  ready-for-review-reuse:", 1
        )[1].split("\n  ci:", 1)[0]
        self.assertIn("github.event.pull_request.head.sha || github.sha", reuse_block)
        self.assertIn("github.event.pull_request.base.sha || github.sha", reuse_block)


if __name__ == "__main__":
    unittest.main()
