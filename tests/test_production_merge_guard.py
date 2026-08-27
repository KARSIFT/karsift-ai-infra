import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from production_merge_guard import (  # noqa: E402
    ProductionMergeGuardError,
    validate_production_merge_guard,
)


REPOSITORY = "KARSIFT/example"


def effective(*, strict=True, source=REPOSITORY, ruleset_id=42):
    return [
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": strict,
                "required_status_checks": [{"context": "ci"}],
            },
            "ruleset_source_type": "Repository",
            "ruleset_source": source,
            "ruleset_id": ruleset_id,
        }
    ]


def ruleset(*, bypass=None, enforcement="active", strict=True):
    return {
        "id": 42,
        "target": "branch",
        "source_type": "Repository",
        "source": REPOSITORY,
        "enforcement": enforcement,
        "bypass_actors": [] if bypass is None else bypass,
        "rules": [
            {"type": "pull_request", "parameters": {}},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": strict,
                    "required_status_checks": [{"context": "ci"}],
                },
            },
        ],
    }


class ProductionMergeGuardTests(unittest.TestCase):
    def test_accepts_effective_active_repository_rule_without_bypass(self):
        self.assertEqual(
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(),
                rulesets=[ruleset()],
            ),
            42,
        )

    def test_rejects_non_strict_effective_rule(self):
        with self.assertRaisesRegex(
            ProductionMergeGuardError, "production_merge_guard_missing"
        ):
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(strict=False),
                rulesets=[ruleset()],
            )

    def test_rejects_empty_or_malformed_required_check_lists(self):
        for effective_checks, full_checks in (
            ([], [{"context": "ci"}]),
            ([{"context": "ci"}], []),
            ([{"context": ""}], [{"context": "ci"}]),
            ([{"context": "ci"}], [{}]),
        ):
            effective_rules = effective()
            effective_rules[0]["parameters"]["required_status_checks"] = (
                effective_checks
            )
            full_ruleset = ruleset()
            full_ruleset["rules"][1]["parameters"]["required_status_checks"] = (
                full_checks
            )
            with self.subTest(
                effective_checks=effective_checks, full_checks=full_checks
            ):
                with self.assertRaises(ProductionMergeGuardError):
                    validate_production_merge_guard(
                        repository=REPOSITORY,
                        effective_rules=effective_rules,
                        rulesets=[full_ruleset],
                    )

    def test_rejects_app_or_any_other_bypass(self):
        with self.assertRaises(ProductionMergeGuardError):
            validate_production_merge_guard(
                repository=REPOSITORY,
                effective_rules=effective(),
                rulesets=[
                    ruleset(
                        bypass=[
                            {
                                "actor_id": 4354360,
                                "actor_type": "Integration",
                                "bypass_mode": "always",
                            }
                        ]
                    )
                ],
            )

    def test_rejects_inactive_mismatched_or_incomplete_rulesets(self):
        variants = [
            ruleset(enforcement="evaluate"),
            {**ruleset(), "source": "KARSIFT/other"},
            {**ruleset(), "rules": [ruleset()["rules"][1]]},
            ruleset(strict=False),
        ]
        for candidate in variants:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ProductionMergeGuardError):
                    validate_production_merge_guard(
                        repository=REPOSITORY,
                        effective_rules=effective(),
                        rulesets=[candidate],
                    )

    def test_runner_bounds_invalid_json_error(self):
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            effective_path = directory_path / "effective.json"
            rulesets_path = directory_path / "rulesets.json"
            effective_path.write_text("not-json SECRET_VALUE")
            rulesets_path.write_text(json.dumps([ruleset()]))
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "config/production-merge-guard-runner.py"),
                    "--repository",
                    REPOSITORY,
                    "--effective-rules-file",
                    str(effective_path),
                    "--rulesets-file",
                    str(rulesets_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("production_merge_rules_unreadable", result.stderr)
        self.assertNotIn("SECRET_VALUE", result.stderr)


if __name__ == "__main__":
    unittest.main()
