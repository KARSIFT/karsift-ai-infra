from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    "normalize_review_narrative",
    ROOT / "config/normalize-review-narrative.py",
)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("cannot load review narrative normalizer")
normalizer = module_from_spec(SPEC)
SPEC.loader.exec_module(normalizer)


class ReviewNarrativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = (ROOT / ".github/workflows/review.yml").read_text()
        cls.plan_review = (ROOT / ".github/workflows/plan-review.yml").read_text()

    def test_preamble_and_workflow_binding_lookalikes_are_removed(self):
        raw = b"""I am checking the task-scoped change.
**Independent verification - bound to commit `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`**
task_id: `VOC-097-T03`
package_path: `specs/changes/VOC-097-example`
authority_issue: `828`
base_sha: `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`

## Findings
No blocking findings.

VERDICT: PASS
"""
        normalized = normalizer.normalize_narrative(raw)
        self.assertTrue(normalized.startswith("I am checking"))
        self.assertIn("## Findings", normalized)
        self.assertNotIn("bound to commit", normalized)
        for key in ("task_id:", "package_path:", "authority_issue:", "base_sha:"):
            self.assertNotIn(key, normalized)
        self.assertTrue(normalized.endswith("VERDICT: PASS\n"))

    def test_conflicting_case_insensitive_binding_lines_are_removed(self):
        raw = b"""- > ### **PACKAGE_PATH: `conflicting`**
1234. _Base_SHA: `not-authoritative`_
*authority_issue: `999`*
The workflow supplies the authoritative identity.
VERDICT: PASS WITH NON-BLOCKING FINDINGS
"""
        normalized = normalizer.normalize_narrative(raw)
        self.assertNotIn("conflicting", normalized)
        self.assertNotIn("not-authoritative", normalized)
        self.assertNotIn("999", normalized)
        self.assertIn("The workflow supplies", normalized)

    def test_duplicate_or_non_final_verdict_still_fails_closed(self):
        fixtures = (
            b"VERDICT: PASS\nVERDICT: FAIL\n",
            b"**VERDICT: PASS**\nVERDICT: FAIL\n",
            b"**VERDICT: FAIL**\nVERDICT: PASS\n",
            b"VERDICT: PASS\ntrailing prose\n",
            b"task_id: `VOC-097-T03`\n",
        )
        for raw in fixtures:
            with self.subTest(raw=raw), self.assertRaises(normalizer.NarrativeError):
                normalizer.normalize_narrative(raw)

    def test_both_review_workflows_use_normalized_narrative(self):
        for workflow in (self.review, self.plan_review):
            with self.subTest(workflow=workflow.splitlines()[0]):
                self.assertIn("normalize-review-narrative.py", workflow)
                record_step = workflow.split(
                    "- name: Build verification record for isolated publisher", 1
                )[1].split("- name: Upload verification record", 1)[0]
                self.assertIn("cat /tmp/verdict-narrative.md", record_step)
                self.assertNotIn("cat /tmp/verdict.md", record_step)


if __name__ == "__main__":
    unittest.main()
