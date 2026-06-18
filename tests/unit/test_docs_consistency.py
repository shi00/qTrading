"""Tests for documentation consistency (J② + J⑤).

Ensures real project files don't drift:
- Version consistency: installer.iss / release-please-manifest / pyright versions match pyproject.toml
- LLM provider count matches LLM_PROVIDERS
- SECURITY supported version matches pyproject.toml
- No stale repo URLs
- No empty markdown links
- CLAUDE.md references point to existing files
- README strategy example signature matches actual code
"""

import json
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
INSTALLER_PATH = ROOT / "installer.iss"
PACKAGE_JSON_PATH = ROOT / "package.json"
RELEASE_MANIFEST_PATH = ROOT / ".release-please-manifest.json"
CI_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci_cd.yml"


def _get_pyproject_version() -> str:
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg["project"]["version"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestVersionConsistency:
    """Check 1-3: Real file version consistency (mirrors verify_versions.py)."""

    def test_installer_version_matches_pyproject(self):
        """installer.iss fallback version must match pyproject.toml version."""
        content = _read(INSTALLER_PATH)
        m = re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', content)
        assert m, f"Could not find MyAppVersion in {INSTALLER_PATH}"
        installer_ver = m.group(1)
        pyproject_ver = _get_pyproject_version()
        assert installer_ver == pyproject_ver, (
            f"installer.iss version '{installer_ver}' != pyproject.toml version '{pyproject_ver}'"
        )

    def test_pyright_versions_match(self):
        """package.json pyright version must match CI pinned pyright version."""
        with open(PACKAGE_JSON_PATH, encoding="utf-8") as f:
            pkg_ver = json.load(f)["devDependencies"]["pyright"]
        ci_content = _read(CI_WORKFLOW_PATH)
        m = re.search(r"pip install pyright==(\S+)", ci_content)
        assert m, f"Could not find pyright version in {CI_WORKFLOW_PATH}"
        ci_ver = m.group(1)
        assert pkg_ver == ci_ver, f"package.json pyright '{pkg_ver}' != CI pyright '{ci_ver}'"

    def test_release_manifest_version_matches_pyproject(self):
        """.release-please-manifest.json version must match pyproject.toml version."""
        with open(RELEASE_MANIFEST_PATH, encoding="utf-8") as f:
            manifest_ver = json.load(f)["."]
        pyproject_ver = _get_pyproject_version()
        assert manifest_ver == pyproject_ver, (
            f".release-please-manifest.json version '{manifest_ver}' != pyproject.toml version '{pyproject_ver}'"
        )


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


class TestClaudeReferences:
    """Check 7: CLAUDE.md reference-style pointers target existing files."""

    def test_claude_file_references_exist(self):
        """CLAUDE.md '见 `xxx.py`' references should point to existing files."""
        content = _read(CLAUDE_PATH)
        refs = re.findall(r"见 `([^`]+)`", content)
        for ref in refs:
            if ref.startswith("§") or not (
                ref.endswith(".py") or ref.endswith(".yml") or ref.endswith(".yaml") or ref.endswith(".json")
            ):
                continue
            target = ROOT / ref
            if target.exists():
                continue
            matches = list(ROOT.rglob(ref))
            assert matches, f"CLAUDE.md references '{ref}' but file does not exist"


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
