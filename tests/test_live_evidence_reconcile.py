from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = load_module(
    "live_evidence_reconcile",
    ROOT / "config/live_evidence_reconcile.py",
)
runner = load_module(
    "live_evidence_reconcile_runner",
    ROOT / "config/live-evidence-reconcile-runner.py",
)


NOW = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
RUN_SHA = "b" * 40
BASE = "c" * 40
PACKAGE = "specs/changes/VOC-097-example"


def contract_text(**overrides):
    values = {
        "workflow_file": "deploy-production.yml",
        "job_names": "  - deploy-production\n  - verify-production",
        "events": "  - push",
        "branch": "main",
        "lineage": "  mode: exact_pr_head",
        "max_age": "72h",
        "dispatch": "",
    }
    values.update(overrides)
    return f"""schema_version: 1
task_id: VOC-097-T02
ownership: operator
workflow_file: {values['workflow_file']}
job_names:
{values['job_names']}
events:
{values['events']}
branch: {values['branch']}
sha_lineage:
{values['lineage']}
conclusion: success
max_age: {values['max_age']}
{values['dispatch']}"""


def parsed_contract(**overrides):
    return policy.validate_contract(
        policy.parse_contract_yaml(contract_text(**overrides)),
        "VOC-097-T02",
    )


def run_fixture(**overrides):
    run = {
        "id": 12345,
        "workflow_id": 91,
        "name": "deploy-production",
        "path": ".github/workflows/deploy-production.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": HEAD,
        "conclusion": "success",
        "run_started_at": "2026-08-20T23:55:00Z",
        "updated_at": "2026-08-20T23:59:00Z",
    }
    run.update(overrides)
    return run


def jobs_fixture():
    return [
        {"id": 7001, "name": "deploy-production", "conclusion": "success"},
        {"id": 7002, "name": "verify-production", "conclusion": "success"},
    ]


class LiveEvidenceReconcilePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (
            ROOT / ".github/workflows/live-evidence-reconcile.yml"
        ).read_text()
        cls.runner_source = (
            ROOT / "config/live-evidence-reconcile-runner.py"
        ).read_text()
        cls.implement = (ROOT / ".github/workflows/implement.yml").read_text()
        cls.plan = (ROOT / ".github/workflows/plan.yml").read_text()
        cls.review = (ROOT / ".github/workflows/review.yml").read_text()
        cls.remediate = (ROOT / ".github/workflows/remediate.yml").read_text()
        cls.pipeline = (
            ROOT / "templates/project-repo/.github/workflows/pipeline.yml"
        ).read_text()

    def assert_rejected(self, contract, run=None, jobs=None, **kwargs):
        with self.assertRaises(policy.ContractError):
            policy.qualify_run(
                contract,
                run or run_fixture(),
                jobs or jobs_fixture(),
                pr_head_sha=HEAD,
                now=NOW,
                **kwargs,
            )

    def test_api_failures_report_only_allowlisted_operation_and_status(self):
        repository = "KARSIFT/example"
        endpoint_cases = {
            ("POST", f"repos/{repository}/git/trees"): "create_tree",
            ("POST", f"repos/{repository}/git/commits"): "create_commit",
            ("PATCH", f"repos/{repository}/git/refs/heads/agent/example"): "update_ref",
            ("POST", f"repos/{repository}/issues/7/comments"): "create_issue_comment",
            ("PATCH", f"repos/{repository}/issues/comments/9"): "update_issue_comment",
            (
                "POST",
                f"repos/{repository}/actions/workflows/synthetic.yml/dispatches",
            ): "dispatch_workflow",
            ("GET", f"repos/{repository}/pulls/7?private=value"): "read_metadata",
        }
        for (method, endpoint), expected in endpoint_cases.items():
            self.assertEqual(runner.api_operation(method, endpoint), expected)

        unsafe_stderr = (
            "gh: secret-value and repos/KARSIFT/example/git/trees "
            "(HTTP 403)\nresponse body with token"
        )
        completed = subprocess.CompletedProcess(
            args=["gh", "api"],
            returncode=1,
            stdout="",
            stderr=unsafe_stderr,
        )
        api = runner.GitHub(repository, "never-render-this-token")
        with patch.object(runner.subprocess, "run", return_value=completed):
            with self.assertRaises(runner.ApiError) as rejected:
                api.mutate(
                    "POST",
                    f"repos/{repository}/git/trees",
                    {"private": "payload"},
                )
        self.assertEqual(
            str(rejected.exception),
            "github_api_create_tree_http_403",
        )
        rendered = str(rejected.exception)
        self.assertNotIn("secret-value", rendered)
        self.assertNotIn("KARSIFT/example", rendered)
        self.assertNotIn("token", rendered)

        no_status = runner.api_failure_code(
            "create_issue_comment",
            "arbitrary private failure text",
        )
        self.assertEqual(no_status, "github_api_create_issue_comment_failed")

    def test_contract_parser_rejects_unknown_duplicate_and_unsafe_yaml(self):
        for suffix in (
            "unknown_field: value\n",
            "task_id: VOC-097-T02\n",
            "unsafe: &anchor value\n",
        ):
            with self.assertRaises(policy.ContractError):
                policy.validate_contract(
                    policy.parse_contract_yaml(contract_text() + suffix),
                    "VOC-097-T02",
                )
        with self.assertRaises(policy.ContractError):
            policy.validate_contract(
                policy.parse_contract_yaml(
                    contract_text(
                        workflow_file=None,
                    ).replace(
                        "workflow_file: None",
                        'workflow_name: "trusted\\nresult_head_sha: `fake`"',
                    )
                ),
                "VOC-097-T02",
            )

    def test_repository_file_accepts_github_base64_line_wrapping_only(self):
        import base64

        payload = b"pipeline:" + (b" trusted" * 30)

        class ContentsApi:
            repository = "KARSIFT/example"

            def __init__(self, content):
                self.content = content

            def get_optional(self, endpoint):
                return {"encoding": "base64", "content": self.content}

        wrapped = base64.encodebytes(payload).decode()
        self.assertEqual(
            runner.read_repository_file(ContentsApi(wrapped), "file", HEAD),
            payload.decode(),
        )
        with self.assertRaises(policy.ContractError):
            runner.read_repository_file(
                ContentsApi(wrapped.replace("\n", " ")),
                "file",
                HEAD,
            )

    def test_06_wrong_workflow_identity_is_rejected(self):
        contract = parsed_contract()
        self.assert_rejected(contract, run_fixture(path=".github/workflows/other.yml"))

    def test_07_wrong_or_missing_required_job_is_rejected(self):
        contract = parsed_contract()
        self.assert_rejected(
            contract,
            jobs=[{"id": 7001, "name": "deploy-production", "conclusion": "success"}],
        )
        failed = jobs_fixture()
        failed[1]["conclusion"] = "failure"
        self.assert_rejected(contract, jobs=failed)

    def test_08_wrong_event_branch_and_sha_lineage_are_rejected(self):
        contract = parsed_contract()
        self.assert_rejected(contract, run_fixture(event="schedule"))
        self.assert_rejected(contract, run_fixture(head_branch="develop"))
        self.assert_rejected(contract, run_fixture(head_sha=RUN_SHA))

        integration = parsed_contract(lineage="  mode: integration_contains_pr_head")
        self.assert_rejected(
            integration,
            run_fixture(head_sha=RUN_SHA),
            integration_contains_pr=True,
            integration_contains_run=False,
        )

    def test_pull_request_run_must_belong_to_target_pr(self):
        task = SimpleNamespace(
            contract=parsed_contract(events="  - pull_request"),
            pr_number=7,
            head_sha=HEAD,
            waiting_since=NOW - timedelta(hours=1),
        )

        class NoApiCalls:
            repository = "KARSIFT/example"

            def __getattr__(self, name):
                raise AssertionError("wrong PR must reject before job API reads")

        with self.assertRaises(policy.ContractError) as rejected:
            runner.qualify(
                NoApiCalls(),
                task,
                run_fixture(
                    event="pull_request",
                    pull_requests=[{"number": 999}],
                ),
                NOW,
            )
        self.assertEqual(rejected.exception.code, "wrong_pull_request")

    def test_name_only_identity_must_resolve_to_one_matching_workflow_id(self):
        name_only_data = policy.parse_contract_yaml(
            contract_text().replace(
                "workflow_file: deploy-production.yml",
                "workflow_name: deploy-production",
            )
        )
        name_only = policy.validate_contract(name_only_data, "VOC-097-T02")
        task = SimpleNamespace(
            contract=name_only,
            head_sha=HEAD,
            waiting_since=NOW - timedelta(hours=1),
        )

        class NameApi:
            repository = "KARSIFT/example"

            def __init__(self, workflow_ids):
                self.workflow_ids = workflow_ids

            def get_all(self, endpoint, key=None):
                return [
                    {"id": workflow_id, "name": "deploy-production"}
                    for workflow_id in self.workflow_ids
                ]

            def get(self, endpoint):
                return {"total_count": 2, "jobs": jobs_fixture()}

        accepted_run = run_fixture(workflow_id=91)
        evidence = runner.qualify(NameApi([91]), task, accepted_run, NOW)
        self.assertEqual(evidence["run_id"], 12345)
        with self.assertRaises(policy.ContractError):
            runner.qualify(NameApi([91, 92]), task, accepted_run, NOW)
        with self.assertRaises(policy.ContractError):
            runner.qualify(NameApi([92]), task, accepted_run, NOW)

    def test_candidate_runs_use_bounded_api_pagination(self):
        task = SimpleNamespace(contract=parsed_contract())

        class RunsApi:
            repository = "KARSIFT/example"

            def get_all(self, endpoint, key=None):
                self.endpoint = endpoint
                self.key = key
                return [run_fixture(id=index) for index in range(1, 41)]

        api = RunsApi()
        runs = runner.candidate_runs(api, task)
        self.assertEqual(len(runs), 40)
        self.assertEqual(api.key, "workflow_runs")
        self.assertNotIn("per_page=30", api.endpoint)

    def test_09_qualifying_output_contains_allowlisted_metadata_only(self):
        evidence = policy.qualify_run(
            parsed_contract(),
            run_fixture(),
            jobs_fixture(),
            pr_head_sha=HEAD,
            now=NOW,
        )
        serialized = json.loads(policy.evidence_json(evidence))
        self.assertEqual(serialized["state"], "qualified")
        self.assertEqual(serialized["job_ids"], [7001, 7002])
        self.assertNotIn("logs", serialized)
        self.assertNotIn("artifacts", serialized)
        self.assertNotIn("actor", serialized)
        with self.assertRaises(policy.ContractError):
            policy.evidence_json({**evidence, "arbitrary_output": "forbidden"})

    def test_10_workflow_never_calls_log_or_artifact_apis(self):
        combined = self.workflow + self.runner_source
        self.assertNotIn("/logs", combined)
        self.assertNotIn("/artifacts", combined)
        self.assertNotIn("download-artifact", combined)
        self.assertNotIn("steps_url", combined)

    def test_11_qualification_is_one_commit_then_fresh_pr_review(self):
        self.assertIn("append_result_commit", self.runner_source)
        self.assertIn("result_already_present", self.runner_source)
        self.assertIn("fresh exact-SHA independent review", self.runner_source)
        self.assertLess(
            self.runner_source.index(
                "post_qualified_comment(read_api, write_api, task, evidence, new_sha)"
            ),
            self.runner_source.index(
                "advance_result_ref(read_api, write_api, task, new_sha)"
            ),
        )
        self.assertIn(
            "fast synchronize run can never observe the result commit",
            self.runner_source,
        )
        append_block = self.runner_source.split("def append_result_commit", 1)[1].split(
            "def advance_result_ref", 1
        )[0]
        advance_block = self.runner_source.split("def advance_result_ref", 1)[1].split(
            "def post_qualified_comment", 1
        )[0]
        comment_block = self.runner_source.split("def post_qualified_comment", 1)[1].split(
            "def comment_exists", 1
        )[0]
        self.assertGreaterEqual(append_block.count("assert_pr_pair_current"), 2)
        self.assertIn("assert_pr_pair_current", advance_block)
        self.assertIn("assert_pr_pair_current", comment_block)
        self.assertIn('f"base_sha: `{task.base_sha}`"', comment_block)
        self.assertIn("pull_request:", self.pipeline)
        self.assertEqual(
            self.pipeline.count(
                "expected_head_sha: ${{ github.event.pull_request.head.sha }}"
            ),
            4,
        )

    def test_live_evidence_mutations_require_the_discovered_base_head_pair(self):
        task = SimpleNamespace(
            pr_number=7,
            head_sha=HEAD,
            head_ref="agent/example",
            base_sha=BASE,
        )

        class PairApi:
            repository = "KARSIFT/example"

            def __init__(self, base_sha=BASE):
                self.base_sha = base_sha

            def get(self, endpoint):
                self.endpoint = endpoint
                return {
                    "state": "open",
                    "merged_at": None,
                    "head": {
                        "sha": HEAD,
                        "ref": "agent/example",
                        "repo": {"full_name": self.repository},
                    },
                    "base": {"sha": self.base_sha},
                }

        runner.assert_pr_pair_current(PairApi(), task)
        with self.assertRaises(policy.ContractError) as stale:
            runner.assert_pr_pair_current(PairApi("f" * 40), task)
        self.assertEqual(stale.exception.code, "stale_pr_pair")

    def test_12_stale_and_non_success_runs_are_rejected(self):
        contract = parsed_contract(max_age="1h")
        self.assert_rejected(
            contract,
            run_fixture(updated_at="2026-08-20T22:00:00Z"),
        )
        self.assert_rejected(contract, run_fixture(conclusion="failure"))
        self.assert_rejected(
            parsed_contract(),
            run_fixture(updated_at="2026-08-20T23:59:00Z"),
            completed_by=datetime(2026, 8, 20, 23, 58, tzinfo=timezone.utc),
        )

    def test_13_timeout_is_bounded_and_marker_is_single_use(self):
        self.assertFalse(policy.timed_out(NOW - timedelta(hours=71), NOW))
        self.assertTrue(policy.timed_out(NOW - timedelta(hours=72), NOW))
        self.assertIn("karsift-live-evidence-timeout", self.runner_source)
        self.assertIn("comment_exists", self.runner_source)

    def test_14_duplicate_result_short_circuits_reconciliation(self):
        task = SimpleNamespace(result_path="result.json", head_sha=HEAD, base_sha=BASE)
        task.pr_number = 12
        class AttestedApi:
            repository = "KARSIFT/example"

            def get_all(self, endpoint, key=None):
                return [{
                    "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
                    "body": (
                        "**Live-evidence reconcile — qualified**\n"
                        f"result_head_sha: `{HEAD}`\n"
                        f"base_sha: `{BASE}`"
                    ),
                }]

        class StaleBaseAttestedApi(AttestedApi):
            def get_all(self, endpoint, key=None):
                comments = super().get_all(endpoint, key)
                comments[0]["body"] = comments[0]["body"].replace(BASE, "f" * 40)
                return comments

        with patch.object(
            runner,
            "read_repository_file",
            return_value='{"schema_version":1,"state":"qualified","run_id":12345}',
        ):
            self.assertTrue(runner.result_already_present(AttestedApi(), task))
            self.assertFalse(runner.result_already_present(StaleBaseAttestedApi(), task))
        self.assertIn('base_binding="base_sha:', self.review)
        self.assertIn('index($base)', self.review)
        self.assertIn(
            "group: live-evidence-reconcile-${{ github.repository }}",
            self.workflow,
        )
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_declared_dispatch_must_mirror_workflow_and_inputs(self):
        dispatch = """dispatch:
  workflow_file: deploy-production.yml
  inputs:
    reason: live-evidence
    bounded: true
"""
        contract = parsed_contract(dispatch=dispatch)
        self.assertEqual(contract.dispatch.workflow_file, "deploy-production.yml")
        self.assertEqual(
            contract.dispatch.inputs,
            {"reason": "live-evidence", "bounded": "true"},
        )
        with self.assertRaises(policy.ContractError):
            parsed_contract(
                dispatch="""dispatch:
  workflow_file: other.yml
  inputs:
    reason: live-evidence
"""
            )

    def test_operator_permissions_are_separate_from_implementer(self):
        operator_job_permissions = self.workflow.split("    permissions:", 1)[1].split(
            "    steps:", 1
        )[0]
        self.assertIn("actions: write", operator_job_permissions)
        self.assertIn("checks: read", operator_job_permissions)
        implement_permissions = self.implement.split("permissions:", 1)[1].split(
            "steps:", 1
        )[0]
        self.assertNotIn("actions:", implement_permissions)
        self.assertNotIn("issues: write", implement_permissions)
        self.assertNotIn("pull-requests: write", implement_permissions)
        self.assertIn("issues: read", implement_permissions)
        self.assertIn("Mint separate operator token", self.workflow)
        self.assertIn(
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
            self.workflow,
        )
        self.assertNotIn(
            "actions/create-github-app-token@d72941d797fd3113feb6b93fd0dec494b13a2547",
            self.workflow,
        )
        self.assertNotIn("permission-actions: write", self.workflow)
        self.assertIn("permission-contents: write", self.workflow)
        self.assertIn("permission-issues: write", self.workflow)
        self.assertIn("permission-pull-requests: write", self.workflow)
        operator_caller = self.pipeline.split("  live-evidence-reconcile:", 1)[1]
        self.assertIn("      actions: write", operator_caller)
        self.assertNotIn("    secrets: inherit", operator_caller)
        self.assertIn("KARSIFT_BOT_APP_ID:", operator_caller)
        self.assertIn("KARSIFT_BOT_PRIVATE_KEY:", operator_caller)
        self.assertIn("repository: ${{ job.workflow_repository }}", self.workflow)
        self.assertIn("ref: ${{ job.workflow_sha }}", self.workflow)
        self_ci = (ROOT / ".github/workflows/self-ci.yml").read_text()
        self.assertIn(
            'property "workflow_repository" is not defined in object type',
            self_ci,
        )
        self.assertIn(
            'property "workflow_sha" is not defined in object type',
            self_ci,
        )
        implement_job, publish_job = self.implement.split("\n  publish:", 1)
        self.assertNotIn("create-github-app-token@", implement_job)
        self.assertNotIn("APP_TOKEN", implement_job)
        self.assertGreaterEqual(implement_job.count("persist-credentials: false"), 2)
        self.assertIn("needs: implement", publish_job)
        self.assertIn("actions/download-artifact@", publish_job)
        self.assertIn("git init --bare", publish_job)
        self.assertIn("core.hooksPath=/dev/null", publish_job)
        self.assertIn("permission-contents: write", publish_job)
        self.assertIn("permission-pull-requests: write", publish_job)
        self.assertNotIn("permission-workflows: write", publish_job)
        self.assertIn("permission-issues: write", publish_job)
        self.assertIn("cannot publish workflow-file changes", publish_job)
        self.assertIn('.user.login == "karsift-ai-infra-bot[bot]"', implement_job)
        self.assertIn('review / publish-review', implement_job)
        self.assertIn("report-no-change:", self.implement)

        plan_job, plan_publish_job = self.plan.split("\n  publish-plan:", 1)
        planner_boundary = plan_job.index(
            "Remove caller checkout credential before unrestricted planner"
        )
        self.assertNotIn("create-github-app-token@", plan_job)
        self.assertNotIn("steps.app-token", plan_job)
        self.assertNotIn("GH_TOKEN:", plan_job[planner_boundary:])
        self.assertGreaterEqual(plan_job.count("persist-credentials: false"), 2)
        self.assertIn("needs: plan", plan_publish_job)
        self.assertIn("actions/download-artifact@", plan_publish_job)
        self.assertIn("git init --bare", plan_publish_job)
        self.assertIn("core.hooksPath=/dev/null", plan_publish_job)
        self.assertIn("permission-contents: write", plan_publish_job)
        self.assertIn("permission-issues: write", plan_publish_job)
        self.assertIn("permission-pull-requests: write", plan_publish_job)
        self.assertIn("changed files outside its package directory", plan_publish_job)

    def test_caller_polls_without_workflow_run_recursion(self):
        self.assertIn('cron: "17 * * * *"', self.pipeline)
        self.assertNotIn("  workflow_run:", self.pipeline)
        self.assertIn("reconcile-live-evidence", self.pipeline)
        self.assertIn("live_evidence_run_id", self.pipeline)
        self.assertIn("  plan-review:", self.pipeline)
        self.assertIn("uses: KARSIFT/karsift-ai-infra/.github/workflows/plan-review.yml@main", self.pipeline)
        self.assertIn(
            "expected_head_sha: ${{ github.event.pull_request.head.sha }}",
            self.pipeline.split("  plan-review:", 1)[1].split("\n  extract-package-path:", 1)[0],
        )
        self.assertIn(
            "expected_base_sha: ${{ github.event.pull_request.base.sha }}",
            self.pipeline.split("  plan-review:", 1)[1].split("\n  extract-package-path:", 1)[0],
        )
        self.assertIn("needs: [review, plan-review]", self.pipeline)

    def test_waiting_marker_requires_trusted_comment_and_successful_review_check(self):
        body = (
            f"**Independent verification - bound to commit `{HEAD}`**\n\n"
            "task_id: `VOC-097-T02`\n"
            f"package_path: `{PACKAGE}`\n"
            "authority_issue: `8`\n\n"
            f"base_sha: `{BASE}`\n\n"
            "VERDICT: WAITING FOR OPERATOR LIVE EVIDENCE"
        )
        comment = {
            "id": 44,
            "body": body,
            "created_at": "2026-08-20T23:59:00Z",
            "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
        }

        class ReviewApi:
            repository = "KARSIFT/example"

            def __init__(self, checks, base_pipeline=b"trusted pipeline"):
                self.checks = checks
                self.base_pipeline = base_pipeline

            def get_all(self, endpoint, key=None):
                return self.checks

            def get_optional(self, endpoint):
                import base64

                content = (
                    self.base_pipeline
                    if f"ref={BASE}" in endpoint
                    else b"trusted pipeline"
                )
                return {
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode(),
                }

            def get(self, endpoint):
                if endpoint.endswith("actions/workflows/pipeline.yml"):
                    return {
                        "id": 88,
                        "path": ".github/workflows/pipeline.yml",
                        "state": "active",
                    }
                if endpoint.endswith("actions/runs/123"):
                    return {
                        "workflow_id": 88,
                        "path": ".github/workflows/pipeline.yml",
                        "event": "pull_request",
                        "head_sha": HEAD,
                        "head_branch": "agent/example",
                        "conclusion": "success",
                        "pull_requests": [{"number": 7}],
                    }
                raise AssertionError(endpoint)

        good_check = {
            "name": "review / publish-review",
            "conclusion": "success",
            "head_sha": HEAD,
            "app": {"slug": "github-actions"},
            "details_url": "https://github.com/KARSIFT/example/actions/runs/123/job/456",
            "started_at": "2026-08-20T23:55:00Z",
            "completed_at": "2026-08-21T00:00:00Z",
        }
        self.assertIsNotNone(
            runner.trusted_waiting_review(
                ReviewApi([good_check]),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [comment],
            )
        )
        with self.assertRaises(policy.ContractError):
            runner.trusted_waiting_review(
                ReviewApi([good_check], base_pipeline=b"different pipeline"),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [comment],
            )
        forged = {**comment, "user": {"login": "untrusted", "type": "User"}}
        self.assertIsNone(
            runner.trusted_waiting_review(
                ReviewApi([good_check]),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [forged],
            )
        )
        generic_actions_bot = {
            **comment,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
        self.assertIsNone(
            runner.trusted_waiting_review(
                ReviewApi([good_check]),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [generic_actions_bot],
            )
        )
        with self.assertRaises(policy.ContractError):
            runner.trusted_waiting_review(
                ReviewApi([]),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [comment],
            )
        retargeted = {**comment, "body": comment["body"].replace("authority_issue: `8`", "authority_issue: `9`")}
        self.assertIsNone(
            runner.trusted_waiting_review(
                ReviewApi([good_check]),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [retargeted],
            )
        )
        stale_base = {**comment, "body": comment["body"].replace(BASE, "f" * 40)}
        self.assertIsNone(
            runner.trusted_waiting_review(
                ReviewApi([good_check]),
                7,
                HEAD,
                "agent/example",
                BASE,
                PACKAGE,
                "VOC-097-T02",
                8,
                [stale_base],
            )
        )
        self.assertIn("authority_issue: \\`$authority_issue\\`", self.review)
        self.assertIn("package_path: \\`$package_path\\`", self.review)
        review_job, publisher_job = self.review.split("\n  publish-review:", 1)
        self.assertNotIn("create-github-app-token@", review_job)
        self.assertNotIn("gh pr comment", review_job)
        self.assertIn("actions/download-artifact@", publisher_job)
        self.assertIn("permission-pull-requests: write", publisher_job)
        self.assertNotIn("permission-issues: write", publisher_job)
        self.assertIn("PR base/head pair changed before verdict publication", publisher_job)
        self.assertGreaterEqual(
            publisher_job.count("GH_REPO: ${{ github.repository }}"),
            2,
            "clean validation and App publication must not depend on a checkout",
        )

    def test_review_error_retry_context_is_metadata_only(self):
        review_error = self.remediate.split(
            "- name: Record sanitized review-job-error metadata without implementation retry",
            1,
        )[1].split("\n  retry:", 1)[0]
        self.assertNotIn("/actions/jobs/", review_error)
        self.assertNotIn("log_tail", review_error)
        self.assertNotIn("--allow-escape-sequences", review_error)
        self.assertIn('run_id: \\`$sanitized_run_id\\`', review_error)
        self.assertIn('head_sha: \\`$EXPECTED_HEAD_SHA\\`', review_error)
        self.assertIn('base_sha: \\`$EXPECTED_BASE_SHA\\`', review_error)
        self.assertIn('job_id: \\`$review_job_id\\`', review_error)
        self.assertIn('job_name: \\`$review_job_name\\`', review_error)
        self.assertIn('conclusion: \\`$review_job_conclusion\\`', review_error)
        self.assertIn("No raw logs, model output, prompts", review_error)

    def test_non_agent_pr_cannot_enter_wake_path(self):
        class NoApiCalls:
            repository = "KARSIFT/example"

            def __getattr__(self, name):
                raise AssertionError("non-agent PR must be rejected before API reads")

        pr = {
            "number": 7,
            "body": "Implements task `VOC-097-T02`\nPackage path: `specs/changes/x`\nCloses #8",
            "head": {
                "sha": HEAD,
                "ref": "feature/unreviewed",
                "repo": {"full_name": "KARSIFT/example"},
            },
            "base": {"sha": BASE},
        }
        self.assertIsNone(runner.current_waiting_task(NoApiCalls(), pr))
        closed = {
            **pr,
            "state": "closed",
            "merged_at": None,
            "head": {**pr["head"], "ref": "agent/closed"},
        }
        self.assertIsNone(runner.current_waiting_task(NoApiCalls(), closed))

    def test_dispatch_requires_protected_unchanged_workflow_and_excludes_pipeline(self):
        self.assertIn("dispatch_branch_unprotected", self.runner_source)
        self.assertNotIn("require_protected=False", self.runner_source)
        self.assertIn("dispatch_workflow_not_trusted", self.runner_source)
        self.assertIn('dispatch.workflow_file == "pipeline.yml"', self.runner_source)
        reservation = self.runner_source.index("declared dispatch reserved**")
        dispatch = self.runner_source.index("/dispatches\"")
        completion = self.runner_source.index("issues/comments/{reservation_id}")
        self.assertLess(reservation, dispatch)
        self.assertLess(dispatch, completion)
        self.assertIn("dispatch_reservation_failed", self.runner_source)

    def test_untrusted_comment_cannot_suppress_dispatch_or_timeout(self):
        marker = "<!-- karsift-live-evidence-dispatch task=VOC-097-T02 head=x -->"

        class CommentApi:
            repository = "KARSIFT/example"

            def __init__(self, login, user_type):
                self.login = login
                self.user_type = user_type

            def get_all(self, endpoint, key=None):
                return [{
                    "body": marker,
                    "user": {"login": self.login, "type": self.user_type},
                }]

        self.assertFalse(
            runner.comment_exists(CommentApi("attacker", "User"), 7, marker)
        )
        self.assertTrue(
            runner.comment_exists(
                CommentApi("karsift-ai-infra-bot[bot]", "Bot"),
                7,
                marker,
            )
        )

    def test_dispatch_reservation_is_trusted_and_precedes_single_attempt(self):
        contract = parsed_contract(
            dispatch="""dispatch:
  workflow_file: deploy-production.yml
  inputs:
    reason: live-evidence
"""
        )
        task = SimpleNamespace(
            contract=contract,
            task_id="VOC-097-T02",
            issue_number=8,
            package_path=PACKAGE,
            head_sha=HEAD,
            head_ref="agent/example",
            base_sha=BASE,
            pr_number=7,
            waiting_comment_id=42,
            waiting_since=NOW - timedelta(hours=1),
        )

        class DispatchReadApi:
            repository = "KARSIFT/example"

            def __init__(self, comments=None, change_branch_after=None):
                self.comments = comments or []
                self.change_branch_after = change_branch_after
                self.branch_reads = 0

            def get_all(self, endpoint, key=None):
                return self.comments

            def get(self, endpoint):
                if endpoint == "repos/KARSIFT/example":
                    return {"default_branch": "main"}
                if endpoint == "repos/KARSIFT/example/pulls/7":
                    return {
                        "state": "open",
                        "merged_at": None,
                        "body": (
                            "Implements task `VOC-097-T02`\n"
                            f"Package path: `{PACKAGE}`\n"
                            "Closes #8"
                        ),
                        "head": {
                            "sha": HEAD,
                            "ref": "agent/example",
                            "repo": {"full_name": "KARSIFT/example"},
                        },
                        "base": {"sha": BASE},
                    }
                if endpoint.endswith("branches/main"):
                    self.branch_reads += 1
                    sha = (
                        RUN_SHA
                        if self.change_branch_after is not None
                        and self.branch_reads > self.change_branch_after
                        else HEAD
                    )
                    return {"protected": True, "commit": {"sha": sha}}
                raise AssertionError(endpoint)

            def get_optional(self, endpoint):
                return {"sha": "trusted-workflow-blob"}

        class DispatchWriteApi:
            repository = "KARSIFT/example"

            def __init__(self):
                self.calls = []

            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                if endpoint.endswith("/comments"):
                    return {"id": 99}
                return None

        class DispatchActionsApi:
            repository = "KARSIFT/example"

            def __init__(self):
                self.calls = []

            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                return None

        trusted_waiting = ({"id": 42}, task.waiting_since)
        writer = DispatchWriteApi()
        actions_writer = DispatchActionsApi()
        with patch.object(
            runner, "trusted_waiting_review", return_value=trusted_waiting
        ), patch.object(
            runner, "current_utc_time", return_value=NOW
        ):
            runner.dispatch_once(
                DispatchReadApi(), writer, actions_writer, task, NOW
            )
        self.assertEqual(
            [(method, endpoint) for method, endpoint, _ in writer.calls],
            [
                ("POST", "repos/KARSIFT/example/issues/7/comments"),
                ("PATCH", "repos/KARSIFT/example/issues/comments/99"),
            ],
        )
        self.assertEqual(
            [(method, endpoint) for method, endpoint, _ in actions_writer.calls],
            [
                (
                    "POST",
                    "repos/KARSIFT/example/actions/workflows/deploy-production.yml/dispatches",
                )
            ],
        )

        reservation = writer.calls[0][2]["body"]
        suppressing_comment = {
            "body": reservation,
            "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
        }
        retry_writer = DispatchWriteApi()
        with patch.object(
            runner, "trusted_waiting_review", return_value=trusted_waiting
        ):
            runner.dispatch_once(
                DispatchReadApi([suppressing_comment]),
                retry_writer,
                DispatchActionsApi(),
                task,
                NOW,
            )
        self.assertEqual(retry_writer.calls, [])

        stale_writer = DispatchWriteApi()
        with patch.object(
            runner, "trusted_waiting_review", return_value=trusted_waiting
        ), patch.object(
            runner, "current_utc_time", return_value=NOW
        ), self.assertRaises(policy.ContractError) as stale:
            runner.dispatch_once(
                DispatchReadApi(change_branch_after=4),
                stale_writer,
                DispatchActionsApi(),
                task,
                NOW,
            )
        self.assertEqual(stale.exception.code, "dispatch_branch_changed")
        self.assertEqual(
            [(method, endpoint) for method, endpoint, _ in stale_writer.calls],
            [("POST", "repos/KARSIFT/example/issues/7/comments")],
        )
        with self.assertRaises(policy.ContractError) as expired:
            runner.dispatch_once(
                DispatchReadApi(),
                DispatchWriteApi(),
                DispatchActionsApi(),
                SimpleNamespace(
                    **{**task.__dict__, "waiting_since": NOW - timedelta(hours=72)}
                ),
                NOW,
            )
        self.assertEqual(expired.exception.code, "dispatch_authorization_expired")

        changed_authority_api = DispatchReadApi()
        original_get = changed_authority_api.get

        def changed_body(endpoint):
            response = original_get(endpoint)
            if endpoint.endswith("pulls/7"):
                response["body"] = response["body"].replace("Closes #8", "Closes #9")
            return response

        changed_authority_api.get = changed_body
        with patch.object(
            runner, "trusted_waiting_review", return_value=trusted_waiting
        ), patch.object(
            runner, "current_utc_time", return_value=NOW
        ), self.assertRaises(policy.ContractError) as changed:
            runner.dispatch_once(
                changed_authority_api,
                DispatchWriteApi(),
                DispatchActionsApi(),
                task,
                NOW,
            )
        self.assertEqual(changed.exception.code, "dispatch_authority_changed")

        with patch.object(
            runner, "trusted_waiting_review", return_value=None
        ), patch.object(
            runner, "current_utc_time", return_value=NOW
        ), self.assertRaises(policy.ContractError) as superseded:
            runner.dispatch_once(
                DispatchReadApi(),
                DispatchWriteApi(),
                DispatchActionsApi(),
                task,
                NOW,
            )
        self.assertEqual(superseded.exception.code, "dispatch_authority_changed")


if __name__ == "__main__":
    unittest.main()
