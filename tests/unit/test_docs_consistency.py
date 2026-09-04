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

pytestmark = [pytest.mark.unit, pytest.mark.meta]

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


_SINGLETON_LIFECYCLE_PATH = ROOT / "docs" / "architecture" / "singleton-lifecycle.md"


def _actual_registered_singletons() -> frozenset[str]:
    """导入全部 @register_singleton 模块（side-effect 注册）后返回 registry 类名集合。

    与 tests/unit/test_singletons_isolation.py 的动态枚举模式一致：noqa: F401
    的导入仅用于 side-effect 注册，不在测试中直接引用类名。
    """
    import data.cache.cache_manager  # noqa: F401 (side-effect 注册)
    import data.data_processor  # noqa: F401 (side-effect 注册)
    import data.domain_services.market_data_service  # noqa: F401 (side-effect 注册)
    import data.external.akshare_concept_client  # noqa: F401 (side-effect 注册)
    import data.external.tushare_client  # noqa: F401 (side-effect 注册)
    import data.persistence.embedded_postgres.service  # noqa: F401 (side-effect 注册)
    import data.persistence.metadata_manager  # noqa: F401 (side-effect 注册)
    import services.ai_service  # noqa: F401 (side-effect 注册)
    import services.embedded_pg_maintenance_service  # noqa: F401 (side-effect 注册)
    import services.local_model_manager  # noqa: F401 (side-effect 注册)
    import services.news_subscription_service  # noqa: F401 (side-effect 注册)
    import services.task_manager  # noqa: F401 (side-effect 注册)
    import strategies.all_strategies  # noqa: F401 (side-effect 注册)
    import utils.scheduler_service  # noqa: F401 (side-effect 注册)
    import utils.thread_pool  # noqa: F401 (side-effect 注册)
    import ui.theme  # noqa: F401 (side-effect 注册)

    from utils.singleton_registry import get_registered_singletons

    return frozenset(get_registered_singletons())


def _documented_registered_singletons() -> frozenset[str]:
    """从 singleton-lifecycle.md 注册单例表格提取文档声明的类名集合。"""
    content = _read(_SINGLETON_LIFECYCLE_PATH)
    section = content.split("**注册单例（", 1)[1].split("**非注册单例", 1)[0]
    return frozenset(re.findall(r"^\| `(\w+)`", section, flags=re.M))


class TestSingletonRegistryConsistency:
    """G24: singleton-lifecycle.md 注册单例清单与 singleton_registry 实际注册动态比对。

    防再漂移：新增/移除 @register_singleton 单例时，文档清单与总数声明由本测试强制同步。
    """

    def test_documented_registered_singletons_match_registry(self):
        """文档注册单例类名集合与 singleton_registry 实际注册集合一致。"""
        actual = _actual_registered_singletons()
        documented = _documented_registered_singletons()
        assert actual == documented, (
            "singleton-lifecycle.md 注册单例清单与 singleton_registry 不一致。"
            "新增/移除单例时必须同步更新 docs/architecture/singleton-lifecycle.md。"
            f"\n仅在 registry 未在文档: {sorted(actual - documented)}"
            f"\n仅在文档未在 registry: {sorted(documented - actual)}"
        )

    def test_declared_singleton_count_matches_registry(self):
        """文档 '注册单例（@register_singleton，N 个）' 总数声明与实际注册数一致。"""
        actual = len(_actual_registered_singletons())
        content = _read(_SINGLETON_LIFECYCLE_PATH)
        m = re.search(r"\*\*注册单例（`@register_singleton`，(\d+)\s*个）\*\*", content)
        assert m, "singleton-lifecycle.md missing '注册单例（@register_singleton，N 个）' count declaration"
        declared = int(m.group(1))
        assert declared == actual, (
            f"singleton-lifecycle.md declares {declared} registered singletons but singleton_registry has {actual}"
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
    - 每条红线含 6 字段 (id/title/description/enforcement/automation_coverage/human_review_required)
    - R 编号连续 append-only (R1~R18, 无缺号/重号/跳号)
    - CLAUDE.md §3.1 表格行数 = yml 条目数
    - automation_coverage 值合法 (full/partial/none) 且与 human_review_required 一致
    - CLAUDE.md §3.1 表格与 YAML 字段语义一致 (id/title/description/enforcement)
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
        """每条红线含全部必填字段 (由 REDLINE_REQUIRED_FIELDS 常量统一维护)."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, REDLINE_REQUIRED_FIELDS

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        for i, entry in enumerate(data["redlines"]):
            missing = REDLINE_REQUIRED_FIELDS - set(entry.keys())
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

    def test_detects_invalid_automation_coverage_value(self, tmp_path, monkeypatch):
        """automation_coverage 值非法 (非 full/partial/none) → 报错."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, check_redlines_yaml_consistency

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        data["redlines"][0]["automation_coverage"] = "invalid_value"
        tmp_yml = tmp_path / "redlines_bad_coverage.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)

        errors = check_redlines_yaml_consistency()
        assert any("automation_coverage" in e and "非法" in e for e in errors), (
            f"应报 automation_coverage 值非法, got: {errors}"
        )

    def test_detects_partial_coverage_but_human_review_false(self, tmp_path, monkeypatch):
        """automation_coverage=partial 但 human_review_required=false → 报错."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, check_redlines_yaml_consistency

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        # R5: automation_coverage=none, human_review_required=true → 改为矛盾
        for entry in data["redlines"]:
            if entry["id"] == "R5":
                entry["automation_coverage"] = "partial"
                entry["human_review_required"] = False
        tmp_yml = tmp_path / "redlines_mismatch.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)

        errors = check_redlines_yaml_consistency()
        assert any("R5" in e and "human_review_required=false" in e for e in errors), (
            f"应报 partial+false 矛盾, got: {errors}"
        )

    def test_detects_full_coverage_but_human_review_true(self, tmp_path, monkeypatch):
        """automation_coverage=full 但 human_review_required=true → 报错."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, check_redlines_yaml_consistency

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        # R3: automation_coverage=full, human_review_required=false → 改为矛盾
        for entry in data["redlines"]:
            if entry["id"] == "R3":
                entry["automation_coverage"] = "full"
                entry["human_review_required"] = True
        tmp_yml = tmp_path / "redlines_mismatch.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)

        errors = check_redlines_yaml_consistency()
        assert any("R3" in e and "human_review_required=true" in e for e in errors), (
            f"应报 full+true 矛盾, got: {errors}"
        )

    def test_detects_claude_yaml_semantic_mismatch(self, tmp_path, monkeypatch):
        """CLAUDE.md 与 YAML 字段语义不一致 → 报错."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, check_redlines_yaml_consistency

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        # 篡改 R1 title 使其与 CLAUDE.md 不一致
        data["redlines"][0]["title"] = "篡改的标题"
        tmp_yml = tmp_path / "redlines_semantic_mismatch.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)

        errors = check_redlines_yaml_consistency()
        assert any("R1" in e and "不一致" in e for e in errors), f"应报 CLAUDE.md 与 YAML 字段不一致, got: {errors}"

    def test_redline_rule_type_valid(self):
        """每条红线 rule_type 应为合法值 (P2-11)."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, RULE_TYPE_VALUES

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["redlines"]:
            rule_type = entry.get("rule_type")
            assert rule_type in RULE_TYPE_VALUES, (
                f"redlines[{entry.get('id')}] rule_type 非法: {rule_type} (应为 {sorted(RULE_TYPE_VALUES)})"
            )

    def test_detects_invalid_rule_type(self, tmp_path, monkeypatch):
        """rule_type 值非法 (非 6 种合法类型) → 报错."""
        import yaml

        from check_docs_consistency import REDLINES_YAML_PATH, check_redlines_yaml_consistency

        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        data["redlines"][0]["rule_type"] = "INVALID_TYPE"
        tmp_yml = tmp_path / "redlines_bad_rule_type.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)

        errors = check_redlines_yaml_consistency()
        assert any("rule_type" in e and "非法" in e for e in errors), f"应报 rule_type 值非法, got: {errors}"

    def test_detects_exceptionable_linkage_violation(self, tmp_path, monkeypatch):
        """例外注册表引用的 rule_id 不是 EXCEPTIONABLE 规则 → 报错."""
        import yaml

        from check_docs_consistency import (
            REDLINES_YAML_PATH,
            check_redlines_yaml_consistency,
        )

        # 将 R1 改为 INVARIANT (非 EXCEPTIONABLE), 且构造 exceptions.yml 引用 R1
        # （review01-A3: 真实 exceptions.yml 已随 EX-0001 移除而置空，故测试构造自包含 fixture）
        data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["redlines"]:
            if entry["id"] == "R1":
                entry["rule_type"] = "INVARIANT"
        tmp_yml = tmp_path / "redlines_non_exceptionable.yml"
        tmp_yml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

        tmp_exc = tmp_path / "exceptions.yml"
        tmp_exc.write_text(
            yaml.safe_dump(
                {
                    "exceptions": [
                        {
                            "id": "EX-TEST-LINKAGE",
                            "rule_id": "R1",
                            "paths": ["ui/startup_views.py"],
                            "reason": "test fixture",
                            "owner": "test",
                            "approved_by": "test",
                            "removal_trigger": "test",
                            "verification": "test",
                        }
                    ]
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr("check_docs_consistency.REDLINES_YAML_PATH", tmp_yml)
        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", tmp_exc)

        errors = check_redlines_yaml_consistency()
        assert any("不是 EXCEPTIONABLE 规则" in e for e in errors), f"应报例外引用非 EXCEPTIONABLE 规则, got: {errors}"

    # === _normalize_for_comparison 纯函数测试 ===

    def test_normalize_removes_bold_markers(self):
        """`**title**` → `title` (移除 markdown 粗体标记)."""
        from check_docs_consistency import _normalize_for_comparison

        assert _normalize_for_comparison("**title**") == "title"

    def test_normalize_removes_code_marks(self):
        """`` `code` `` → `code` (移除 markdown 行内代码标记)."""
        from check_docs_consistency import _normalize_for_comparison

        assert _normalize_for_comparison("`code`") == "code"

    def test_normalize_reverses_escaped_pipes(self):
        """`X \\| Y` → `X | Y` (反转义 markdown 表格转义管道符)."""
        from check_docs_consistency import _normalize_for_comparison

        assert _normalize_for_comparison("X \\| Y") == "X | Y"

    def test_normalize_removes_quotes(self):
        """`"value"` → `value`, `'value'` → `value` (移除两端引号)."""
        from check_docs_consistency import _normalize_for_comparison

        assert _normalize_for_comparison('"value"') == "value"
        assert _normalize_for_comparison("'value'") == "value"

    def test_normalize_strips_whitespace(self):
        """`  text  ` → `text` (strip 首尾空白)."""
        from check_docs_consistency import _normalize_for_comparison

        assert _normalize_for_comparison("  text  ") == "text"

    # === _parse_claude_redline_table 纯函数测试 ===

    def test_parse_typical_redline_row(self):
        """解析标准红线行, 验证 id/title/description/enforcement 正确提取."""
        from check_docs_consistency import _parse_claude_redline_table

        claude_content = "| R1 | **架构越界** | `core/` 导入任何其他层模块 | pre-commit（import-linter 4 条契约） |\n"
        result = _parse_claude_redline_table(claude_content)
        assert "R1" in result
        assert result["R1"]["title"] == "架构越界"
        assert result["R1"]["description"] == "core/ 导入任何其他层模块"
        assert result["R1"]["enforcement"] == "pre-commit（import-linter 4 条契约）"

    def test_parse_escaped_pipe_in_description(self):
        """解析 R6 描述中的 `X \\| Y`, 验证转义管道符正确处理 (不分割字段)."""
        from check_docs_consistency import _parse_claude_redline_table

        claude_content = (
            "| R6 | **过时类型注解** | 使用 `Union[X, Y]` / `Optional[X]` "
            "(必须使用 `X \\| Y` / `X \\| None`) | ruff |\n"
        )
        result = _parse_claude_redline_table(claude_content)
        assert "R6" in result
        assert result["R6"]["title"] == "过时类型注解"
        assert result["R6"]["description"] == "使用 Union[X, Y] / Optional[X] (必须使用 X | Y / X | None)"
        assert result["R6"]["enforcement"] == "ruff"


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
        redlines = [
            {
                "id": "R4",
                "enforcement": "pre-commit（check_redlines.py）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R4",
                "enforcement": "pre-commit（check_redlines.py）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R4",
                "enforcement": "pre-commit（check_redlines.py）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R4",
                "enforcement": "pre-commit（check_redlines.py）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R1",
                "enforcement": "pre-commit（import-linter 4 条契约）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R1",
                "enforcement": "pre-commit（import-linter 4 条契约）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R1",
                "enforcement": "pre-commit（import-linter）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R1",
                "enforcement": "pre-commit（import-linter 4 条契约）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R1",
                "enforcement": "pre-commit（import-linter 4 条契约）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [{"id": "R6", "enforcement": "ruff", "automation_coverage": "full", "human_review_required": False}]
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
        redlines = [{"id": "R6", "enforcement": "ruff", "automation_coverage": "full", "human_review_required": False}]
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
        redlines = [{"id": "R6", "enforcement": "ruff", "automation_coverage": "full", "human_review_required": False}]
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
        redlines = [
            {
                "id": "R9",
                "enforcement": "安全扫描 + 仅人工评审",
                "automation_coverage": "partial",
                "human_review_required": True,
            }
        ]
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
        redlines = [
            {
                "id": "R9",
                "enforcement": "安全扫描 + 仅人工评审",
                "automation_coverage": "partial",
                "human_review_required": True,
            }
        ]
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
        redlines = [
            {
                "id": "R9",
                "enforcement": "安全扫描 + 仅人工评审",
                "automation_coverage": "partial",
                "human_review_required": True,
            }
        ]
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
        redlines = [
            {
                "id": "R9",
                "enforcement": "安全扫描 + 仅人工评审",
                "automation_coverage": "partial",
                "human_review_required": True,
            }
        ]
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
        redlines = [
            {
                "id": "R2",
                "enforcement": "CI-test（全量，asyncio 相关测试）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R2",
                "enforcement": "CI-test（全量，asyncio 相关测试）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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
        redlines = [
            {
                "id": "R2",
                "enforcement": "CI-test（全量，asyncio 相关测试）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R2" in e and "N5" in e for e in errors), f"pip install pytest 不应满足 N5, got: {errors}"

    # === N6~N9: human_review_required 一致性测试 ===

    def test_n6_human_review_keyword_mismatch(self):
        """N6: automation_coverage != full 但 human_review_required=false → 报错."""
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
            {"id": "R5", "enforcement": "仅人工评审", "automation_coverage": "partial", "human_review_required": False}
        ]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R5" in e and "N6" in e for e in errors), f"应报 N6 错误, got: {errors}"

    def test_n7_pending_keyword_mismatch(self):
        """N7: enforcement 含 '待实现' 但 automation_coverage != none → 报错."""
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
                "enforcement": "可自动化待实现",
                "automation_coverage": "partial",
                "human_review_required": True,
            }
        ]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R16" in e and "N7" in e for e in errors), f"应报 N7 错误, got: {errors}"

    def test_n7_pending_keyword_with_human_review_false(self):
        """N7: enforcement 含 '待实现', automation_coverage=none 但 human_review_required=false → 报错.

        R16 特化守护: 待实现关键词要求 automation_coverage=none 且 human_review_required=true,
        human_review_required=false 违反 N7 (同时也违反 N6).
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
                "enforcement": "可自动化待实现",
                "automation_coverage": "none",
                "human_review_required": False,
            }
        ]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R16" in e and "N7" in e for e in errors), f"应报 N7 错误, got: {errors}"

    def test_n8_reverse_invariant_violation(self):
        """N8: human_review_required=true 但 automation_coverage=full → 报错."""
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
                "id": "R5",
                "enforcement": "pre-commit（some-hook）",
                "automation_coverage": "full",
                "human_review_required": True,
            }
        ]  # 矛盾
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
        redlines = [
            {"id": "R5", "enforcement": "仅人工评审", "automation_coverage": "partial", "human_review_required": False}
        ]  # 矛盾
        errors = _check_enforcement_invariants(redlines, env)
        # N6 应报错（automation_coverage='partial' != full 但 human_review_required=false）
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
        redlines = [
            {
                "id": "R9",
                "enforcement": "安全扫描 + 仅人工评审",
                "automation_coverage": "partial",
                "human_review_required": True,
            }
        ]
        errors = _check_enforcement_invariants(redlines, env)
        assert any("R9" in e and "N4" in e for e in errors), f"应报 N4, got: {errors}"
        # N6 应通过 (automation_coverage='partial' != full, human_review_required=true 一致)
        assert not any("R9" in e and "N6" in e for e in errors), f"不应报 N6, got: {errors}"

    def test_r16_dual_pending_keywords_passes_n7(self):
        """R16 enforcement='可自动化待实现（AST 检查，暂缓：误报风险高）'
        同时含 '待实现' 和 '暂缓', automation_coverage=none, human_review_required=true → N7 通过 (不报错).
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
                "automation_coverage": "none",
                "human_review_required": True,
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
        redlines = [
            {
                "id": "R2",
                "enforcement": "CI-test（全量，asyncio 相关测试）",
                "automation_coverage": "full",
                "human_review_required": False,
            }
        ]
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


class TestFletHubCompleteness:
    """Flet 入口完整性检查（spec §11.1 + §11.2 + §11.3 + §11.4）。

    验证 docs/flet/README.md 作为唯一导航入口的完整性：
    - 动态发现 docs/flet/*.md 全部纳入 CHECKED_DOCS
    - README 覆盖全部专题文档（无遗漏）
    - README 不引用不存在的专题（无幽灵链接）
    - 带锚点的缺失 Markdown 文件会被报告（§11.3 修复验证）
    - man/flet-best-practices.md 只指向 README（不含子文档深链接）
    - Flet 四包版本一致（flet / flet-desktop / flet-charts / flet-mcp）
    """

    def test_flet_readme_in_checked_docs(self):
        """docs/flet/README.md 应在 CHECKED_DOCS 中（通过 FLET_DOCS_PATHS 动态发现）。"""
        from check_docs_consistency import CHECKED_DOCS, FLET_HUB_PATH

        assert FLET_HUB_PATH in CHECKED_DOCS, (
            f"FLET_HUB_PATH {FLET_HUB_PATH} not in CHECKED_DOCS, "
            f"FLET_DOCS_PATHS should dynamically discover docs/flet/README.md"
        )

    def test_mcp_usage_in_checked_docs(self):
        """docs/flet/mcp-usage.md 应在 CHECKED_DOCS 中（通过 FLET_DOCS_PATHS 动态发现）。"""
        from check_docs_consistency import CHECKED_DOCS

        mcp_usage_path = ROOT / "docs" / "flet" / "mcp-usage.md"
        assert mcp_usage_path in CHECKED_DOCS, (
            "docs/flet/mcp-usage.md not in CHECKED_DOCS, FLET_DOCS_PATHS should dynamically discover all docs/flet/*.md"
        )

    def test_ui_ux_best_practices_in_checked_docs(self):
        """docs/flet/ui-ux-best-practices.md 应在 CHECKED_DOCS 中（新增专题自动纳入门禁）。"""
        from check_docs_consistency import CHECKED_DOCS

        ui_ux_path = ROOT / "docs" / "flet" / "ui-ux-best-practices.md"
        assert ui_ux_path in CHECKED_DOCS, (
            "docs/flet/ui-ux-best-practices.md not in CHECKED_DOCS, "
            "FLET_DOCS_PATHS should dynamically discover all docs/flet/*.md"
        )

    def test_canvaskit_e2e_guide_in_checked_docs(self):
        """docs/flet/canvaskit-rendering-e2e-guide.md 应在 CHECKED_DOCS 中。"""
        from check_docs_consistency import CHECKED_DOCS

        canvaskit_path = ROOT / "docs" / "flet" / "canvaskit-rendering-e2e-guide.md"
        assert canvaskit_path in CHECKED_DOCS, (
            "docs/flet/canvaskit-rendering-e2e-guide.md not in CHECKED_DOCS, "
            "FLET_DOCS_PATHS should dynamically discover all docs/flet/*.md"
        )

    def test_flet_docs_paths_matches_glob(self):
        """FLET_DOCS_PATHS 应与 sorted(docs/flet/*.md) 实际集合一致（动态发现验证）。"""
        from check_docs_consistency import FLET_DOCS_DIR, FLET_DOCS_PATHS

        expected = sorted(FLET_DOCS_DIR.glob("*.md"))
        assert expected == FLET_DOCS_PATHS, (
            f"FLET_DOCS_PATHS does not match sorted(FLET_DOCS_DIR.glob('*.md')):\n"
            f"  FLET_DOCS_PATHS: {FLET_DOCS_PATHS}\n"
            f"  expected: {expected}"
        )

    def test_readme_covers_all_flet_docs(self):
        """README 应覆盖全部 docs/flet/*.md 专题文档（除 README.md 自身）。"""
        from check_docs_consistency import check_flet_hub_completeness

        errors = check_flet_hub_completeness()
        missing_errors = [e for e in errors if "未链接专题文档" in e]
        assert missing_errors == [], "docs/flet/README.md 未覆盖全部专题文档:\n  " + "\n  ".join(missing_errors)

    def test_readme_no_phantom_links(self):
        """README 不应引用不存在的专题文档（防止幽灵链接）。"""
        from check_docs_consistency import check_flet_hub_completeness

        errors = check_flet_hub_completeness()
        phantom_errors = [e for e in errors if "引用了不存在的专题文档" in e]
        assert phantom_errors == [], "docs/flet/README.md 引用了不存在的专题文档:\n  " + "\n  ".join(phantom_errors)

    def test_check_flet_hub_completeness_passes(self):
        """check_flet_hub_completeness() 在当前项目状态下应返回空列表（全部通过）。"""
        from check_docs_consistency import check_flet_hub_completeness

        errors = check_flet_hub_completeness()
        assert errors == [], "check_flet_hub_completeness() should pass:\n  " + "\n  ".join(errors)

    def test_flet_hub_completeness_detects_missing_registration(self, tmp_path, monkeypatch):
        """新增 docs/flet/xxx.md 未登记到 README 时应报错（fail closed）。"""
        from check_docs_consistency import check_flet_hub_completeness

        # 构造临时 docs/flet/ 目录
        flet_dir = tmp_path / "docs" / "flet"
        flet_dir.mkdir(parents=True)
        # README 只链接 a.md
        (flet_dir / "README.md").write_text("# Flet Hub\n\n[a](./a.md)\n", encoding="utf-8")
        (flet_dir / "a.md").write_text("# A\n", encoding="utf-8")
        # b.md 未在 README 中登记
        (flet_dir / "b.md").write_text("# B\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.FLET_HUB_PATH", flet_dir / "README.md")
        monkeypatch.setattr("check_docs_consistency.FLET_DOCS_DIR", flet_dir)
        monkeypatch.setattr(
            "check_docs_consistency.FLET_DOCS_PATHS",
            sorted(flet_dir.glob("*.md")),
        )

        errors = check_flet_hub_completeness()
        assert any("b.md" in e and "未链接" in e for e in errors), f"应检测到 b.md 未登记到 README, got: {errors}"

    def test_flet_hub_completeness_detects_phantom_link(self, tmp_path, monkeypatch):
        """README 引用不存在的专题文件时应报错（防止幽灵链接）。"""
        from check_docs_consistency import check_flet_hub_completeness

        # 构造临时 docs/flet/ 目录
        flet_dir = tmp_path / "docs" / "flet"
        flet_dir.mkdir(parents=True)
        # README 引用了不存在的 phantom.md
        (flet_dir / "README.md").write_text("# Flet Hub\n\n[phantom](./phantom.md)\n[a](./a.md)\n", encoding="utf-8")
        (flet_dir / "a.md").write_text("# A\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.FLET_HUB_PATH", flet_dir / "README.md")
        monkeypatch.setattr("check_docs_consistency.FLET_DOCS_DIR", flet_dir)
        monkeypatch.setattr(
            "check_docs_consistency.FLET_DOCS_PATHS",
            sorted(flet_dir.glob("*.md")),
        )

        errors = check_flet_hub_completeness()
        assert any("phantom.md" in e and "引用了不存在" in e for e in errors), (
            f"应检测到 phantom.md 幽灵链接, got: {errors}"
        )

    def test_anchor_dead_link_to_missing_file_detected(self, tmp_path, monkeypatch):
        """带锚点的链接指向不存在的 Markdown 文件时应被报告（spec §11.3 修复验证）。"""
        from check_docs_consistency import check_anchor_dead_links

        # 构造临时文档，含指向不存在文件的带锚点链接
        tmp_doc = tmp_path / "test_doc.md"
        tmp_doc.write_text("# Test\n\n[link](./nonexistent.md#section)\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CHECKED_DOCS", [tmp_doc])

        errors = check_anchor_dead_links()
        assert len(errors) == 1, f"Should detect 1 dead link to missing file, got {errors}"
        assert "nonexistent.md" in errors[0]
        assert "不存在" in errors[0]

    def test_anchor_dead_link_to_existing_non_checked_file_skips_anchor_check(self, tmp_path, monkeypatch):
        """带锚点的链接指向存在但不在 CHECKED_DOCS 中的文件时，跳过锚点校验（不误报）。"""
        from check_docs_consistency import check_anchor_dead_links

        # 构造临时文档，含指向存在但不在 CHECKED_DOCS 中的文件的带锚点链接
        tmp_doc = tmp_path / "test_doc.md"
        # 创建目标文件（存在但不在 CHECKED_DOCS 中）
        target_file = tmp_path / "target.md"
        target_file.write_text("# Target\n", encoding="utf-8")
        tmp_doc.write_text("# Test\n\n[link](./target.md#nonexistent-anchor)\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CHECKED_DOCS", [tmp_doc])

        errors = check_anchor_dead_links()
        # 目标文件存在但不在 CHECKED_DOCS 中 → 跳过锚点校验，不报错
        assert errors == [], f"Should not flag anchor for existing non-CHECKED_DOCS file, got: {errors}"

    def test_man_flet_best_practices_only_links_readme(self):
        """man/flet-best-practices.md 应只指向 README，不含子文档深链接。

        spec §9 要求 man/flet-best-practices.md 缩减为最小 stub，只链接 docs/flet/README.md。
        """
        from check_docs_consistency import FLET_BEST_PRACTICES_PATH

        content = FLET_BEST_PRACTICES_PATH.read_text(encoding="utf-8")
        # 应包含指向 docs/flet/README.md 的链接
        assert "../docs/flet/README.md" in content or "docs/flet/README.md" in content, (
            "man/flet-best-practices.md should link to docs/flet/README.md"
        )
        # 不应包含 docs/flet/ 子文档深链接（如 v1-api-constraints.md / project-differences.md 等）
        # 排除 README.md 自身（允许链接 README.md）
        sub_doc_pattern = re.compile(r"\(\.\./docs/flet/(?!README\.md)[^)]+\.md[^)]*\)")
        deep_links = sub_doc_pattern.findall(content)
        assert deep_links == [], (
            f"man/flet-best-practices.md should not contain deep links to docs/flet/ sub-documents "
            f"(only README.md allowed), found: {deep_links}"
        )

    def test_flet_four_packages_version_consistent(self):
        """Flet 四包（flet / flet-desktop / flet-charts / flet-mcp）版本应一致。"""
        from check_docs_consistency import _get_flet_locked_versions

        versions = _get_flet_locked_versions()
        assert len(versions) == 1, (
            f"Flet 四包版本不一致 (应全部锁定同一版本): {versions}. "
            f"检查 pyproject.toml [project.dependencies] 和 [project.optional-dependencies].dev"
        )

    def test_flet_packages_includes_flet_mcp(self):
        """_FLET_PACKAGES 应包含 flet-mcp（spec §11.4 四包版本一致性）。"""
        from check_docs_consistency import _FLET_PACKAGES

        assert "flet-mcp" in _FLET_PACKAGES, (
            f"_FLET_PACKAGES should include 'flet-mcp' for 4-package version consistency, got: {_FLET_PACKAGES}"
        )

    def test_flet_version_drift_check_passes(self):
        """check_flet_version_drift() 在当前项目状态下应返回空列表（无版本漂移）。"""
        from check_docs_consistency import check_flet_version_drift

        errors = check_flet_version_drift()
        assert errors == [], "check_flet_version_drift() should pass:\n  " + "\n  ".join(errors)

    def test_main_returns_zero_with_flet_hub_check(self):
        """脚本 main() 包含 Flet 入口完整性检查后仍应返回 0（全部通过）。"""
        from check_docs_consistency import main

        assert main() == 0, (
            "check_docs_consistency.py main() should return 0 when all checks pass (including Flet hub completeness)"
        )


class TestExceptionsYamlConsistency:
    """例外注册表一致性校验 (P1-01: 集中例外治理).

    校验 docs/governance/exceptions.yml 的 schema、rule_id 存在性、路径存在性。
    """

    def test_exceptions_yaml_file_exists(self):
        """exceptions.yml 应存在于 docs/governance/ 下."""
        from check_docs_consistency import EXCEPTIONS_YAML_PATH

        assert EXCEPTIONS_YAML_PATH.exists(), f"exceptions.yml should exist at {EXCEPTIONS_YAML_PATH}"

    def test_exceptions_yaml_parses_successfully(self):
        """exceptions.yml 应可被 yaml.safe_load 解析."""
        import yaml

        from check_docs_consistency import EXCEPTIONS_YAML_PATH

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "exceptions" in data
        assert isinstance(data["exceptions"], list)

    def test_exception_fields_complete(self):
        """每条例外必填字段齐全, 且 expires_at/removal_trigger 二选一."""
        import yaml

        from check_docs_consistency import (
            EXCEPTIONS_YAML_PATH,
            EXCEPTION_EXPIRY_FIELDS,
            EXCEPTION_REQUIRED_FIELDS,
        )

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["exceptions"]:
            missing = EXCEPTION_REQUIRED_FIELDS - entry.keys()
            assert not missing, f"例外 {entry.get('id')} 缺少必填字段: {sorted(missing)}"
            assert EXCEPTION_EXPIRY_FIELDS & entry.keys(), f"例外 {entry.get('id')} 缺少 expires_at 或 removal_trigger"

    def test_exception_ids_unique(self):
        """例外 id 应唯一且格式为 EX-XXXX."""
        import yaml

        from check_docs_consistency import EXCEPTIONS_YAML_PATH

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
        ids = [e["id"] for e in data["exceptions"]]
        assert len(ids) == len(set(ids)), f"例外 id 重复: {ids}"
        for exc_id in ids:
            assert exc_id.startswith("EX-"), f"例外 id 格式应为 EX-XXXX: {exc_id}"

    def test_exception_rule_id_exists_in_redlines(self):
        """例外 rule_id 必须存在于 redlines.yml."""
        import yaml

        from check_docs_consistency import EXCEPTIONS_YAML_PATH, REDLINES_YAML_PATH

        exc_data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
        red_data = yaml.safe_load(REDLINES_YAML_PATH.read_text(encoding="utf-8"))
        registered = {e["id"] for e in red_data["redlines"]}
        for entry in exc_data["exceptions"]:
            assert entry["rule_id"] in registered, (
                f"例外 {entry['id']} 的 rule_id '{entry['rule_id']}' 不存在于 redlines.yml"
            )

    def test_exception_paths_exist(self):
        """例外 paths 指向的仓库路径必须真实存在."""
        import yaml

        from check_docs_consistency import EXCEPTIONS_YAML_PATH, ROOT

        data = yaml.safe_load(EXCEPTIONS_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["exceptions"]:
            for p in entry["paths"]:
                assert (ROOT / p).exists(), f"例外 {entry['id']} 的路径不存在: {p}"

    def test_check_exceptions_yaml_consistency_passes(self):
        """当前项目配置下 check_exceptions_yaml_consistency() 应返回空错误列表."""
        from check_docs_consistency import check_exceptions_yaml_consistency

        errors = check_exceptions_yaml_consistency()
        assert errors == [], "当前项目配置应通过 exceptions.yml 校验, 失败:\n  " + "\n  ".join(errors)

    def test_detects_missing_required_field(self, tmp_path, monkeypatch):
        """缺少必填字段时应报错."""
        from check_docs_consistency import check_exceptions_yaml_consistency

        tmp_yml = tmp_path / "exceptions.yml"
        tmp_yml.write_text(
            "exceptions:\n  - id: EX-0001\n    rule_id: R1\n    paths: [ui/startup_views.py]\n    reason: test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", tmp_yml)
        errors = check_exceptions_yaml_consistency()
        assert any("缺少必填字段" in e for e in errors), f"应报缺少必填字段, got: {errors}"

    def test_detects_missing_expiry_field(self, tmp_path, monkeypatch):
        """缺少 expires_at/removal_trigger 二选一字段时应报错."""
        from check_docs_consistency import check_exceptions_yaml_consistency

        tmp_yml = tmp_path / "exceptions.yml"
        tmp_yml.write_text(
            "exceptions:\n"
            "  - id: EX-0001\n"
            "    rule_id: R1\n"
            "    paths: [ui/startup_views.py]\n"
            "    reason: test\n"
            "    owner: test\n"
            "    approved_by: test\n"
            "    verification: test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", tmp_yml)
        errors = check_exceptions_yaml_consistency()
        assert any("expires_at 或 removal_trigger" in e for e in errors), f"应报缺少二选一字段, got: {errors}"

    def test_detects_duplicate_id(self, tmp_path, monkeypatch):
        """例外 id 重复时应报错."""
        from check_docs_consistency import check_exceptions_yaml_consistency

        tmp_yml = tmp_path / "exceptions.yml"
        tmp_yml.write_text(
            "exceptions:\n"
            "  - id: EX-0001\n"
            "    rule_id: R1\n"
            "    paths: [ui/startup_views.py]\n"
            "    reason: test\n"
            "    owner: test\n"
            "    approved_by: test\n"
            "    verification: test\n"
            "    removal_trigger: test\n"
            "  - id: EX-0001\n"
            "    rule_id: R1\n"
            "    paths: [ui/startup_views.py]\n"
            "    reason: test\n"
            "    owner: test\n"
            "    approved_by: test\n"
            "    verification: test\n"
            "    removal_trigger: test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", tmp_yml)
        errors = check_exceptions_yaml_consistency()
        assert any("id 重复" in e for e in errors), f"应报 id 重复, got: {errors}"

    def test_detects_invalid_rule_id(self, tmp_path, monkeypatch):
        """rule_id 不存在于 redlines.yml 时应报错."""
        from check_docs_consistency import check_exceptions_yaml_consistency

        tmp_yml = tmp_path / "exceptions.yml"
        tmp_yml.write_text(
            "exceptions:\n"
            "  - id: EX-0001\n"
            "    rule_id: R999\n"
            "    paths: [ui/startup_views.py]\n"
            "    reason: test\n"
            "    owner: test\n"
            "    approved_by: test\n"
            "    verification: test\n"
            "    removal_trigger: test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", tmp_yml)
        errors = check_exceptions_yaml_consistency()
        assert any("rule_id" in e and "不存在于 redlines.yml" in e for e in errors), (
            f"应报 rule_id 不存在, got: {errors}"
        )

    def test_detects_missing_path(self, tmp_path, monkeypatch):
        """paths 指向不存在的文件时应报错."""
        from check_docs_consistency import check_exceptions_yaml_consistency

        tmp_yml = tmp_path / "exceptions.yml"
        tmp_yml.write_text(
            "exceptions:\n"
            "  - id: EX-0001\n"
            "    rule_id: R1\n"
            "    paths: [nonexistent/file.py]\n"
            "    reason: test\n"
            "    owner: test\n"
            "    approved_by: test\n"
            "    verification: test\n"
            "    removal_trigger: test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", tmp_yml)
        errors = check_exceptions_yaml_consistency()
        assert any("路径不存在" in e for e in errors), f"应报路径不存在, got: {errors}"


class TestCanonicalTopicsYamlConsistency:
    """主题 → canonical 正本映射一致性校验 (P2-12).

    校验 docs/governance/canonical-topics.yml 的 schema、id 唯一性、路径存在性。
    """

    def test_canonical_topics_yaml_file_exists(self):
        """canonical-topics.yml 应存在于 docs/governance/ 下."""
        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH

        assert CANONICAL_TOPICS_YAML_PATH.exists(), f"canonical-topics.yml should exist at {CANONICAL_TOPICS_YAML_PATH}"

    def test_canonical_topics_yaml_parses_successfully(self):
        """canonical-topics.yml 应可被 yaml.safe_load 解析."""
        import yaml

        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "topics" in data
        assert isinstance(data["topics"], list)

    def test_topic_fields_complete(self):
        """每个主题必填 id/title/canonical."""
        import yaml

        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["topics"]:
            missing = {"id", "title", "canonical"} - entry.keys()
            assert not missing, f"主题 {entry.get('id')} 缺少必填字段: {sorted(missing)}"

    def test_topic_ids_unique(self):
        """主题 id 应唯一."""
        import yaml

        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
        ids = [t["id"] for t in data["topics"]]
        assert len(ids) == len(set(ids)), f"主题 id 重复: {ids}"

    def test_topic_canonical_paths_exist(self):
        """canonical 指向的文档路径必须真实存在."""
        import yaml

        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH, ROOT

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["topics"]:
            assert (ROOT / entry["canonical"]).exists(), (
                f"主题 {entry['id']} 的 canonical 路径不存在: {entry['canonical']}"
            )

    def test_topic_workflow_paths_exist(self):
        """workflow（若存在）指向的文档路径必须真实存在."""
        import yaml

        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH, ROOT

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
        for entry in data["topics"]:
            workflow = entry.get("workflow")
            if workflow is not None:
                assert (ROOT / workflow).exists(), f"主题 {entry['id']} 的 workflow 路径不存在: {workflow}"

    def test_check_canonical_topics_consistency_passes(self):
        """当前项目配置下 check_canonical_topics_consistency() 应返回空错误列表."""
        from check_docs_consistency import check_canonical_topics_consistency

        errors = check_canonical_topics_consistency()
        assert errors == [], "当前项目配置应通过 canonical-topics.yml 校验, 失败:\n  " + "\n  ".join(errors)

    def test_detects_missing_required_field(self, tmp_path, monkeypatch):
        """缺少必填字段时应报错."""
        from check_docs_consistency import check_canonical_topics_consistency

        tmp_yml = tmp_path / "canonical-topics.yml"
        tmp_yml.write_text(
            "topics:\n  - id: strategy\n    title: 新增/修改策略\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", tmp_yml)
        errors = check_canonical_topics_consistency()
        assert any("缺少必填字段" in e for e in errors), f"应报缺少必填字段, got: {errors}"

    def test_detects_duplicate_id(self, tmp_path, monkeypatch):
        """主题 id 重复时应报错."""
        from check_docs_consistency import check_canonical_topics_consistency

        tmp_yml = tmp_path / "canonical-topics.yml"
        tmp_yml.write_text(
            "topics:\n"
            "  - id: strategy\n    title: A\n    canonical: docs/patterns/strategy-template.md\n"
            "  - id: strategy\n    title: B\n    canonical: docs/patterns/dao-pattern.md\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", tmp_yml)
        errors = check_canonical_topics_consistency()
        assert any("id 重复" in e for e in errors), f"应报 id 重复, got: {errors}"

    def test_detects_missing_canonical_path(self, tmp_path, monkeypatch):
        """canonical 指向不存在的文件时应报错."""
        from check_docs_consistency import check_canonical_topics_consistency

        tmp_yml = tmp_path / "canonical-topics.yml"
        tmp_yml.write_text(
            "topics:\n  - id: strategy\n    title: A\n    canonical: docs/nonexistent.md\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", tmp_yml)
        errors = check_canonical_topics_consistency()
        assert any("canonical 路径不存在" in e for e in errors), f"应报 canonical 路径不存在, got: {errors}"

    def test_detects_missing_workflow_path(self, tmp_path, monkeypatch):
        """workflow 指向不存在的文件时应报错."""
        from check_docs_consistency import check_canonical_topics_consistency

        tmp_yml = tmp_path / "canonical-topics.yml"
        tmp_yml.write_text(
            "topics:\n"
            "  - id: strategy\n    title: A\n    canonical: docs/patterns/strategy-template.md\n"
            "    workflow: docs/nonexistent.md\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", tmp_yml)
        errors = check_canonical_topics_consistency()
        assert any("workflow 路径不存在" in e for e in errors), f"应报 workflow 路径不存在, got: {errors}"

    def test_canonical_topics_align_with_claude_decision_tree(self):
        """§1.8 决策树表引用的路径应存在于 canonical-topics.yml 的 canonical 集合中 (P2-12 镜像一致性).

        §1.8 是 canonical-topics.yml 的人类可读摘要，有意合并相关主题（如 UI 视图/布局/ViewModel/i18n
        合并为一行）。因此不校验标题一一对应，而是校验 §1.8 表中引用的每个 .md 路径
        都在 canonical-topics.yml 中注册，防止新增 §1.8 行时遗漏 canonical-topics.yml 同步。
        """
        import re
        import yaml

        from check_docs_consistency import CANONICAL_TOPICS_YAML_PATH, CLAUDE_PATH

        claude_text = CLAUDE_PATH.read_text(encoding="utf-8")
        # 提取 §1.8 决策树表格内容（从「### 1.8」到下一个「###」或「##」）
        m = re.search(r"### 1\.8.*?\n(\|.*?\|.*?\n(?:\|[-:| ]+\n)?(?:\|.*?\|.*?\n)+)", claude_text, re.DOTALL)
        assert m, "CLAUDE.md §1.8 决策树表格未找到"
        table_text = m.group(1)

        # 提取表中引用的 .md 路径（markdown 链接目标或裸路径），统一去掉 ./ 前缀
        all_paths_raw = re.findall(r"[\w/\-.]+\.md", table_text)
        all_paths = {p.lstrip("./") for p in all_paths_raw}

        data = yaml.safe_load(CANONICAL_TOPICS_YAML_PATH.read_text(encoding="utf-8"))
        canonical_set = {entry["canonical"] for entry in data["topics"]}

        for path in all_paths:
            assert path in canonical_set, (
                f"CLAUDE.md §1.8 引用的路径 '{path}' 未在 canonical-topics.yml 中注册，"
                f"可能存在单边漂移（新增 §1.8 行时未同步 canonical-topics.yml）"
            )


class TestAgentsMdSync:
    """AGENTS.md 最小安全集生成区块与 redlines.yml 一致性契约测试 (DOC-08 / DOC-13, 见 ADR-0006)."""

    def test_agents_md_exists_and_has_generated_block(self):
        """AGENTS.md 存在且含生成区块包裹标记."""
        from check_docs_consistency import AGENTS_PATH

        assert AGENTS_PATH.exists(), "AGENTS.md should exist"
        content = AGENTS_PATH.read_text(encoding="utf-8")
        assert "<!-- generated:redlines-invariant -->" in content, "缺少生成区块起始标记"
        assert "<!-- /generated -->" in content, "缺少生成区块结束标记"

    def test_render_invariant_lines_expected_ids(self):
        """渲染结果应为 INVARIANT(R2/R3/R4/R5/R7/R9/R10) + R18, 共 8 行."""
        from check_docs_consistency import _render_agents_invariant_lines

        lines = _render_agents_invariant_lines()
        ids = [line.removeprefix("- R").split("：")[0] for line in lines]
        assert ids == ["2", "3", "4", "5", "7", "9", "10", "18"], f"红线集合漂移, got: {ids}"

    def test_check_agents_md_sync_passes(self):
        """真实 AGENTS.md 生成区块应与 redlines.yml 渲染一致 (无错误)."""
        from check_docs_consistency import check_agents_md_sync

        assert check_agents_md_sync() == []

    def test_detects_block_drift(self, tmp_path, monkeypatch):
        """篡改 AGENTS.md 生成区块 → check_agents_md_sync() 报错."""
        from check_docs_consistency import (
            AGENTS_PATH,
            check_agents_md_sync,
            _render_agents_invariant_lines,
        )

        content = AGENTS_PATH.read_text(encoding="utf-8")
        start_tag = "<!-- generated:redlines-invariant -->"
        end_tag = "<!-- /generated -->"
        start = content.find(start_tag)
        end = content.find(end_tag)
        expected = _render_agents_invariant_lines()
        tampered = "\n".join([expected[0]] + ["- R99：臆造红线"] + expected[1:])
        new_content = content[: start + len(start_tag)] + "\n" + tampered + "\n" + content[end:]
        tmp_agents = tmp_path / "AGENTS.md"
        tmp_agents.write_text(new_content, encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.AGENTS_PATH", tmp_agents)

        errors = check_agents_md_sync()
        assert len(errors) > 0, "应检出生成区块漂移, got no errors"
        assert any("不一致" in e for e in errors), f"错误信息应含『不一致』, got: {errors}"

    def test_detects_missing_markers(self, tmp_path, monkeypatch):
        """缺少标记块 → check_agents_md_sync() 报错并提示标记缺失."""
        from check_docs_consistency import check_agents_md_sync

        tmp_agents = tmp_path / "AGENTS.md"
        tmp_agents.write_text("# no markers here", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.AGENTS_PATH", tmp_agents)

        errors = check_agents_md_sync()
        assert len(errors) > 0, "应检出缺少标记, got no errors"
        assert any("缺少生成区块标记" in e for e in errors), f"错误信息应提示标记缺失, got: {errors}"


class TestRulesetMetadataConsistency:
    """规则集元数据一致性（DOC-01）：CLAUDE.md 与 CONTRIBUTING.md 的 ruleset_version / last_reviewed 同步."""

    def test_metadata_pass_on_current_repo(self):
        """真实 CLAUDE.md / CONTRIBUTING.md 应满足元数据同步（无错误）."""
        from check_docs_consistency import check_ruleset_metadata_consistency

        assert check_ruleset_metadata_consistency() == []

    def test_detects_ruleset_version_drift(self, tmp_path, monkeypatch):
        """ruleset_version 不一致 → 报错（fail closed）."""
        from check_docs_consistency import check_ruleset_metadata_consistency

        claude = tmp_path / "CLAUDE.md"
        contributing = tmp_path / "CONTRIBUTING.md"
        claude.write_text("> - ruleset_version: 1.3.0\n> - last_reviewed: 2026-09-03\n", encoding="utf-8")
        contributing.write_text("> - ruleset_version: 1.2.0\n> - last_reviewed: 2026-09-03\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)

        errors = check_ruleset_metadata_consistency()
        assert any("ruleset_version 漂移" in e for e in errors), f"应检出 ruleset_version 漂移, got: {errors}"

    def test_detects_contributing_last_reviewed_earlier(self, tmp_path, monkeypatch):
        """CONTRIBUTING 的 last_reviewed 早于 CLAUDE → 报错."""
        from check_docs_consistency import check_ruleset_metadata_consistency

        claude = tmp_path / "CLAUDE.md"
        contributing = tmp_path / "CONTRIBUTING.md"
        claude.write_text("> - ruleset_version: 1.3.0\n> - last_reviewed: 2026-09-03\n", encoding="utf-8")
        contributing.write_text("> - ruleset_version: 1.3.0\n> - last_reviewed: 2026-08-01\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)

        errors = check_ruleset_metadata_consistency()
        assert any("last_reviewed" in e and "早于" in e for e in errors), f"应检出 last_reviewed 倒挂, got: {errors}"

    def test_detects_missing_metadata(self, tmp_path, monkeypatch):
        """任一份文件缺元数据字段 → 报错."""
        from check_docs_consistency import check_ruleset_metadata_consistency

        claude = tmp_path / "CLAUDE.md"
        contributing = tmp_path / "CONTRIBUTING.md"
        claude.write_text("# no ruleset metadata\n", encoding="utf-8")
        contributing.write_text("> - ruleset_version: 1.3.0\n> - last_reviewed: 2026-09-03\n", encoding="utf-8")
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)

        errors = check_ruleset_metadata_consistency()
        assert any("CLAUDE.md 缺少 ruleset_version" in e for e in errors), f"应检出缺字段, got: {errors}"


class TestDecisionTreeMapping:
    """决策树与其机器可读镜像双向一致（DOC-04）：CLAUDE.md §1.8 ↔ canonical-topics.yml."""

    def test_mapping_pass_on_current_repo(self):
        """真实 §1.8 决策树与 canonical-topics.yml 应双向一致（无错误）."""
        from check_docs_consistency import check_decision_tree_mapping

        assert check_decision_tree_mapping() == []

    def test_detects_claude_target_not_registered(self, tmp_path, monkeypatch):
        """§1.8 引用的路径未在 yml 登记 → 报错."""
        import yaml

        from check_docs_consistency import check_decision_tree_mapping

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            "## 1.8 任务类型 → 必读文件\n"
            "| 任务类型 | 必读入口 |\n"
            "| --- | --- |\n"
            "| X | [foo](./docs/patterns/foo.md) |\n",
            encoding="utf-8",
        )
        yml = tmp_path / "canonical-topics.yml"
        yml.write_text(
            yaml.safe_dump({"topics": [{"id": "bar", "title": "Bar", "canonical": "docs/patterns/bar.md"}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", yml)

        errors = check_decision_tree_mapping()
        assert any("docs/patterns/foo.md" in e and "未在 canonical-topics.yml" in e for e in errors), (
            f"应检出 §1.8 目标未登记, got: {errors}"
        )

    def test_detects_yml_canonical_missing_in_claude(self, tmp_path, monkeypatch):
        """yml 登记的 canonical 未在 §1.8 出现 → 报错."""
        import yaml

        from check_docs_consistency import check_decision_tree_mapping

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            "## 1.8 任务类型 → 必读文件\n"
            "| 任务类型 | 必读入口 |\n"
            "| --- | --- |\n"
            "| X | [foo](./docs/patterns/foo.md) |\n",
            encoding="utf-8",
        )
        yml = tmp_path / "canonical-topics.yml"
        # yml 追加一个宪法未出现的 canonical，制造反向漂移
        yml.write_text(
            yaml.safe_dump(
                {
                    "topics": [
                        {"id": "foo", "title": "Foo", "canonical": "docs/patterns/foo.md"},
                        {"id": "orphan", "title": "Orphan", "canonical": "CONTRIBUTING.md"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", yml)

        errors = check_decision_tree_mapping()
        assert any("CONTRIBUTING.md" in e and "未在 CLAUDE.md" in e for e in errors), f"应检出反向漂移, got: {errors}"

    def test_detects_shared_canonical_topic_misassigned(self, tmp_path, monkeypatch):
        """共享 canonical 的 yml 主题归属与白名单不一致（主题正本被指到别的主体）→ 报错."""
        import yaml

        from check_docs_consistency import check_decision_tree_mapping

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            "## 1.8 任务类型 → 必读文件\n"
            "| 任务类型 | 必读入口 |\n"
            "| --- | --- |\n"
            "| 视图 | [docs/flet/README.md](./docs/flet/README.md) |\n"
            "| 主题 A | [docs/patterns/mvvm.md](./docs/patterns/mvvm.md) |\n",
            encoding="utf-8",
        )
        yml = tmp_path / "canonical-topics.yml"
        # a/b 分别指向不同正本，集合级断言会因两者都存在于两侧而通过，但归属已错配
        yml.write_text(
            yaml.safe_dump(
                {
                    "topics": [
                        {"id": "a", "title": "A", "canonical": "docs/patterns/mvvm.md"},
                        {"id": "b", "title": "B", "canonical": "docs/patterns/mvvm.md"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "check_docs_consistency._DECISION_TREE_MERGED_IDS",
            {"docs/patterns/mvvm.md": {"a", "c"}},
        )
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", yml)

        errors = check_decision_tree_mapping()
        assert any("docs/patterns/mvvm.md" in e and "归属 ['a', 'b']" in e for e in errors), (
            f"应检出共享 canonical 归属错配, got: {errors}"
        )

    def test_detects_merged_topic_removed(self, tmp_path, monkeypatch):
        """共享 canonical 的期望主题被删除（合并行承载主题数丢）→ 报错."""
        import yaml

        from check_docs_consistency import check_decision_tree_mapping

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            "## 1.8 任务类型 → 必读文件\n"
            "| 任务类型 | 必读入口 |\n"
            "| --- | --- |\n"
            "| 视图 | [docs/flet/README.md](./docs/flet/README.md) |\n",
            encoding="utf-8",
        )
        yml = tmp_path / "canonical-topics.yml"
        yml.write_text(
            yaml.safe_dump({"topics": [{"id": "a", "title": "A", "canonical": "docs/flet/README.md"}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "check_docs_consistency._DECISION_TREE_MERGED_IDS",
            {"docs/flet/README.md": {"a", "b"}},
        )
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", yml)

        errors = check_decision_tree_mapping()
        assert any("docs/flet/README.md" in e and "归属" in e for e in errors), (
            f"应检出共享 canonical 主题删除, got: {errors}"
        )

    def test_detects_unregistered_shared_canonical(self, tmp_path, monkeypatch):
        """单主题 canonical 被多个 yml 主题共享但未登记白名单 → 报错."""
        import yaml

        from check_docs_consistency import check_decision_tree_mapping

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            "## 1.8 任务类型 → 必读文件\n"
            "| 任务类型 | 必读入口 |\n"
            "| --- | --- |\n"
            "| 策略 | [docs/patterns/strategy.md](./docs/patterns/strategy.md) |\n",
            encoding="utf-8",
        )
        yml = tmp_path / "canonical-topics.yml"
        yml.write_text(
            yaml.safe_dump(
                {
                    "topics": [
                        {"id": "p1", "title": "P1", "canonical": "docs/patterns/strategy.md"},
                        {"id": "p2", "title": "P2", "canonical": "docs/patterns/strategy.md"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", yml)

        errors = check_decision_tree_mapping()
        assert any("docs/patterns/strategy.md" in e and "未登记" in e for e in errors), (
            f"应检出未登记共享 canonical, got: {errors}"
        )


class TestCanonicalRouting:
    """canonical 入口承担条件路由责任（DOC-05）：声明 workflow 的入口必须含指向 workflow 的链接."""

    def test_routing_pass_on_current_repo(self):
        """真实 canonical-topics.yml 中声明 workflow 的主题均应路由到 workflow（无错误）."""
        from check_docs_consistency import check_canonical_routing

        assert check_canonical_routing() == []

    def test_detects_missing_workflow_link(self, tmp_path, monkeypatch):
        """canonical 文档未含指向 workflow 的链接 → 报错."""
        import yaml

        from check_docs_consistency import check_canonical_routing

        # canonical 指向仓库真实文件（CONTRIBUTING.md），workflow 用制造的文件名确保缺失
        yml = tmp_path / "canonical-topics.yml"
        yml.write_text(
            yaml.safe_dump(
                {
                    "topics": [
                        {
                            "id": "dao",
                            "title": "DAO",
                            "canonical": "CONTRIBUTING.md",
                            "workflow": "docs/fabricated-routing-guide.md",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("check_docs_consistency.CANONICAL_TOPICS_YAML_PATH", yml)

        errors = check_canonical_routing()
        assert any("未路由到" in e and "fabricated-routing-guide.md" in e for e in errors), (
            f"应检出 canonical 未路由到 workflow, got: {errors}"
        )


class TestDocsIndexCompleteness:
    """文档索引全覆盖（DOC-07 / DOC-11）：docs/**/*.md 均被 CONTRIBUTING 或 docs/README 引用."""

    def test_index_pass_on_current_repo(self):
        """真实 docs/ 目录应全被两级索引覆盖（无错误）."""
        from check_docs_consistency import check_docs_index_completeness

        assert check_docs_index_completeness() == []

    def test_detects_uncovered_file(self, tmp_path, monkeypatch):
        """docs/ 下存在未被任何索引源引用的 .md → 报错."""
        from check_docs_consistency import check_docs_index_completeness

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True)
        (docs_dir / "README.md").write_text("# Index\n", encoding="utf-8")
        (docs_dir / "a.md").write_text("# A\n", encoding="utf-8")
        (docs_dir / "b.md").write_text("# B\n", encoding="utf-8")
        contributing = tmp_path / "CONTRIBUTING.md"
        contributing.write_text("# Contrib\n[a](./docs/a.md)\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.DOCS_README_PATH", docs_dir / "README.md")
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)

        errors = check_docs_index_completeness()
        assert any("b.md" in e and "未被 CONTRIBUTING.md" in e for e in errors), f"应检出未覆盖 b.md, got: {errors}"

    def test_directory_link_covers_subfiles(self, tmp_path, monkeypatch):
        """目录级引用应覆盖其下全部文件（含子目录）."""
        from check_docs_consistency import check_docs_index_completeness

        docs_dir = tmp_path / "docs"
        sub = docs_dir / "sub"
        sub.mkdir(parents=True)
        (docs_dir / "README.md").write_text("# Index\n[sub](./sub/)\n", encoding="utf-8")
        (sub / "x.md").write_text("# X\n", encoding="utf-8")
        (sub / "nested").mkdir()
        (sub / "nested" / "y.md").write_text("# Y\n", encoding="utf-8")
        contributing = tmp_path / "CONTRIBUTING.md"
        contributing.write_text("# Contrib\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.DOCS_README_PATH", docs_dir / "README.md")
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)

        assert check_docs_index_completeness() == [], "目录级引用应覆盖其下全部文件"


class TestReviewsIndexCompleteness:
    """检视方法论文档登记（DOC-07）：docs/reviews/README.md 以文件级链接登记顶层方法论."""

    def test_reviews_index_pass_on_current_repo(self):
        """真实 docs/reviews/README.md 应登记全部顶层方法论文档（无错误）."""
        from check_docs_consistency import check_reviews_index_completeness

        assert check_reviews_index_completeness() == []

    def test_detects_unregistered_top_level_doc(self, tmp_path, monkeypatch):
        """新增顶层方法论文档未在 README 登记 → 报错."""
        from check_docs_consistency import check_reviews_index_completeness

        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir(parents=True)
        (reviews_dir / "README.md").write_text("# Index\n[ai-review.md](./ai-review.md)\n", encoding="utf-8")
        (reviews_dir / "ai-review.md").write_text("# A\n", encoding="utf-8")
        (reviews_dir / "new-methodology.md").write_text("# N\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.REVIEWS_README_PATH", reviews_dir / "README.md")
        monkeypatch.setattr("check_docs_consistency.REVIEWS_DOCS_DIR", reviews_dir)

        errors = check_reviews_index_completeness()
        assert any("new-methodology.md" in e and "未登记" in e for e in errors), f"应检出未登记, got: {errors}"

    def test_detects_phantom_link(self, tmp_path, monkeypatch):
        """README 引用不存在的 docs/reviews/ 内文档 → 报错（幽灵链接）."""
        from check_docs_consistency import check_reviews_index_completeness

        reviews_dir = tmp_path / "reviews"
        reviews_dir.mkdir(parents=True)
        (reviews_dir / "README.md").write_text("# Index\n[ghost.md](./ghost.md)\n", encoding="utf-8")
        # 无 ghost.md 实体文件

        monkeypatch.setattr("check_docs_consistency.REVIEWS_README_PATH", reviews_dir / "README.md")
        monkeypatch.setattr("check_docs_consistency.REVIEWS_DOCS_DIR", reviews_dir)

        errors = check_reviews_index_completeness()
        assert any("ghost.md" in e and "引用了不存在的文档" in e for e in errors), f"应检出幽灵链接, got: {errors}"


class TestGovernanceIdReferences:
    """治理 id 引用一致性（DOC-09）：EX-\\d{4} 双向——引用须已登记，登记须被消费."""

    def test_id_refs_pass_on_current_repo(self):
        """真实 exceptions.yml（空）与消费文档间应无悬空/孤儿引用（无错误）."""
        from check_docs_consistency import check_governance_id_references

        assert check_governance_id_references() == []

    def test_detects_dangling_reference(self, tmp_path, monkeypatch):
        """消费文档引用未登记的 EX-\\d{4} → 报错."""
        from check_docs_consistency import check_governance_id_references

        docs_dir = tmp_path / "docs" / "governance"
        docs_dir.mkdir(parents=True)
        (docs_dir / "exceptions.yml").write_text("exceptions: []\n", encoding="utf-8")
        contributing = tmp_path / "CONTRIBUTING.md"
        contributing.write_text("引用已删除的例外 EX-0001\n", encoding="utf-8")
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# no EX reference\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", docs_dir / "exceptions.yml")
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)
        monkeypatch.setattr("check_docs_consistency.DOCS_README_PATH", tmp_path / "docs" / "README.md")

        errors = check_governance_id_references()
        assert any("EX-0001" in e and "未在 exceptions.yml" in e for e in errors), f"应检出悬空引用, got: {errors}"

    def test_detects_orphan_registration(self, tmp_path, monkeypatch):
        """exceptions.yml 登记但从未被消费文档引用 → 报错."""
        from check_docs_consistency import check_governance_id_references

        docs_dir = tmp_path / "docs" / "governance"
        docs_dir.mkdir(parents=True)
        (docs_dir / "exceptions.yml").write_text(
            "exceptions:\n  - id: EX-0001\n    rule_id: R1\n    reason: x\n"
            "    owner: n\n    approved_by: n\n    removal_trigger: x\n    verification: x\n"
            "    paths:\n      - CONTRIBUTING.md\n",
            encoding="utf-8",
        )
        contributing = tmp_path / "CONTRIBUTING.md"
        contributing.write_text("# no EX reference\n", encoding="utf-8")
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# no EX reference\n", encoding="utf-8")

        monkeypatch.setattr("check_docs_consistency.EXCEPTIONS_YAML_PATH", docs_dir / "exceptions.yml")
        monkeypatch.setattr("check_docs_consistency.CLAUDE_PATH", claude)
        monkeypatch.setattr("check_docs_consistency.CONTRIBUTING_PATH", contributing)
        monkeypatch.setattr("check_docs_consistency.DOCS_README_PATH", tmp_path / "docs" / "README.md")

        errors = check_governance_id_references()
        assert any("EX-0001" in e and "从未被任何消费文档引用" in e for e in errors), f"应检出孤儿登记, got: {errors}"
