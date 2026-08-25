"""Regression coverage for portable Python cache ignore rules (VOC-120)."""

from fnmatch import fnmatch
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = ROOT / ".gitignore"

PYCACHE_RULE = "__pycache__/"
BYTECODE_RULE = "*.py[cod]"


class PythonCacheIgnoreError(ValueError):
    """Fail-closed rejection when required ignore rules are missing."""

    def __init__(self, code: str, detail: str = ""):
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)
        self.code = code
        self.detail = detail


def gitignore_rule_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def validate_python_cache_ignore_rules(text: str) -> None:
    lines = gitignore_rule_lines(text)
    if PYCACHE_RULE not in lines:
        raise PythonCacheIgnoreError("missing_pycache_rule", PYCACHE_RULE)
    if BYTECODE_RULE not in lines:
        raise PythonCacheIgnoreError("missing_bytecode_rule", BYTECODE_RULE)


class PythonCacheIgnoreTests(unittest.TestCase):
    def test_repository_gitignore_exists(self):
        self.assertTrue(GITIGNORE.is_file())

    def test_repository_gitignore_has_required_rules(self):
        validate_python_cache_ignore_rules(GITIGNORE.read_text(encoding="utf-8"))

    def test_bytecode_rule_covers_variants(self):
        for extension in (".pyc", ".pyo", ".pyd"):
            self.assertTrue(
                fnmatch(f"module{extension}", BYTECODE_RULE),
                f"expected {BYTECODE_RULE} to match module{extension}",
            )

    def test_missing_pycache_rule_fails_closed(self):
        with self.assertRaises(PythonCacheIgnoreError) as caught:
            validate_python_cache_ignore_rules(f"{BYTECODE_RULE}\n")
        self.assertEqual(caught.exception.code, "missing_pycache_rule")

    def test_missing_bytecode_rule_fails_closed(self):
        with self.assertRaises(PythonCacheIgnoreError) as caught:
            validate_python_cache_ignore_rules(f"{PYCACHE_RULE}\n")
        self.assertEqual(caught.exception.code, "missing_bytecode_rule")

    def test_empty_gitignore_fails_closed(self):
        with self.assertRaises(PythonCacheIgnoreError) as caught:
            validate_python_cache_ignore_rules("")
        self.assertEqual(caught.exception.code, "missing_pycache_rule")


if __name__ == "__main__":
    unittest.main()
