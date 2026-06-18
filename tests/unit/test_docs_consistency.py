"""Tests for documentation consistency (J② + J⑤).

Ensures docs don't drift from code:
- LLM provider count matches LLM_PROVIDERS
- SECURITY supported version matches pyproject.toml
- No stale repo URLs
- No empty markdown links
- README strategy example signature matches actual code
"""

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

README_PATH = ROOT / "README.md"
SECURITY_PATH = ROOT / "SECURITY.md"
CONTRIBUTING_PATH = ROOT / "CONTRIBUTING.md"
CLAUDE_PATH = ROOT / "CLAUDE.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"


def _get_pyproject_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["project"]["version"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestLLMProviderCount:
    """J②: README LLM provider count matches code."""

    def test_no_stale_count_in_readme(self):
        """README should not contain stale '11 家' provider count."""
        content = _read(README_PATH)
        assert "11 家" not in content, "README still references '11 家' LLM providers (should be '10 家')"

    def test_provider_count_matches_code(self):
        """README '10 家' count matches actual LLM_PROVIDERS dict size (excluding custom)."""
        from utils.llm_providers import LLM_PROVIDERS

        named_providers = [k for k in LLM_PROVIDERS if k != "custom"]
        expected_count = len(named_providers)
        content = _read(README_PATH)
        # README should reference the correct count
        pattern = rf"{expected_count} 家"
        assert re.search(pattern, content), (
            f"README should reference '{expected_count} 家' LLM providers (found {len(named_providers)} in code)"
        )


class TestSecurityVersion:
    """J②: SECURITY.md supported version matches pyproject.toml."""

    def test_security_version_matches_pyproject(self):
        pyproject_ver = _get_pyproject_version()
        major_minor = ".".join(pyproject_ver.split(".")[:2])
        content = _read(SECURITY_PATH)
        pattern = rf"\| {re.escape(major_minor)}\.x\s+\| :white_check_mark:"
        assert re.search(pattern, content), (
            f"SECURITY.md should list '{major_minor}.x' as supported (pyproject version: {pyproject_ver})"
        )

    def test_no_stale_version_in_security(self):
        """SECURITY.md should not contain stale '1.x.x' version."""
        content = _read(SECURITY_PATH)
        assert "1.x.x" not in content, "SECURITY.md still references '1.x.x' (should be '0.7.x')"


class TestRepoUrlConsistency:
    """J②: No stale repo URLs in docs."""

    @pytest.mark.parametrize("doc_path", [README_PATH, CONTRIBUTING_PATH, SECURITY_PATH])
    def test_no_stale_repo_url(self, doc_path):
        content = _read(doc_path)
        assert "louis2sin/AStockScreener" not in content, (
            f"{doc_path.name} contains stale repo URL 'louis2sin/AStockScreener'"
        )


class TestEmptyMarkdownLinks:
    """J②: No empty markdown links in README."""

    def test_no_empty_links_in_readme(self):
        content = _read(README_PATH)
        for i, line in enumerate(content.splitlines(), 1):
            assert "]()" not in line, f"README.md:{i} contains empty markdown link ']()'"


class TestStrategyExampleSignature:
    """J⑤: README strategy example signature matches actual code."""

    def test_readme_uses_real_filter_signature(self):
        """README should use 'async def filter(self, context: StrategyContext)' not '_filter_logic'."""
        content = _read(README_PATH)
        # The example should use the real filter() method, not _filter_logic
        assert "async def filter(self, context: StrategyContext)" in content, (
            "README strategy example should use 'async def filter(self, context: StrategyContext)'"
        )
        # Should not use the wrong _filter_logic signature from PolarsBaseStrategy
        # (OversoldStrategy inherits BaseStrategy, not PolarsBaseStrategy)
        wrong_pattern = r"def _filter_logic\(self, lf: pl\.LazyFrame, context: dict\)"
        assert not re.search(wrong_pattern, content), (
            "README strategy example should not use '_filter_logic(self, lf, context: dict)' "
            "(OversoldStrategy uses filter(), not _filter_logic)"
        )

    def test_claude_uses_tuple_not_list(self):
        """CLAUDE.md strategy example should use tuple, not list for required_context_keys."""
        content = _read(CLAUDE_PATH)
        # Should use tuple syntax
        assert "required_context_keys: tuple[str, ...]" in content, (
            "CLAUDE.md should use 'tuple[str, ...]' for required_context_keys"
        )
        # Should not use list syntax
        wrong_pattern = r"required_context_keys\s*=\s*\["
        assert not re.search(wrong_pattern, content), (
            "CLAUDE.md should not use list syntax for required_context_keys (should be tuple)"
        )
