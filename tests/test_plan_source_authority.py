import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/plan.yml").read_text()


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


class PlanSourceAuthorityTests(unittest.TestCase):
    scripts = (
        workflow_run_block("Reconcile source issue before planner execution"),
        workflow_run_block("Revalidate source issue before plan publication"),
    )

    @staticmethod
    def run_guard(
        script,
        *,
        source_issue="123",
        trusted_publisher="karsift-ai-infra-bot[bot]",
        issue=None,
        comments=None,
        pulls=None,
        linked=None,
    ):
        issue = issue or {"number": 123, "state": "open", "labels": []}
        comments = comments or [[]]
        pulls = pulls or [[]]
        linked = linked or {
            "number": 42,
            "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
            "head": {"ref": "plan/voc-100-fixture"},
            "body": (
                "<!-- karsift-plan-source-issue:123 -->\n"
                "Draft change package proposed by the planner role (model resolved from\n"
                "karsift-ai-infra/config/roles.yml).\n\n"
                "In response to issue #123."
            ),
        }
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            bin_path = scratch_path / "bin"
            bin_path.mkdir()
            fixture_paths = {}
            for name, value in {
                "issue": issue,
                "comments": comments,
                "pulls": pulls,
                "linked": linked,
            }.items():
                fixture_path = scratch_path / f"{name}.json"
                fixture_path.write_text(json.dumps(value))
                fixture_paths[name] = fixture_path
            invocation_log = scratch_path / "gh-invocations"
            invocation_log.touch()
            gh_stub = bin_path / "gh"
            gh_stub.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    printf '%s\n' "$*" >> {invocation_log}
                    case "$*" in
                      "api repos/KARSIFT/fixture/issues/123") cat {fixture_paths['issue']} ;;
                      "api --paginate --slurp repos/KARSIFT/fixture/issues/123/comments?per_page=100") cat {fixture_paths['comments']} ;;
                      "api --paginate --slurp repos/KARSIFT/fixture/pulls?state=all&per_page=100") cat {fixture_paths['pulls']} ;;
                      "api repos/KARSIFT/fixture/pulls/42") cat {fixture_paths['linked']} ;;
                      *) exit 81 ;;
                    esac
                    """
                )
            )
            gh_stub.chmod(0o755)
            output = scratch_path / "github-output"
            output.touch()
            env = os.environ.copy()
            env.update(
                {
                    "GH_REPO": "KARSIFT/fixture",
                    "SOURCE_ISSUE": source_issue,
                    "TRUSTED_PUBLISHER": trusted_publisher,
                    "GITHUB_OUTPUT": str(output),
                    "PATH": f"{bin_path}:{env['PATH']}",
                }
            )
            completed = subprocess.run(
                ["bash", "-c", script],
                cwd=scratch_path,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            return completed, output.read_text(), invocation_log.read_text()

    def test_same_source_runs_are_serialized_and_both_guards_gate_mutation(self):
        self.assertIn(
            "group: karsift-plan-source-${{ github.repository_id }}-${{ inputs.source_issue_number || github.run_id }}",
            WORKFLOW,
        )
        self.assertIn("cancel-in-progress: false", WORKFLOW)
        self.assertIn("needs: source-authority", WORKFLOW)
        self.assertIn(
            "if: needs.source-authority.outputs.proceed == 'true'", WORKFLOW
        )
        self.assertEqual(WORKFLOW.count("steps.source-guard.outputs.proceed == 'true'"), 8)
        publisher_job = WORKFLOW.split("  publish-plan:", 1)[1]
        self.assertLess(
            publisher_job.index("Revalidate source issue before plan publication"),
            publisher_job.index("Post clarifying question from clean runner"),
        )
        self.assertIn(
            "needs.plan.outputs.needs_info == 'true' && steps.source-guard.outputs.proceed == 'true'",
            publisher_job,
        )

    def test_guard_matrix_runs_from_outside_a_git_worktree(self):
        trusted_pull = {
            "number": 42,
            "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
            "head": {"ref": "plan/voc-100-fixture"},
            "body": (
                "<!-- karsift-plan-source-issue:123 -->\n"
                "Draft change package proposed by the planner role (model resolved from\n"
                "karsift-ai-infra/config/roles.yml).\n\n"
                "In response to issue #123."
            ),
        }
        legacy_pull = {
            **trusted_pull,
            "state": "closed",
            "body": (
                "Draft change package proposed by the planner role (model resolved from\n"
                "karsift-ai-infra/config/roles.yml).\n\n"
                "In response to issue #123."
            ),
        }
        body_text_spoof = {
            **trusted_pull,
            "body": (
                "Draft change package proposed by the planner role (model resolved from\n"
                "karsift-ai-infra/config/roles.yml).\n\n"
                "**This is a draft.**\n\n"
                "> unrelated request\nIn response to issue #123."
            ),
        }
        trusted_comment = {
            "user": {"login": "karsift-ai-infra-bot[bot]", "type": "Bot"},
            "body": "Draft change package proposed: https://github.com/KARSIFT/fixture/pull/42",
        }
        fallback_pull = {
            **trusted_pull,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
        fallback_comment = {
            **trusted_comment,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
        cases = (
            ("non-issue", {"source_issue": ""}, 0, "proceed=true"),
            ("open-unplanned", {}, 0, "proceed=true"),
            (
                "trusted-body-link",
                {"pulls": [[trusted_pull]]},
                0,
                "proceed=false",
            ),
            (
                "legacy-fixed-prefix-link",
                {"pulls": [[legacy_pull]], "linked": legacy_pull},
                0,
                "proceed=false",
            ),
            (
                "source-text-outside-authority-prefix",
                {"pulls": [[body_text_spoof]]},
                0,
                "proceed=true",
            ),
            (
                "trusted-comment-link",
                {"comments": [[trusted_comment]], "linked": trusted_pull},
                0,
                "proceed=false",
            ),
            (
                "fallback-publisher-link",
                {
                    "trusted_publisher": "github-actions[bot]",
                    "comments": [[fallback_comment]],
                    "pulls": [[fallback_pull]],
                    "linked": fallback_pull,
                },
                0,
                "proceed=false",
            ),
            (
                "closed-unplanned",
                {"issue": {"number": 123, "state": "closed", "labels": []}},
                0,
                "proceed=false",
            ),
            (
                "untrusted-planned-label",
                {
                    "issue": {
                        "number": 123,
                        "state": "open",
                        "labels": [{"name": "karsift:planned"}],
                    }
                },
                1,
                "",
            ),
            (
                "ambiguous-trusted-links",
                {"pulls": [[trusted_pull, {**trusted_pull, "number": 43}]]},
                1,
                "",
            ),
            (
                "comment-target-mismatch",
                {
                    "comments": [[trusted_comment]],
                    "linked": {
                        **trusted_pull,
                        "user": {"login": "untrusted", "type": "User"},
                    },
                },
                1,
                "",
            ),
        )
        for script in self.scripts:
            for name, kwargs, expected_code, expected_output in cases:
                with self.subTest(script=script[:40], case=name):
                    completed, output, _ = self.run_guard(script, **kwargs)
                    self.assertEqual(
                        completed.returncode, expected_code, completed.stderr
                    )
                    self.assertIn(expected_output, output)

    def test_only_configured_bot_authored_exact_source_links_are_trusted(self):
        for script in self.scripts:
            self.assertIn(".user.login == $publisher", script)
            self.assertIn('--arg publisher "$TRUSTED_PUBLISHER"', script)
            self.assertIn('.user.type == "Bot"', script)
            self.assertIn('(.head.ref | startswith("plan/"))', script)
            self.assertIn('binding="In response to issue #$SOURCE_ISSUE."', script)
            self.assertIn(
                'authority_marker="<!-- karsift-plan-source-issue:$SOURCE_ISSUE -->"',
                script,
            )
            self.assertNotIn("contains($binding)", script)
            self.assertIn("ambiguous trusted plan linkage", script)

        self.assertIn(
            'echo "<!-- karsift-plan-source-issue:$SOURCE_ISSUE -->"', WORKFLOW
        )
        self.assertEqual(WORKFLOW.count("TRUSTED_PUBLISHER: ${{"), 2)
        self.assertEqual(WORKFLOW.count("pulls?state=all&per_page=100"), 2)
        self.assertNotIn("pulls?state=open", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
