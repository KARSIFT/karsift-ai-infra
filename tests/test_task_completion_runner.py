from importlib.util import module_from_spec, spec_from_file_location
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


def exact_marker():
    return {
        "body": marker_body(RECORD),
        "user": {"login": BOT_LOGIN, "type": "Bot"},
    }


class TaskCompletionRunnerTests(unittest.TestCase):
    def test_corrected_live_body_drives_first_publication_after_merge(self):
        with (
            patch.object(RUNNER, "pull", return_value=PR) as get_pull,
            patch.object(RUNNER, "comments", return_value=[]),
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

    def test_duplicate_or_conflicting_existing_marker_fails_closed(self):
        conflicting = exact_marker()
        conflicting["body"] = conflicting["body"].replace("VOC-108-T00", "VOC-108-T01")
        for markers in ([conflicting], [exact_marker(), exact_marker()]):
            with self.subTest(marker_count=len(markers)):
                with (
                    patch.object(RUNNER, "pull", return_value=PR),
                    patch.object(RUNNER, "comments", return_value=markers),
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
            patch.object(RUNNER, "comments", return_value=[exact_marker()]),
            patch.object(RUNNER, "issue", return_value={"state": "closed"}),
            patch.object(RUNNER, "gh") as gh,
        ):
            RUNNER.publish_completion(
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                reviewed_head_sha=HEAD,
            )
        gh.assert_not_called()

    def test_new_marker_reopens_then_closes_an_already_closed_issue(self):
        with (
            patch.object(RUNNER, "pull", return_value=PR),
            patch.object(RUNNER, "comments", return_value=[]),
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
