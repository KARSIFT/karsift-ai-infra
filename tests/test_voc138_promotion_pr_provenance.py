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


class Voc138PromotionPrProvenanceTests(unittest.TestCase):
    def test_pull_request_ci_run_is_attestable(self):
        verify_promotion_required_run_semantics(
            {
                "event": "pull_request",
                "path": ".github/workflows/pipeline.yml",
                "display_title": "ci / ci",
            },
            context="ci / ci",
        )

    def test_recover_promotion_pr_checks_dispatch_is_attestable(self):
        verify_promotion_required_run_semantics(
            {
                "event": "workflow_dispatch",
                "path": ".github/workflows/pipeline.yml",
                "display_title": "recover-promotion-pr-checks",
            },
            context="ci / ci",
        )

    def test_squash_safe_dispatch_is_rejected(self):
        with self.assertRaisesRegex(
            AttestationError, "untrusted_ci_recovery_semantics"
        ):
            verify_promotion_required_run_semantics(
                {
                    "event": "workflow_dispatch",
                    "path": ".github/workflows/pipeline.yml",
                    "display_title": "recover-integration-push",
                },
                context="ci / ci",
            )


if __name__ == "__main__":
    unittest.main()
