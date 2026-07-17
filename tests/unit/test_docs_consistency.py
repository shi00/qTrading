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

pytestmark = pytest.mark.unit

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


def _get_coverage_sources() -> list[str]:
    """从 pyproject.toml 读取 [tool.coverage.run] source 配置。"""
    with open(PYPROJECT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    try:
        return cfg["tool"]["coverage"]["run"]["source"]
    except KeyError as e:
        raise AssertionError(f"pyproject.toml missing [tool.coverage.run] source config (key {e} not found)") from e


def _count_business_daos() -> int:
    """统计 data/persistence/daos/ 下业务 DAO 文件数（排除 base_dao.py）。"""
    daos_dir = ROOT / "data" / "persistence" / "daos"
    return sum(1 for f in daos_dir.glob("*_dao.py") if f.name != "base_dao.py")


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
        """CLAUDE.md or strategy template doc should use tuple, not list for required_context_keys."""
        strategy_template_path = ROOT / "docs" / "patterns" / "strategy-template.md"
        content = _read(CLAUDE_PATH) + "\n" + _read(ROOT / "CONTRIBUTING.md")
        if strategy_template_path.exists():
            content += "\n" + _read(strategy_template_path)
        # Should use tuple syntax
        assert "required_context_keys: tuple[str, ...]" in content, (
            "CLAUDE.md, CONTRIBUTING.md, or docs/patterns/strategy-template.md should use 'tuple[str, ...]' for required_context_keys"
        )
        # Should not use list syntax
        wrong_pattern = r"required_context_keys\s*=\s*\["
        assert not re.search(wrong_pattern, content), (
            "should not use list syntax for required_context_keys (should be tuple)"
        )


class TestTrackedDocsLinksResolve:
    """审计报告 P0: 被跟踪 markdown 中指向 docs/ 的链接目标必须存在。

    docs/ 已从 .gitignore 移除，转为正式文档目录；引用 docs/ 路径的链接
    应解析到真实存在的文件，避免死链。沿用 check_relative_dead_links 逻辑：
    扫描 markdown 链接 [text](url)，跳过外部链接与 fenced code block。
    """

    # CHANGELOG.md 由 release-please 自动生成，不纳入手动检查
    TRACKED_MD_FILES = [
        README_PATH,
        CONTRIBUTING_PATH,
        SECURITY_PATH,
        CLAUDE_PATH,
        ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
        ROOT / "man" / "database-account-separation.md",
        ROOT / "man" / "table-partitioning-strategy.md",
    ]

    @pytest.mark.parametrize("doc_path", TRACKED_MD_FILES)
    def test_docs_links_resolve(self, doc_path):
        """被跟踪 markdown 中指向 docs/ 的链接目标必须存在（沿用 check_relative_dead_links 逻辑）。"""
        content = _read(doc_path)
        link_pattern = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
        in_code_block = False
        for line_no, line in enumerate(content.splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            for m in link_pattern.finditer(line):
                url = m.group(2).strip()
                # 忽略外部链接
                if url.startswith(("http://", "https://", "mailto:")):
                    continue
                # 解析路径部分（去掉锚点），同文件锚点链接跳过
                path_part = url.split("#", 1)[0]
                if not path_part:
                    continue
                # 从 source_doc 所在目录解析相对路径
                target = (doc_path.parent / path_part).resolve()
                # 只检查指向 docs/ 目录的链接
                try:
                    target.relative_to(ROOT / "docs")
                except ValueError:
                    continue
                assert target.exists(), f"{doc_path.name}:{line_no}: 指向 docs/ 的死链 '{url}' (目标 '{target}' 不存在)"


class TestCoverageSourceConsistency:
    """审计报告 P1: README/CONTRIBUTING 覆盖率源清单与 pyproject.toml 一致。"""

    def test_readme_coverage_source_matches_pyproject(self):
        """README.md 覆盖率源模块路径清单与 pyproject.toml source 一致。"""
        sources = _get_coverage_sources()
        content = _read(README_PATH)
        # 每个 source 模块都应以路径形式（如 `core/`）出现在 README 覆盖率维度表
        for module in sources:
            assert f"`{module}/`" in content, (
                f"README.md coverage table missing path `{module}/` (pyproject source: {sources})"
            )
        # "X 个核心模块"中的数字应等于 source 数量（要求阿拉伯数字）
        m = re.search(r"(\d+)\s*个核心模块", content)
        assert m, f"README.md missing 'X 个核心模块' count declaration (should use Arabic numerals, sources: {sources})"
        declared = int(m.group(1))
        assert declared == len(sources), (
            f"README.md declares {declared} 核心模块 but pyproject.toml has {len(sources)} sources: {sources}"
        )

    def test_contributing_coverage_source_matches_pyproject(self):
        """CONTRIBUTING.md 覆盖率源模块名清单与 pyproject.toml source 一致。"""
        sources = _get_coverage_sources()
        content = _read(CONTRIBUTING_PATH)
        # 每个 source 模块都应以反引号包裹的名称形式（如 `core`）出现在 CONTRIBUTING
        for module in sources:
            assert f"`{module}`" in content, f"CONTRIBUTING.md missing module `{module}` (pyproject source: {sources})"


class TestDaoCountConsistency:
    """审计报告 P1: README mermaid 图 DAO 数量与实际代码一致。"""

    def test_dao_count_matches_readme(self):
        """README.md 'X 个业务 DAO + Base' 数量与 data/persistence/daos/ 实际文件数一致。"""
        actual = _count_business_daos()
        content = _read(README_PATH)
        m = re.search(r"(\d+)\s*个业务\s*DAO\s*[+＋]\s*Base", content)
        assert m, "README.md missing 'X 个业务 DAO + Base' count declaration"
        declared = int(m.group(1))
        assert declared == actual, (
            f"README.md declares {declared} 业务 DAO but data/persistence/daos/ has {actual} (excluding base_dao.py)"
        )


class TestDocsConsistencyScript:
    """C5: scripts/check_docs_consistency.py 契约测试。

    验证 doc-lint 第一阶段三项检查（锚点死链 / 版本一致 / pre-commit hook 数量）正确工作。
    """

    def test_github_anchor_emoji_heading(self):
        """带 emoji 的标题应生成双连字符锚点（GitHub 行为：不折叠连续空格）。"""
        from check_docs_consistency import github_anchor

        # "3.1 ❌ 绝对禁止" → 移除 "." 和 "❌" → "31  绝对禁止" → "31--绝对禁止"
        assert github_anchor("3.1 ❌ 绝对禁止") == "31--绝对禁止"
        assert github_anchor("3.2 ✅ 强制要求") == "32--强制要求"

    def test_github_anchor_cjk_and_punctuation(self):
        """CJK 保留，标点/括号移除。"""
        from check_docs_consistency import github_anchor

        assert github_anchor("语言切换响应 (I18n Hot Reload)") == "语言切换响应-i18n-hot-reload"
        assert github_anchor("V1 声明式 UI 开发规范") == "v1-声明式-ui-开发规范"

    def test_check_anchor_dead_links_passes(self):
        """CLAUDE.md 与 CONTRIBUTING.md 不含死锚点。"""
        from check_docs_consistency import check_anchor_dead_links

        errors = check_anchor_dead_links()
        assert errors == [], "Dead anchor links found:\n  " + "\n  ".join(errors)

    def test_check_version_consistency_passes(self):
        """CLAUDE.md 顶部版本与 pyproject.toml 一致。"""
        from check_docs_consistency import check_version_consistency

        errors = check_version_consistency()
        assert errors == [], "Version mismatch:\n  " + "\n  ".join(errors)

    def test_check_precommit_hook_count_passes(self):
        """文档中 pre-commit hook 数量与 .pre-commit-config.yaml 一致。"""
        from check_docs_consistency import check_precommit_hook_count

        errors = check_precommit_hook_count()
        assert errors == [], "Hook count mismatch:\n  " + "\n  ".join(errors)

    def test_main_returns_zero(self):
        """脚本 main() 在当前文档状态下应返回 0（全部通过）。"""
        from check_docs_consistency import main

        assert main() == 0, "check_docs_consistency.py main() should return 0 when all checks pass"

    def test_count_local_hooks_matches_config(self):
        """_count_local_hooks 返回 .pre-commit-config.yaml 实际 hook 数量。"""
        from check_docs_consistency import _count_local_hooks

        count = _count_local_hooks()
        assert count >= 8, f"Expected at least 8 local hooks, got {count}"

    def test_check_note_lazy_format_passes(self):
        """现有代码库所有 NOTE(lazy) 标记都含三要素（ceiling + upgrade）。"""
        from check_docs_consistency import check_note_lazy_format

        errors = check_note_lazy_format()
        assert errors == [], "NOTE(lazy) missing three-element format:\n  " + "\n  ".join(errors)


class TestNoteLazyFormatDetection:
    """C5 第二阶段 3a: NOTE(lazy) 三要素格式检查的纯函数测试。

    直接调用 _check_note_lazy_in_text 验证块识别与要素校验逻辑，
    避免构造临时 .py 文件的开销。
    """

    def test_single_line_all_elements(self):
        """单行格式：所有三要素在 NOTE(lazy): 同行。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "# NOTE(lazy): except Exception 保留. ceiling: 38处策略层异常. upgrade: 策略层重构.\n"
        issues = _check_note_lazy_in_text(content)
        assert issues == [], f"Should not flag valid single-line NOTE(lazy): {issues}"

    def test_multiline_hash_comments(self):
        """多行 # 注释格式：ceiling/upgrade 在后续 # 注释行。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = (
            "# NOTE(lazy): _on_exit 不触发 state 变化.\n"
            "#   ceiling: exit cleanup 5s 窗口内 Retry 可点击.\n"
            "#   upgrade: 重写为 EXITING 状态时处理.\n"
        )
        issues = _check_note_lazy_in_text(content)
        assert issues == [], f"Should not flag valid multi-line # NOTE(lazy): {issues}"

    def test_docstring_multiline_format(self):
        """docstring 内多行格式：ceiling/upgrade 在后续 docstring 行。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = (
            '"""BacktestState.\n\n'
            "    NOTE(lazy): result 字段类型为 BacktestResult | None.\n"
            "    dataclass 领域对象, 内部含 pl.DataFrame/pl.Series.\n"
            "    ceiling: BacktestResult 拆解为 tuple[Row, ...] 需重写 Panel.\n"
            "    upgrade: BacktestResultPanel 接收 tuple[Row, ...] 时移除自定义 __eq__.\n"
            '    """\n'
        )
        issues = _check_note_lazy_in_text(content)
        assert issues == [], f"Should not flag valid docstring NOTE(lazy): {issues}"

    def test_missing_ceiling_flagged(self):
        """缺 ceiling: 应被标记。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "# NOTE(lazy): xxx. upgrade: B.\n"
        issues = _check_note_lazy_in_text(content)
        assert len(issues) == 1, f"Should flag 1 issue, got {issues}"
        line_idx, missing = issues[0]
        assert "ceiling:" in missing, f"Should report missing ceiling:, got {missing}"

    def test_missing_upgrade_flagged(self):
        """缺 upgrade: 应被标记。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "# NOTE(lazy): xxx. ceiling: A.\n"
        issues = _check_note_lazy_in_text(content)
        assert len(issues) == 1, f"Should flag 1 issue, got {issues}"
        line_idx, missing = issues[0]
        assert "upgrade:" in missing, f"Should report missing upgrade:, got {missing}"

    def test_missing_both_flagged(self):
        """缺 ceiling: 和 upgrade: 都应被标记。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "# NOTE(lazy): xxx without ceiling or upgrade.\n"
        issues = _check_note_lazy_in_text(content)
        assert len(issues) == 1, f"Should flag 1 issue, got {issues}"
        _, missing = issues[0]
        assert "ceiling:" in missing and "upgrade:" in missing, f"Should report both missing, got {missing}"

    def test_todo_not_flagged(self):
        """# TODO: 不触发 NOTE(lazy) 检查。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "# TODO: this is a todo without ceiling or upgrade.\n"
        issues = _check_note_lazy_in_text(content)
        assert issues == [], f"# TODO: should not be flagged as NOTE(lazy): {issues}"

    def test_note_lazy_in_fenced_code_block_not_flagged(self):
        """fenced code block 内的 NOTE(lazy) 不被检查（避免代码示例误判）。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "Some markdown.\n\n```\n# NOTE(lazy): xxx without ceiling or upgrade.\n```\n"
        issues = _check_note_lazy_in_text(content)
        assert issues == [], f"NOTE(lazy) in fenced code block should not be flagged: {issues}"

    def test_two_independent_blocks_both_flagged(self):
        """两个 NOTE(lazy) 块各缺要素，应被独立标记。"""
        from check_docs_consistency import _check_note_lazy_in_text

        content = "# NOTE(lazy): a. upgrade: A.\n# NOTE(lazy): b. ceiling: B.\n"
        issues = _check_note_lazy_in_text(content)
        assert len(issues) == 2, f"Should flag 2 independent issues, got {issues}"
        missing_set = {tuple(missing) for _, missing in issues}
        assert ("ceiling:",) in missing_set, f"First block should miss ceiling:, got {issues}"
        assert ("upgrade:",) in missing_set, f"Second block should miss upgrade:, got {issues}"

    def test_note_lazy_block_truncated_at_next_note_lazy(self):
        """NOTE(lazy) 块在遇到下一个 NOTE(lazy): 时截断（避免吞下下一块要素）。"""
        from check_docs_consistency import _check_note_lazy_in_text

        # 第一个 NOTE(lazy) 缺 ceiling/upgrade，紧邻的第二个 NOTE(lazy) 有
        # 第一个块的扫描窗口应在第二个 NOTE(lazy) 行截断，所以第一个块仍应被标记为缺要素
        content = "# NOTE(lazy): first block missing both.\n# NOTE(lazy): second. ceiling: A. upgrade: B.\n"
        issues = _check_note_lazy_in_text(content)
        assert len(issues) == 1, f"First block should be flagged, second should not. Got: {issues}"
        _, missing = issues[0]
        assert "ceiling:" in missing and "upgrade:" in missing, f"First block should miss both, got {missing}"


class TestDocsConsistencyScriptExtensions:
    """C5 第二阶段扩展：Windows 编码修复 / man/ 受检 / Flet 版本漂移 / 相对链接死链。

    覆盖 r6 检视报告 M4 修复项的契约测试。
    """

    def test_flet_best_practices_in_checked_docs(self):
        """man/flet-best-practices.md 应在 CHECKED_DOCS 中。"""
        from check_docs_consistency import CHECKED_DOCS, FLET_BEST_PRACTICES_PATH

        assert FLET_BEST_PRACTICES_PATH in CHECKED_DOCS, (
            f"FLET_BEST_PRACTICES_PATH should be in CHECKED_DOCS, got {CHECKED_DOCS}"
        )

    def test_utf8_reconfigure_no_error_on_import(self):
        """导入 check_docs_consistency 模块时 reconfigure stdout/stderr 不应抛异常。

        Windows 默认 GBK 终端下，若未 reconfigure，emoji（✅/❌）输出会触发 UnicodeEncodeError。
        模块加载时已调用 reconfigure(encoding="utf-8")，导入成功即证明不抛异常。
        """
        import importlib

        import check_docs_consistency

        # reload 重新执行模块级 reconfigure 代码，验证不抛异常
        importlib.reload(check_docs_consistency)

    def test_main_emoji_output_no_unicode_error(self):
        """main() 输出含 emoji（✅/❌）但不应触发 UnicodeEncodeError。

        无论 main() 返回 0 或 1，emoji 输出都不应触发 UnicodeEncodeError。
        """
        from check_docs_consistency import main

        try:
            main()
        except UnicodeEncodeError:
            pytest.fail("main() should not raise UnicodeEncodeError on emoji output")

    def test_resolve_target_doc_same_file_anchor(self):
        """_resolve_target_doc: 同文件锚点（#section）返回 source_doc。"""
        from check_docs_consistency import CLAUDE_PATH, _resolve_target_doc

        result = _resolve_target_doc("#section", CLAUDE_PATH)
        assert result == CLAUDE_PATH

    def test_resolve_target_doc_cross_file_from_man(self):
        """_resolve_target_doc: man/ 下 ../CLAUDE.md 应解析为 ROOT/CLAUDE.md。"""
        from check_docs_consistency import CLAUDE_PATH, FLET_BEST_PRACTICES_PATH, _resolve_target_doc

        result = _resolve_target_doc("../CLAUDE.md#section", FLET_BEST_PRACTICES_PATH)
        assert result == CLAUDE_PATH

    def test_resolve_target_doc_non_checked_target(self):
        """_resolve_target_doc: 非 CHECKED_DOCS 中的目标返回 None。"""
        from check_docs_consistency import CLAUDE_PATH, _resolve_target_doc

        # ui/hooks.py 不在 CHECKED_DOCS 中
        result = _resolve_target_doc("ui/hooks.py#section", CLAUDE_PATH)
        assert result is None

    def test_relative_dead_links_detects_broken(self, tmp_path, monkeypatch):
        """man/ 目录下含 ./nonexistent.py 的文档应报死链。"""
        from check_docs_consistency import check_relative_dead_links

        # 构造 man/ 目录下的临时文档
        man_dir = tmp_path / "man"
        man_dir.mkdir()
        tmp_doc = man_dir / "test_doc.md"
        tmp_doc.write_text("# Test\n\n[link](./nonexistent.py)\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CHECKED_DOCS", [tmp_doc])

        errors = check_relative_dead_links()
        assert len(errors) == 1, f"Should detect 1 broken link, got {errors}"
        assert "nonexistent.py" in errors[0]

    def test_relative_dead_links_valid_path(self, tmp_path, monkeypatch):
        """man/ 目录下含 ../ui/hooks.py 的文档不应报死链（ROOT/ui/hooks.py 存在）。"""
        from check_docs_consistency import check_relative_dead_links

        # 构造 man/ 目录下的临时文档，引用 ../ui/hooks.py
        man_dir = tmp_path / "man"
        man_dir.mkdir()
        # 创建 ui/hooks.py 文件
        ui_dir = tmp_path / "ui"
        ui_dir.mkdir()
        (ui_dir / "hooks.py").write_text("# stub\n", encoding="utf-8")

        tmp_doc = man_dir / "test_doc.md"
        tmp_doc.write_text("# Test\n\n[link](../ui/hooks.py)\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CHECKED_DOCS", [tmp_doc])

        errors = check_relative_dead_links()
        assert errors == [], f"Should not flag valid relative link: {errors}"

    def test_relative_dead_links_skips_anchor_links(self, tmp_path, monkeypatch):
        """含锚点的链接（./file.md#section）不应被 check_relative_dead_links 检查。"""
        from check_docs_consistency import check_relative_dead_links

        tmp_doc = tmp_path / "test_doc.md"
        # 带锚点的链接，目标文件不存在，但应由 check_anchor_dead_links 处理
        tmp_doc.write_text("# Test\n\n[link](./nonexistent.md#section)\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CHECKED_DOCS", [tmp_doc])

        errors = check_relative_dead_links()
        assert errors == [], f"Should not flag anchor links: {errors}"

    def test_flet_version_drift_detects_old_version(self, tmp_path, monkeypatch):
        """Flet 上下文中含 0.85.3（旧版本）的文档应被检测到。"""
        from check_docs_consistency import check_flet_version_drift

        tmp_doc = tmp_path / "test_doc.md"
        tmp_doc.write_text("# Test\n\nFlet 0.85.3 是当前版本。\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.FLET_VERSION_DOCS", [tmp_doc])

        errors = check_flet_version_drift()
        assert any("0.85.3" in e for e in errors), f"Should detect 0.85.3: {errors}"

    def test_flet_version_drift_detects_current_version(self, tmp_path, monkeypatch):
        """Flet 上下文中含 0.86.0（pyproject.toml 锁定版本）的文档也应被检测到。

        根据 spec「文档 SHALL NOT 硬编码 Flet 补丁版本号」，任何具体版本号都应报错。
        """
        from check_docs_consistency import check_flet_version_drift

        tmp_doc = tmp_path / "test_doc.md"
        tmp_doc.write_text("# Test\n\nFlet 0.86.0 是当前版本。\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.FLET_VERSION_DOCS", [tmp_doc])

        errors = check_flet_version_drift()
        assert any("0.86.0" in e for e in errors), f"Should detect 0.86.0: {errors}"

    def test_flet_version_drift_no_version_no_error(self, tmp_path, monkeypatch):
        """文档中无 Flet 关键词附近版本号时不报错。"""
        from check_docs_consistency import check_flet_version_drift

        tmp_doc = tmp_path / "test_doc.md"
        tmp_doc.write_text("# Test\n\n这是一个测试文档，无版本号。\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.FLET_VERSION_DOCS", [tmp_doc])

        errors = check_flet_version_drift()
        assert errors == [], f"Should not flag document without version: {errors}"

    def test_flet_version_drift_version_not_near_flet_keyword(self, tmp_path, monkeypatch):
        """版本号不在 Flet 关键词附近（前后 50 字符内）时不报错。"""
        from check_docs_consistency import check_flet_version_drift

        tmp_doc = tmp_path / "test_doc.md"
        # 版本号与 Flet 关键词距离超过 50 字符
        content = "# Test\n\n" + "Flet 是一个框架。" + "x" * 60 + " 0.85.3 是某个版本。\n"
        tmp_doc.write_text(content, encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.FLET_VERSION_DOCS", [tmp_doc])

        errors = check_flet_version_drift()
        assert errors == [], f"Should not flag version far from Flet keyword: {errors}"

    def test_flet_version_drift_lowercase_flet_keyword(self, tmp_path, monkeypatch):
        """小写 'flet' 关键词附近的版本号也应被检测到。"""
        from check_docs_consistency import check_flet_version_drift

        tmp_doc = tmp_path / "test_doc.md"
        tmp_doc.write_text("# Test\n\n使用 flet==0.85.3 进行开发。\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.FLET_VERSION_DOCS", [tmp_doc])

        errors = check_flet_version_drift()
        assert any("0.85.3" in e for e in errors), f"Should detect 0.85.3 near 'flet': {errors}"


class TestRedlinesYamlConsistency:
    """C5 第二阶段 3b: redlines.yml 机器可读映射一致性校验 (ADR-0003 推翻 3b 决策后落地)。

    校验 docs/governance/redlines.yml 与 CLAUDE.md §3.1 红线表一致:
    - YAML 解析成功 + 含 redlines key
    - 每条红线含 5 字段 (id/title/description/enforcement/human_review_required)
    - R 编号连续 append-only (R1~R18, 无缺号/重号/跳号)
    - CLAUDE.md §3.1 表格行数 = yml 条目数
    - 构造缺 R15 的 yml 验证检测
    """

    def test_redlines_yaml_file_exists(self):
        """docs/governance/redlines.yml 文件存在 (ADR-0003 决策落地前置)."""
        from check_docs_consistency import REDLINES_YAML_PATH

        assert REDLINES_YAML_PATH.exists(), f"redlines.yml should exist at {REDLINES_YAML_PATH}"

    def test_redlines_yaml_parses_successfully(self):
        """redlines.yml 可被 yaml.safe_load 解析,且含 redlines key (list)."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"redlines.yml 顶层应为 dict, 实际 {type(data)}"
        assert "redlines" in data, "redlines.yml 顶层应含 'redlines' key"
        assert isinstance(data["redlines"], list), f"'redlines' 应为 list, 实际 {type(data['redlines'])}"
        assert len(data["redlines"]) > 0, "'redlines' 不应为空"

    def test_redline_fields_complete(self):
        """每条红线含 5 个必填字段: id/title/description/enforcement/human_review_required."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        required_fields = {"id", "title", "description", "enforcement", "human_review_required"}
        for i, entry in enumerate(data["redlines"]):
            missing = required_fields - set(entry.keys())
            assert not missing, f"redlines[{i}] 缺字段: {missing}, 实际字段: {set(entry.keys())}"

    def test_redline_ids_are_sequential_append_only(self):
        """R 编号连续 append-only: R1, R2, ..., R_N, 无缺号/重号/跳号."""
        import re

        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        ids = [entry["id"] for entry in data["redlines"]]
        # 校验格式: R\d+
        id_pattern = re.compile(r"^R(\d+)$")
        parsed_nums = []
        for rid in ids:
            m = id_pattern.match(rid)
            assert m, f"R 编号格式错误: {rid} (应为 R\\d+)"
            parsed_nums.append(int(m.group(1)))
        # 校验无重号
        assert len(parsed_nums) == len(set(parsed_nums)), f"R 编号有重号: {parsed_nums}"
        # 校验连续 append-only: 1, 2, ..., N
        expected = list(range(1, len(parsed_nums) + 1))
        assert parsed_nums == expected, f"R 编号不连续 append-only: 期望 {expected}, 实际 {parsed_nums}"

    def test_redlines_count_matches_claude_md_section_3_1_table(self):
        """CLAUDE.md §3.1 红线表行数 = redlines.yml 条目数.

        CLAUDE.md §3.1 表格中以 ``| R`` 开头的行计为红线行.
        """
        import yaml

        from check_docs_consistency import CLAUDE_PATH, REDLINES_YAML_PATH

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        yml_count = len(data["redlines"])

        claude_content = CLAUDE_PATH.read_text(encoding="utf-8")
        # 提取 §3.1 红线表: 以 "| R" 开头 (markdown 表格行)
        # 格式: "| R1 | **架构越界** | ... |"
        r_lines = [line for line in claude_content.splitlines() if re.match(r"^\|\s*R\d+\s*\|", line)]
        assert len(r_lines) == yml_count, f"CLAUDE.md §3.1 表格行数 {len(r_lines)} != redlines.yml 条目数 {yml_count}"

    def test_check_redlines_yaml_consistency_passes(self):
        """check_redlines_yaml_consistency() 在当前 redlines.yml 状态下应返回空错误列表."""
        from check_docs_consistency import check_redlines_yaml_consistency

        errors = check_redlines_yaml_consistency()
        assert errors == [], "redlines.yml consistency check failed:\n  " + "\n  ".join(errors)

    def test_detects_missing_r15_in_yaml(self, tmp_path, monkeypatch):
        """构造缺 R15 的 yml, check_redlines_yaml_consistency() 应报错 (append-only 守护)."""
        import yaml

        from check_docs_consistency import check_redlines_yaml_consistency

        # 从真实 redlines.yml 读取并删除 R15, 写入临时 yml
        from check_docs_consistency import REDLINES_YAML_PATH

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        data["redlines"] = [r for r in data["redlines"] if r["id"] != "R15"]
        tmp_yml = tmp_path / "redlines_missing_r15.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        # monkeypatch 路径常量指向临时 yml
        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)

        errors = check_redlines_yaml_consistency()
        assert len(errors) > 0, "Should detect missing R15, got no errors"
        assert any("R15" in e for e in errors), f"Errors should mention R15, got: {errors}"


class TestEnforcementMapping:
    """C5 第二阶段 3c: enforcement 字段与实际 hook/CI job 映射一致性校验 (ADR-0005).

    校验 docs/governance/redlines.yml 的 enforcement 字段中声称的守护机制
    实际配置存在且粗粒度可达 (9 个不变量 N1~N9).

    测试覆盖:
    - 纯函数 _extract_enforcement_keywords / _check_enforcement_invariants 正反例
    - 辅助函数 _check_precommit_hook / _extract_workflow_run_blocks / _check_gitleaks_scan_exists
    - 集成测试 check_enforcement_mapping() 在当前项目配置下通过
    - 漂移检测: monkeypatch 替换模块级路径常量构造临时配置
    """

    # === _extract_enforcement_keywords 纯函数测试 ===

    def test_extract_keywords_check_redlines(self):
        """enforcement 含 'check_redlines.py' 关键词被正确提取."""
        from check_docs_consistency import _extract_enforcement_keywords

        kws = _extract_enforcement_keywords("pre-commit（check_redlines.py）")
        assert "check_redlines.py" in kws

    def test_extract_keywords_multiple(self):
        """enforcement 含多个关键词 (如 '安全扫描 + 仅人工评审') 被全部提取."""
        from check_docs_consistency import _extract_enforcement_keywords

        kws = _extract_enforcement_keywords("安全扫描 + 仅人工评审")
        assert "安全扫描" in kws
        assert "仅人工评审" in kws

    def test_extract_keywords_pending(self):
        """enforcement 含 '待实现' 和 '暂缓' 被识别为 pending 关键词."""
        from check_docs_consistency import _extract_enforcement_keywords

        kws = _extract_enforcement_keywords("可自动化待实现（AST 检查，暂缓：误报风险高）")
        assert "待实现" in kws
        assert "暂缓" in kws

    def test_extract_keywords_ruff_word_boundary(self):
        """'ruff' 关键词使用 word boundary 匹配, 不误匹配 'scruffian'."""
        from check_docs_consistency import _extract_enforcement_keywords

        assert "ruff" in _extract_enforcement_keywords("ruff")
        assert "ruff" in _extract_enforcement_keywords("使用 ruff 检查")
        assert "ruff" not in _extract_enforcement_keywords("scruffian")
        assert "ruff" not in _extract_enforcement_keywords("scruffy")

    # === N1: check_redlines.py 不变量测试 ===

    def test_n1_check_redlines_keyword_without_hook(self):
        """N1: enforcement 含 'check_redlines.py' 但 redline-check hook 不存在 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",  # 空 precommit → hook 不存在
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R4", "enforcement": "pre-commit（check_redlines.py）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R4" in e and "N1" in e for e in errors), f"应报 N1 错误, got: {errors}"

    def test_n1_check_redlines_keyword_with_hook_but_wrong_entry(self):
        """N1: enforcement 含 'check_redlines.py' 且 hook 存在但 entry 指向其他脚本 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: redline-check
        name: Redline
        entry: python scripts/other_script.py
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R4", "enforcement": "pre-commit（check_redlines.py）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R4" in e and "N1" in e for e in errors), f"应报 N1 entry 错误, got: {errors}"

    def test_n1_check_redlines_keyword_with_hook_and_correct_entry(self):
        """N1: enforcement 含 'check_redlines.py' 且 hook + entry + 脚本文件均正确 → 通过."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: redline-check
        name: Redline
        entry: python scripts/check_redlines.py
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R4", "enforcement": "pre-commit（check_redlines.py）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert errors == [], f"N1 正例不应报错, got: {errors}"

    def test_n1_check_redlines_script_missing(self):
        """N1: hook + entry 正确但 scripts/check_redlines.py 文件不存在 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: redline-check
        name: Redline
        entry: python scripts/check_redlines.py
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=False,  # 脚本文件不存在
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R4", "enforcement": "pre-commit（check_redlines.py）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R4" in e and "N1" in e and "文件不存在" in e for e in errors), f"应报 N1 脚本缺失, got: {errors}"

    # === N2: import-linter 不变量测试 ===

    def test_n2_import_linter_hook_missing(self):
        """N2: enforcement 含 'import-linter' 但 lint-imports hook 不存在 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",  # 无 hook
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R1", "enforcement": "pre-commit（import-linter 4 条契约）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R1" in e and "N2" in e for e in errors), f"应报 N2 错误, got: {errors}"

    def test_n2_import_linter_wrong_entry(self):
        """N2: enforcement 含 'import-linter' 且 hook 存在但 entry 不含 lint-imports → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: lint-imports
        name: Lint Imports
        entry: python scripts/other_linter.py
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R1", "enforcement": "pre-commit（import-linter 4 条契约）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R1" in e and "N2" in e for e in errors), f"应报 N2 entry 错误, got: {errors}"

    def test_n2_import_linter_no_contract_count_in_enforcement_skipped(self):
        """N2: enforcement 含 'import-linter' 但未含『N 条契约』描述 → 跳过数量校验 (不报错)."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: lint-imports
        name: Lint Imports
        entry: lint-imports
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",  # 空 pyproject
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        # enforcement 不含 "N 条契约" → 跳过数量校验
        redlines = [{"id": "R1", "enforcement": "pre-commit（import-linter）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert errors == [], f"无契约数量描述时不应报 N2 数量错误, got: {errors}"

    def test_n2_import_linter_contract_count_mismatch(self):
        """N2: enforcement 声明 '4 条契约' 但 pyproject.toml 实际 3 条 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: lint-imports
        name: Lint Imports
        entry: lint-imports
        language: system
"""
        pyproject = """[[tool.importlinter.contracts]]\nname = "c1"\n[[tool.importlinter.contracts]]\nname = "c2"\n[[tool.importlinter.contracts]]\nname = "c3"\n"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content=pyproject,  # 3 条契约
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R1", "enforcement": "pre-commit（import-linter 4 条契约）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R1" in e and "N2" in e and "4" in e and "3" in e for e in errors), (
            f"应报 N2 数量不匹配, got: {errors}"
        )

    def test_n2_import_linter_contract_count_match(self):
        """N2: enforcement 声明 '4 条契约' 且 pyproject.toml 实际 4 条 → 通过."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: lint-imports
        name: Lint Imports
        entry: lint-imports
        language: system
"""
        pyproject = """[[tool.importlinter.contracts]]\nname = "c1"\n[[tool.importlinter.contracts]]\nname = "c2"\n[[tool.importlinter.contracts]]\nname = "c3"\n[[tool.importlinter.contracts]]\nname = "c4"\n"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content=pyproject,  # 4 条契约
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R1", "enforcement": "pre-commit（import-linter 4 条契约）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert errors == [], f"N2 正例不应报错, got: {errors}"

    # === N3: ruff 不变量测试 (v3 §14.2.1 补齐) ===

    def test_n3_ruff_hook_missing(self):
        """N3: enforcement 含 'ruff' 但 ruff-check hook 不存在 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",  # 无 hook
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R6", "enforcement": "ruff", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R6" in e and "N3" in e for e in errors), f"应报 N3 错误, got: {errors}"

    def test_n3_ruff_hook_wrong_entry(self):
        """N3: enforcement 含 'ruff' 且 hook 存在但 entry 不含 ruff → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: Ruff Check
        entry: python scripts/other_linter.py
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R6", "enforcement": "ruff", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R6" in e and "N3" in e for e in errors), f"应报 N3 entry 错误, got: {errors}"

    def test_n3_ruff_hook_correct(self):
        """N3: enforcement 含 'ruff' 且 hook + entry 正确 → 通过."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        precommit = """repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: Ruff Check
        entry: python -m ruff check --fix
        language: system
"""
        env = EnforcementEnvironment(
            precommit_content=precommit,
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R6", "enforcement": "ruff", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert errors == [], f"N3 正例不应报错, got: {errors}"

    # === N4: 安全扫描不变量测试 (含 v3 §14.2.4 半配置反例) ===

    def test_n4_security_scan_requires_gitleaks_and_config(self):
        """N4: 安全扫描需同时存在 Gitleaks workflow 与 .gitleaks.toml."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        # 无 Gitleaks workflow + 无 config
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=False,
        )
        redlines = [{"id": "R9", "enforcement": "安全扫描 + 仅人工评审", "human_review_required": True}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R9" in e and "N4" in e for e in errors), f"应报 N4 错误, got: {errors}"

    def test_n4_pip_audit_alone_not_security_scan_evidence(self):
        """N4: 仅存在 pip-audit 不应被当作 R9/R10 安全扫描证据."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        # workflow 只含 pip-audit, 不含 gitleaks/gitleaks-action
        workflow = """name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pip install pip-audit
      - run: pip-audit
"""
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(workflow,),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R9", "enforcement": "安全扫描 + 仅人工评审", "human_review_required": True}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R9" in e and "N4" in e for e in errors), f"pip-audit 不应满足 N4, got: {errors}"

    def test_n4_gitleaks_workflow_exists_but_config_missing(self):
        """N4: Gitleaks workflow 存在但 .gitleaks.toml 缺失 → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        workflow = """name: Gitleaks
jobs:
  scan:
    steps:
      - uses: gitleaks/gitleaks-action@v2
"""
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(workflow,),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=False,  # config 缺失
        )
        redlines = [{"id": "R9", "enforcement": "安全扫描 + 仅人工评审", "human_review_required": True}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R9" in e and "N4" in e for e in errors), f"半配置应报 N4, got: {errors}"

    def test_n4_gitleaks_config_exists_but_workflow_missing(self):
        """N4: .gitleaks.toml 存在但所有 workflow 均无 Gitleaks → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),  # 无 workflow
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,  # config 存在
        )
        redlines = [{"id": "R9", "enforcement": "安全扫描 + 仅人工评审", "human_review_required": True}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R9" in e and "N4" in e for e in errors), f"半配置应报 N4, got: {errors}"

    # === N5: CI-test 不变量测试 ===

    def test_n5_pytest_only_matches_run_command_block(self):
        """N5: pytest 只在 run: 命令块中出现时才算 CI-test 证据."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        # workflow 含 pytest in run block → 应通过
        workflow = """name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: python -m pytest tests/unit/
"""
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(workflow,),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R2", "enforcement": "CI-test（全量，asyncio 相关测试）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert errors == [], f"N5 正例不应报错, got: {errors}"

        # 无 pytest in run block → 应报错
        env2 = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),  # 无 workflow
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        errors2 = _check_enforcement_invariants(redlines, env2)
        assert any("R2" in e and "N5" in e for e in errors2), f"无 pytest 应报 N5, got: {errors2}"

    def test_n5_pytest_in_cache_step_name_does_not_satisfy_ci_test(self):
        """N5: pytest 只出现在 step 名称或 cache key 中时, 不应满足 CI-test 映射."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        # pytest 只出现在 step name 和 cache key, 不在 run: 命令块
        workflow = """name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Cache pytest
        uses: actions/cache@v3
        with:
          path: .pytest_cache
          key: pytest-${{ runner.os }}
"""
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(workflow,),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R2", "enforcement": "CI-test（全量，asyncio 相关测试）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R2" in e and "N5" in e for e in errors), f"step name 中的 pytest 不应满足 N5, got: {errors}"

    def test_n5_pip_install_pytest_in_run_block_does_not_satisfy_ci_test(self):
        """N5: run: 块中含 'pip install pytest' → 不应被误判为满足 CI-test 映射.

        真实场景: ci_cd.yml:314 含 'pip install playwright pytest-playwright'.
        验证 'pip install pytest' 中的 pytest 不会被误匹配 (正则要求 pytest 在行首).
        """
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        workflow = """name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pip install pytest
      - run: pip install pytest-playwright
"""
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(workflow,),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R2", "enforcement": "CI-test（全量，asyncio 相关测试）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R2" in e and "N5" in e for e in errors), f"pip install pytest 不应满足 N5, got: {errors}"

    # === N6~N9: human_review_required 一致性测试 ===

    def test_n6_human_review_keyword_mismatch(self):
        """N6: enforcement 含 '仅人工评审' 但 human_review_required=false → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R5", "enforcement": "仅人工评审", "human_review_required": False}]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R5" in e and "N6" in e for e in errors), f"应报 N6 错误, got: {errors}"

    def test_n7_pending_keyword_mismatch(self):
        """N7: enforcement 含 '待实现' 但 human_review_required=true → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R16", "enforcement": "可自动化待实现", "human_review_required": True}]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R16" in e and "N7" in e for e in errors), f"应报 N7 错误, got: {errors}"

    def test_n8_reverse_invariant_violation(self):
        """N8: human_review_required=true 但 enforcement 不含 '仅人工评审' → 报错."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R5", "enforcement": "pre-commit（some-hook）", "human_review_required": True}]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R5" in e and "N8" in e for e in errors), f"应报 N8 错误, got: {errors}"

    def test_n9_deleted_no_duplicate_with_n6(self):
        """N9 已在实施后检视中删除（与 N6 触发条件等价）：相同场景仅 N6 报错，无 N9 错误."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R5", "enforcement": "仅人工评审", "human_review_required": False}]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        # N6 应报错（enforcement 含「仅人工评审」但 human_review_required=false）
        assert any("R5" in e and "N6" in e for e in errors), f"应报 N6 错误, got: {errors}"
        # N9 已删除，不应出现 N9 错误
        assert not any("N9" in e for e in errors), f"N9 已删除不应报错, got: {errors}"

    def test_missing_human_review_required_field_skipped(self):
        """yml 条目缺 human_review_required 字段时 N6~N8 跳过 (由 3b 守护字段完整性)."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        # 缺 human_review_required 字段 → N6~N8 跳过 (不报 N6/N7/N8 错误)
        redlines = [{"id": "R5", "enforcement": "仅人工评审"}]  # 无 human_review_required
        errors = _check_enforcement_invariants(redlines, env)
        n6_to_n8_errors = [e for e in errors if "N6" in e or "N7" in e or "N8" in e]
        assert n6_to_n8_errors == [], f"字段缺失时应跳过 N6~N8, got: {n6_to_n8_errors}"

    # === v3 §14.2.7 R9 多关键词 + R16 双 pending 特例 ===

    def test_n4_and_n6_both_checked_for_r9_style_enforcement(self):
        """R9 风格 enforcement='安全扫描 + 仅人工评审' 触发 N4 + N6 双重校验.

        构造 Gitleaks 缺失场景: N4 报错, N6 通过 (human_review_required=true).
        """
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),  # 无 Gitleaks workflow
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R9", "enforcement": "安全扫描 + 仅人工评审", "human_review_required": True}]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R9" in e and "N4" in e for e in errors), f"应报 N4, got: {errors}"
        # N6 应通过 (human_review_required=true 与 '仅人工评审' 一致)
        assert not any("R9" in e and "N6" in e for e in errors), f"不应报 N6, got: {errors}"

    def test_r16_dual_pending_keywords_passes_n7(self):
        """R16 enforcement='可自动化待实现（AST 检查，暂缓：误报风险高）'
        同时含 '待实现' 和 '暂缓', human_review_required=false → N7 通过 (不报错).
        """
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [
            {
                "id": "R16",
                "enforcement": "可自动化待实现（AST 检查，暂缓：误报风险高）",
                "human_review_required": False,
            }
        ]
        errors = _check_enforcement_invariants(redlines, env)
        n7_errors = [e for e in errors if "R16" in e and "N7" in e]
        assert n7_errors == [], f"R16 双 pending 特例不应报 N7, got: {n7_errors}"

    # === _extract_workflow_run_blocks 辅助函数单测 (v3 §14.2.6) ===

    def test_extract_workflow_run_blocks_excludes_step_names(self):
        """_extract_workflow_run_blocks() 不应纳入 step name 行 (只含 run: 命令块)."""
        from check_docs_consistency import _extract_workflow_run_blocks

        workflow = """name: CI
jobs:
  test:
    steps:
      - name: Run pytest tests
        run: pytest tests/
      - name: Cache pytest
        uses: actions/cache@v3
"""
        blocks = _extract_workflow_run_blocks(workflow)
        # 应只提取 1 个 block: 'pytest tests/'
        assert len(blocks) == 1, f"应提取 1 个 run block, got {len(blocks)}: {blocks}"
        assert "pytest tests/" in blocks[0]
        # step name 中的 'Run pytest tests' 不应出现在任何 block 中
        for block in blocks:
            assert "Run pytest tests" not in block, f"step name 不应被提取: {block}"
            assert "Cache pytest" not in block, f"step name 不应被提取: {block}"

    def test_extract_workflow_run_blocks_handles_four_yaml_styles(self):
        """_extract_workflow_run_blocks() 必须覆盖 4 种 YAML 写法:
        1. run: pytest (单行无引号)
        2. run: python -m pytest tests/unit/ (单行带参数)
        3. run: | + 多行命令块 (块状字面量)
        4. run: >- + 多行折叠块 (折叠去尾换行)
        """
        from check_docs_consistency import _extract_workflow_run_blocks

        workflow = """name: CI
jobs:
  test1:
    steps:
      - run: pytest
  test2:
    steps:
      - run: python -m pytest tests/unit/
  test3:
    steps:
      - run: |
          set -e
          python -m pytest tests/unit/
  test4:
    steps:
      - run: >-
          set -e
          pytest tests/integration/
"""
        blocks = _extract_workflow_run_blocks(workflow)
        # 应提取 4 个 block，按出现顺序对应 4 种 YAML 风格
        assert len(blocks) == 4, f"应提取 4 个 run block, got {len(blocks)}: {blocks}"
        # 风格 1: 单行无引号 → block 内容 = 'pytest'
        assert blocks[0] == "pytest", f"blocks[0] 应为 'pytest', got: {blocks[0]!r}"
        # 风格 2: 单行带参数 → block 内容 = 'python -m pytest tests/unit/'
        assert blocks[1] == "python -m pytest tests/unit/", f"blocks[1] 错误, got: {blocks[1]!r}"
        # 风格 3: 块状字面量 → block 含 'set -e' 和 'python -m pytest tests/unit/'
        assert "set -e" in blocks[2], f"blocks[2] 应含 'set -e', got: {blocks[2]!r}"
        assert "python -m pytest tests/unit/" in blocks[2], f"blocks[2] 应含 pytest 命令, got: {blocks[2]!r}"
        # 风格 4: 折叠块 → block 含 'set -e' 和 'pytest tests/integration/'
        assert "set -e" in blocks[3], f"blocks[3] 应含 'set -e', got: {blocks[3]!r}"
        assert "pytest tests/integration/" in blocks[3], f"blocks[3] 应含 pytest 命令, got: {blocks[3]!r}"

    def test_n5_four_yaml_styles_all_satisfy_ci_test(self):
        """N5 端到端: 4 种 YAML 风格的 pytest 命令都应让 N5 通过 (不报错)."""
        from check_docs_consistency import (
            EnforcementEnvironment,
            _check_enforcement_invariants,
        )

        # 4 种 YAML 风格各一个 workflow，都含 pytest 命令
        workflow = """name: CI
jobs:
  test1:
    steps:
      - run: pytest
  test2:
    steps:
      - run: python -m pytest tests/unit/
  test3:
    steps:
      - run: |
          set -e
          python -m pytest tests/unit/
  test4:
    steps:
      - run: >-
          set -e
          pytest tests/integration/
"""
        env = EnforcementEnvironment(
            precommit_content="",
            workflow_contents=(workflow,),
            pyproject_content="",
            check_redlines_script_exists=True,
            gitleaks_config_exists=True,
        )
        redlines = [{"id": "R2", "enforcement": "CI-test（全量，asyncio 相关测试）", "human_review_required": False}]
        errors = _check_enforcement_invariants(redlines, env)
        assert errors == [], f"4 种 YAML 风格都含 pytest，N5 不应报错, got: {errors}"

    # === 集成测试: 真实项目配置 ===

    def test_check_enforcement_mapping_passes(self):
        """当前项目配置下 check_enforcement_mapping() 应返回空错误列表."""
        from check_docs_consistency import check_enforcement_mapping

        errors = check_enforcement_mapping()
        assert errors == [], "当前项目配置应通过 3c 校验, 失败:\n  " + "\n  ".join(errors)

    # === 漂移检测: monkeypatch 构造临时配置 ===

    def test_detects_deleted_redline_check_hook(self, tmp_path, monkeypatch):
        """构造缺失 redline-check hook 的 .pre-commit-config.yaml → 应报错 (N1)."""
        from check_docs_consistency import check_enforcement_mapping

        # 真实 redlines.yml (含 R4 含 'check_redlines.py')
        from check_docs_consistency import REDLINES_YAML_PATH

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", REDLINES_YAML_PATH)

        # 构造无 redline-check hook 的 precommit
        precommit = """repos:
  - repo: local
    hooks:
      - id: ruff-check
        name: Ruff Check
        entry: python -m ruff check --fix
        language: system
"""
        tmp_precommit = tmp_path / ".pre-commit-config.yaml"
        tmp_precommit.write_text(precommit, encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.PRECOMMIT_PATH", tmp_precommit)

        errors = check_enforcement_mapping()
        assert any("N1" in e for e in errors), f"应检测到 redline-check hook 缺失, got: {errors}"

    def test_detects_wrong_hook_entry(self, tmp_path, monkeypatch):
        """构造 redline-check hook 但 entry 指向其他脚本 → 应报错 (N1)."""
        from check_docs_consistency import check_enforcement_mapping

        from check_docs_consistency import REDLINES_YAML_PATH

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", REDLINES_YAML_PATH)

        # entry 指向 other_script.py 而非 check_redlines.py
        precommit = """repos:
  - repo: local
    hooks:
      - id: redline-check
        name: Redline
        entry: python scripts/other_script.py
        language: system
"""
        tmp_precommit = tmp_path / ".pre-commit-config.yaml"
        tmp_precommit.write_text(precommit, encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.PRECOMMIT_PATH", tmp_precommit)

        errors = check_enforcement_mapping()
        assert any("N1" in e for e in errors), f"应检测到 entry 篡改, got: {errors}"

    def test_detects_gitleaks_removed_from_all_workflows(self, tmp_path, monkeypatch):
        """构造全部 workflow 文件缺失 Gitleaks → 应报错 (N4, R9/R10 enforcement 含 '安全扫描')."""
        from check_docs_consistency import check_enforcement_mapping

        from check_docs_consistency import REDLINES_YAML_PATH

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", REDLINES_YAML_PATH)

        # 构造无 Gitleaks 的 workflow 目录
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest\n", encoding="utf-8"
        )
        monkeypatch.setattr("check_docs_consistency.CI_WORKFLOW_DIR", workflows_dir)

        errors = check_enforcement_mapping()
        assert any("N4" in e for e in errors), f"应检测到 Gitleaks 缺失, got: {errors}"

    def test_detects_gitleaks_moved_to_security_workflow(self, tmp_path, monkeypatch):
        """Gitleaks 从 ci_cd.yml 迁移到 security.yml → 不应报错 (glob 扫描全部 workflow)."""
        from check_docs_consistency import check_enforcement_mapping

        from check_docs_consistency import REDLINES_YAML_PATH

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", REDLINES_YAML_PATH)

        # Gitleaks 在 security.yml 而非 ci.yml
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest\n", encoding="utf-8"
        )
        (workflows_dir / "security.yml").write_text(
            "name: Gitleaks\njobs:\n  scan:\n    steps:\n      - uses: gitleaks/gitleaks-action@v2\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CI_WORKFLOW_DIR", workflows_dir)
        # gitleaks config 存在
        tmp_config = tmp_path / ".gitleaks.toml"
        tmp_config.write_text("[allowlist]\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.GITLEAKS_CONFIG_PATH", tmp_config)

        errors = check_enforcement_mapping()
        n4_errors = [e for e in errors if "N4" in e]
        assert n4_errors == [], f"Gitleaks 迁移到 security.yml 不应报 N4, got: {n4_errors}"

    def test_detects_gitleaks_moved_to_yaml_extension_workflow(self, tmp_path, monkeypatch):
        """Gitleaks 迁移到 security.yaml (非 .yml) → 不应报错 (glob 双模式扫描, v3 §14.2.5)."""
        from check_docs_consistency import check_enforcement_mapping

        from check_docs_consistency import REDLINES_YAML_PATH

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", REDLINES_YAML_PATH)

        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  test:\n    steps:\n      - run: pytest\n", encoding="utf-8"
        )
        # security.yaml (注意 .yaml 扩展名)
        (workflows_dir / "security.yaml").write_text(
            "name: Gitleaks\njobs:\n  scan:\n    steps:\n      - uses: gitleaks/gitleaks-action@v2\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CI_WORKFLOW_DIR", workflows_dir)
        tmp_config = tmp_path / ".gitleaks.toml"
        tmp_config.write_text("[allowlist]\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.GITLEAKS_CONFIG_PATH", tmp_config)

        errors = check_enforcement_mapping()
        n4_errors = [e for e in errors if "N4" in e]
        assert n4_errors == [], f"Gitleaks 迁移到 .yaml 不应报 N4, got: {n4_errors}"

    def test_detects_pytest_removed_from_all_workflows(self, tmp_path, monkeypatch):
        """构造全部 workflow run: 命令块缺失 pytest → 应报错 (N5, R2/R7/R8 enforcement 含 'CI-test')."""
        from check_docs_consistency import check_enforcement_mapping

        from check_docs_consistency import REDLINES_YAML_PATH

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", REDLINES_YAML_PATH)

        # workflow 只含 ruff 命令, 无 pytest
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "ci.yml").write_text(
            "name: CI\njobs:\n  test:\n    steps:\n      - run: ruff check .\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CI_WORKFLOW_DIR", workflows_dir)

        errors = check_enforcement_mapping()
        assert any("N5" in e for e in errors), f"应检测到 pytest 缺失, got: {errors}"
