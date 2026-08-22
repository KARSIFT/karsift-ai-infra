import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from task_completion import (  # noqa: E402
    BOT_LOGIN,
    CompletionError,
    marker_body,
    parse_pr_authority,
    validate_comments,
)


EXPECTED = {
    "repository": "KARSIFT/caller",
    "authority_issue": "17",
    "package_path": "specs/changes/VOC-108-example",
    "task_id": "VOC-108-T00",
}
RECORD = {
    **EXPECTED,
    "pr_number": "18",
    "reviewed_head_sha": "a" * 40,
    "merge_commit_sha": "b" * 40,
    "merged_at": "2026-08-22T00:00:10Z",
}
PR = {
    "number": 18,
    "state": "closed",
    "merged_at": RECORD["merged_at"],
    "head": {"sha": RECORD["reviewed_head_sha"]},
    "merge_commit_sha": RECORD["merge_commit_sha"],
}


def comment(**overrides):
    value = {
        "body": marker_body(RECORD),
        "user": {"login": BOT_LOGIN, "type": "Bot"},
        "created_at": "2026-08-22T00:00:11Z",
    }
    value.update(overrides)
    return value


class TaskCompletionTests(unittest.TestCase):
    def test_live_pr_authority_requires_one_matching_canonical_identity(self):
        self.assertEqual(
            parse_pr_authority(
                "Implements task `VOC-108-T00`\n"
                "Package path: `specs/changes/VOC-108-example`\n"
                "Closes #17"
            ),
            {
                "authority_issue": "17",
                "package_path": "specs/changes/VOC-108-example",
                "task_id": "VOC-108-T00",
            },
        )

    def test_live_pr_authority_rejects_ambiguity_and_cross_change_identity(self):
        cases = (
            "Implements task `VOC-108-T00`\n"
            "Package path: `specs/changes/VOC-108-example`\n"
            "Closes #17\nCloses #18",
            "Implements task `VOC-109-T00`\n"
            "Package path: `specs/changes/VOC-108-example`\n"
            "Closes #17",
            "Implements task `VOC-108-T00`\n"
            "Package path: `../VOC-108-example`\n"
            "Closes #17",
        )
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(CompletionError):
                    parse_pr_authority(body)

    def test_valid_app_marker_matches_live_caller_merge(self):
        self.assertEqual(
            validate_comments([comment()], expected=EXPECTED, pull_request=PR, issue_state="CLOSED"),
            RECORD,
        )

    def test_closed_state_alone_and_duplicate_or_human_markers_fail(self):
        for comments in (
            [],
            [comment(), comment()],
            [comment(user={"login": "human", "type": "User"})],
        ):
            with self.subTest(count=len(comments)):
                with self.assertRaises(CompletionError):
                    validate_comments(comments, expected=EXPECTED, pull_request=PR, issue_state="CLOSED")

    def test_stale_foreign_unmerged_and_premerge_records_fail(self):
        cases = [
            ([comment()], {**EXPECTED, "repository": "foreign/repo"}, PR, "CLOSED"),
            ([comment()], EXPECTED, {**PR, "head": {"sha": "c" * 40}}, "CLOSED"),
            ([comment()], EXPECTED, {**PR, "state": "open", "merged_at": None}, "CLOSED"),
            ([comment(created_at="2026-08-22T00:00:09Z")], EXPECTED, PR, "CLOSED"),
            ([comment()], EXPECTED, PR, "OPEN"),
        ]
        for comments, expected, pr, state in cases:
            with self.assertRaises(CompletionError):
                validate_comments(comments, expected=expected, pull_request=pr, issue_state=state)


if __name__ == "__main__":
    unittest.main()
