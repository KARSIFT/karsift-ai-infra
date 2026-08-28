"""VOC-138 promotion PR pr-validation and recovery semantic equivalence tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from promotion_status_attestation import (
    AttestationError,
    verify_promotion_required_run_semantics,
)


REPOSITORY = "KARSIFT/example"
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
RUN_ID = 321
PR_NUMBER = 947


def run_payload(
    *,
    event: str = "workflow_dispatch",
    path: str = ".github/workflows/pipeline.yml",
) -> dict:
    repository = {"full_name": REPOSITORY}
    return {
        "id": RUN_ID,
        "event": event,
        "path": path,
        "display_title": "pipeline",
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD_SHA,
        "head_branch": "develop",
        "repository": repository,
        "pull_requests": [
            {
                "number": PR_NUMBER,
                "base": {
                    "sha": BASE_SHA,
                    "ref": "main",
                    "repo": repository,
                },
                "head": {
                    "sha": HEAD_SHA,
                    "ref": "develop",
                    "repo": repository,
                },
            }
        ],
    }


def verify(payload: dict, *, context: str = "ci / ci") -> None:
    verify_promotion_required_run_semantics(
        payload,
        context=context,
        run_id=RUN_ID,
        repository=REPOSITORY,
        pr_number=PR_NUMBER,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        base_ref="main",
        head_ref="develop",
    )


class Voc138PromotionPrProvenanceTests(unittest.TestCase):
    def test_pull_request_ci_run_is_attestable(self):
        verify(run_payload(event="pull_request"))

    def test_recover_promotion_pr_checks_dispatch_is_attestable(self):
        # GitHub exposes the workflow name, not the dispatch action, as this
        # title. Binding comes from immutable run/PR metadata and the exact
        # caller workflow that produced the successful ci / ci check.
        verify(run_payload())

    def test_every_pr_bound_required_workflow_dispatch_is_attestable(self):
        workflows = {
            "governance-policy": ".github/workflows/governance-policy.yml",
            "validate": ".github/workflows/repository-governance.yml",
            "ci / ci": ".github/workflows/pipeline.yml",
        }
        for context, path in workflows.items():
            with self.subTest(context=context):
                verify(run_payload(path=path), context=context)

    def test_squash_safe_dispatch_is_rejected(self):
        with self.assertRaisesRegex(
            AttestationError, "untrusted_ci_recovery_pr_binding"
        ):
            payload = run_payload()
            payload["pull_requests"] = []
            verify(payload)

    def test_every_run_and_pr_identity_mismatch_is_rejected(self):
        cases = {
            "run id": ("id", RUN_ID + 1),
            "head sha": ("head_sha", "c" * 40),
            "head branch": ("head_branch", "feature"),
            "repository": ("repository", {"full_name": "OTHER/repo"}),
            "status": ("status", "in_progress"),
            "conclusion": ("conclusion", "failure"),
            "workflow": ("path", ".github/workflows/other.yml"),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label):
                payload = run_payload()
                payload[field] = value
                with self.assertRaises(AttestationError):
                    verify(payload)

        pr_cases = {
            "pr number": ("number", PR_NUMBER + 1),
            "base sha": ("base.sha", "c" * 40),
            "base ref": ("base.ref", "staging"),
            "head sha": ("head.sha", "c" * 40),
            "head ref": ("head.ref", "feature"),
        }
        for label, (field, value) in pr_cases.items():
            with self.subTest(label=label):
                payload = run_payload()
                target = payload["pull_requests"][0]
                if "." in field:
                    section, key = field.split(".", 1)
                    target[section][key] = value
                else:
                    target[field] = value
                with self.assertRaisesRegex(
                    AttestationError, "untrusted_ci_recovery_pr_binding"
                ):
                    verify(payload)


if __name__ == "__main__":
    unittest.main()
