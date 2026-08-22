import base64
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from task_completion import (  # noqa: E402
    BOT_LOGIN,
    CompletionError,
    marker_body,
)


def load_runner():
    path = ROOT / "config/task-completion-runner.py"
    spec = spec_from_file_location("task_completion_runner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_runner()
REPOSITORY = "KARSIFT/caller"
PR_NUMBER = 18
ISSUE_NUMBER = 17
HEAD = "a" * 40
MERGE = "b" * 40
MERGED_AT = "2026-08-22T00:00:10Z"
BODY = (
    "Implements task `VOC-108-T00`\n"
    "Package path: `specs/changes/VOC-108-example`\n"
    "Closes #17"
)
PR = {
    "number": PR_NUMBER,
    "state": "closed",
    "merged_at": MERGED_AT,
    "merge_commit_sha": MERGE,
    "head": {"sha": HEAD},
    "body": BODY,
}
RECORD = {
    "repository": REPOSITORY,
    "authority_issue": str(ISSUE_NUMBER),
    "package_path": "specs/changes/VOC-108-example",
    "task_id": "VOC-108-T00",
    "pr_number": PR_NUMBER,
    "reviewed_head_sha": HEAD,
    "merge_commit_sha": MERGE,
    "merged_at": MERGED_AT,
}
ROSTER = [{"task_id": "VOC-108-T00", "issue": ISSUE_NUMBER, "depends_on": []}]


def exact_marker():
    return {
        "body": marker_body(RECORD),
        "user": {"login": BOT_LOGIN, "type": "Bot"},
        "created_at": "2026-08-22T00:00:11Z",
    }


def review_comment(*, issue_number=ISSUE_NUMBER, verdict="PASS", comment_id=1):
    return {
        "id": comment_id,
        "created_at": "2026-08-22T00:00:05Z",
        "user": {"login": BOT_LOGIN, "type": "Bot"},
        "body": (
            f"**Independent verification - bound to commit `{HEAD}`**\n\n"
            "task_id: `VOC-108-T00`\n"
            "package_path: `specs/changes/VOC-108-example`\n"
            f"authority_issue: `{issue_number}`\n\n"
            f"VERDICT: {verdict}"
        ),
    }


def comment_reads(task_markers):
    return [[review_comment()], task_markers]


def completion_timeline(*, close_after_marker):
    marker_event = {
        "event": "commented",
        "body": marker_body(RECORD),
        "actor": {"login": BOT_LOGIN, "type": "Bot"},
    }
    close_event = {"event": "closed", "actor": {"login": BOT_LOGIN, "type": "Bot"}}
    return [marker_event, close_event] if close_after_marker else [close_event, marker_event]


class TaskCompletionRunnerTests(unittest.TestCase):
    def test_roster_loader_accepts_github_wrapped_base64_at_exact_head(self):
        encoded = base64.b64encode(json.dumps(ROSTER).encode()).decode()
        wrapped = f"{encoded[:20]}\n{encoded[20:]}\n"
        with patch.object(
            RUNNER,
            "gh",
            return_value={"encoding": "base64", "content": wrapped},
        ) as gh:
            self.assertEqual(
                RUNNER.roster(REPOSITORY, "specs/changes/VOC-108-example", HEAD),
                ROSTER,
            )
        self.assertIn(f"?ref={HEAD}", gh.call_args.args[-1])

    def test_corrected_live_body_drives_first_publication_after_merge(self):
        with (
            patch.object(RUNNER, "pull", return_value=PR) as get_pull,
            patch.object(RUNNER, "comments", side_effect=comment_reads([])),
            patch.object(RUNNER, "roster", return_value=ROSTER),
            patch.object(RUNNER, "issue", return_value={"state": "open"}),
            patch.object(RUNNER, "gh") as gh,
        ):
            RUNNER.publish_completion(
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                reviewed_head_sha=HEAD,
            )

        get_pull.assert_called_once_with(REPOSITORY, PR_NUMBER)
        self.assertEqual(gh.call_count, 2)
        self.assertIn("VOC-108-T00", gh.call_args_list[0].kwargs["input_data"])
        self.assertIn('"state": "closed"', gh.call_args_list[1].kwargs["input_data"])

    def test_changed_head_and_ambiguous_live_body_fail_before_mutation(self):
        cases = (
            ({**PR, "head": {"sha": "c" * 40}}, HEAD),
            ({**PR, "body": f"{BODY}\nCloses #19"}, HEAD),
        )
        for pr, expected_head in cases:
            with self.subTest(body=pr["body"], head=pr["head"]):
                with (
                    patch.object(RUNNER, "pull", return_value=pr),
                    patch.object(RUNNER, "comments") as get_comments,
                    patch.object(RUNNER, "gh") as gh,
                ):
                    with self.assertRaises(CompletionError):
                        RUNNER.publish_completion(
                            repository=REPOSITORY,
                            pr_number=PR_NUMBER,
                            reviewed_head_sha=expected_head,
                        )
                get_comments.assert_not_called()
                gh.assert_not_called()

    def test_live_body_cannot_redirect_mutation_beyond_signed_review_identity(self):
        redirected = {**PR, "body": BODY.replace("Closes #17", "Closes #19")}
        with (
            patch.object(RUNNER, "pull", return_value=redirected),
            patch.object(
                RUNNER, "comments", return_value=[review_comment(issue_number=19)]
            ),
            patch.object(RUNNER, "roster", return_value=ROSTER) as get_roster,
            patch.object(RUNNER, "issue") as get_issue,
            patch.object(RUNNER, "gh") as gh,
        ):
            with self.assertRaises(CompletionError):
                RUNNER.publish_completion(
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    reviewed_head_sha=HEAD,
                )
        get_roster.assert_called_once()
        get_issue.assert_not_called()
        gh.assert_not_called()

    def test_duplicate_or_conflicting_existing_marker_fails_closed(self):
        conflicting = exact_marker()
        conflicting["body"] = conflicting["body"].replace("VOC-108-T00", "VOC-108-T01")
        for markers in ([conflicting], [exact_marker(), exact_marker()]):
            with self.subTest(marker_count=len(markers)):
                with (
                    patch.object(RUNNER, "pull", return_value=PR),
                    patch.object(RUNNER, "comments", side_effect=comment_reads(markers)),
                    patch.object(RUNNER, "roster", return_value=ROSTER),
                    patch.object(RUNNER, "issue") as get_issue,
                    patch.object(RUNNER, "gh") as gh,
                ):
                    with self.assertRaises(CompletionError):
                        RUNNER.publish_completion(
                            repository=REPOSITORY,
                            pr_number=PR_NUMBER,
                            reviewed_head_sha=HEAD,
                        )
                get_issue.assert_not_called()
                gh.assert_not_called()

    def test_already_complete_retry_is_a_mutation_free_noop(self):
        with (
            patch.object(RUNNER, "pull", return_value=PR),
            patch.object(
                RUNNER, "comments", side_effect=comment_reads([exact_marker()])
            ),
            patch.object(RUNNER, "roster", return_value=ROSTER),
            patch.object(
                RUNNER,
                "issue",
                return_value={"state": "closed"},
            ),
            patch.object(
                RUNNER,
                "timeline",
                return_value=completion_timeline(close_after_marker=True),
            ),
            patch.object(RUNNER, "gh") as gh,
        ):
            RUNNER.publish_completion(
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                reviewed_head_sha=HEAD,
            )
        gh.assert_not_called()

    def test_partial_publication_retry_restores_post_marker_close_wakeup(self):
        with (
            patch.object(RUNNER, "pull", return_value=PR),
            patch.object(
                RUNNER, "comments", side_effect=comment_reads([exact_marker()])
            ),
            patch.object(RUNNER, "roster", return_value=ROSTER),
            patch.object(
                RUNNER,
                "issue",
                return_value={"state": "closed"},
            ),
            patch.object(
                RUNNER,
                "timeline",
                return_value=completion_timeline(close_after_marker=False),
            ),
            patch.object(RUNNER, "gh") as gh,
        ):
            RUNNER.publish_completion(
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                reviewed_head_sha=HEAD,
            )
        self.assertEqual(gh.call_count, 2)
        self.assertIn('"state": "open"', gh.call_args_list[0].kwargs["input_data"])
        self.assertIn('"state": "closed"', gh.call_args_list[1].kwargs["input_data"])

    def test_new_marker_reopens_then_closes_an_already_closed_issue(self):
        with (
            patch.object(RUNNER, "pull", return_value=PR),
            patch.object(RUNNER, "comments", side_effect=comment_reads([])),
            patch.object(RUNNER, "roster", return_value=ROSTER),
            patch.object(RUNNER, "issue", return_value={"state": "closed"}),
            patch.object(RUNNER, "gh") as gh,
        ):
            RUNNER.publish_completion(
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                reviewed_head_sha=HEAD,
            )
        self.assertEqual(gh.call_count, 3)
        self.assertIn('"state": "open"', gh.call_args_list[1].kwargs["input_data"])
        self.assertIn('"state": "closed"', gh.call_args_list[2].kwargs["input_data"])


if __name__ == "__main__":
    unittest.main()
