"""Tests for documentation consistency (J② + J⑤) — 旧入口兼容转发（D9，检视 5.3）。

D9 按检视 §5.3 分层迁移后，本文件只保留依赖应用符号导入的合同
（TestLLMProviderCount / TestSingletonRegistryConsistency）；其余纯文档用例
已迁往 tests/docs/test_consistency.py（独立轻量环境），旧节点名经模块级转发
保持 `python -m pytest tests/unit/test_docs_consistency.py` 收集兼容。
转发完整性由 tests/docs/test_no_double_collection.py 守卫：同一用例禁止在
新/旧两处各持一份实现（双收集计为重复执行）。
"""

import re
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(autouse=True)
def _reset_doc_view_cache():
    """D5：统一解析视图缓存与本文件 `pathlib.Path.read_text` mock 的隔离。

    `_load_doc_view_cached` 以 (路径, mtime_ns, size) 为键；mock read_text 的测试
    不改动 stat() 结果——mock 前不清理会命中真实内容视图（mock 失效），mock 期间
    解析的假视图会驻留缓存、污染同 worker 后续真实读取。每个测试前后清空缓存。
    """
    from check_docs_consistency import _load_doc_view_cached

    _load_doc_view_cached.cache_clear()
    yield
    _load_doc_view_cached.cache_clear()


README_PATH = ROOT / "README.md"


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
    import services.news_sentiment_index_service  # noqa: F401 (side-effect 注册)
    import services.news_sentiment_scorer  # noqa: F401 (side-effect 注册)
    import services.news_subscription_service  # noqa: F401 (side-effect 注册)
    import services.task_manager  # noqa: F401 (side-effect 注册)
    import strategies.all_strategies  # noqa: F401 (side-effect 注册)
    import utils.scheduler_service  # noqa: F401 (side-effect 注册)
    import utils.thread_pool  # noqa: F401 (side-effect 注册)
    import utils.egress_audit  # noqa: F401 (side-effect 注册)
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


# ---------------------------------------------------------------------------
# D9 兼容转发：纯文档用例已迁至 tests/docs/test_consistency.py（检视 §5.3）。
# 仅服务已有应用环境选择器（旧节点名保转发）；tests/docs 轻量收集不经过本文件。
# import-not-found 为静态解析视角（运行时经 sys.path 动态解析，见 verify_versions 先例）。
# ---------------------------------------------------------------------------

_DOCS_TESTS_DIR = ROOT / "tests" / "docs"
if str(_DOCS_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_DOCS_TESTS_DIR))

from test_consistency import (  # type: ignore[import-not-found]  # tests/docs 目录运行时经 sys.path 动态解析
    TestVersionConsistency,
    TestSecurityVersion,
    TestRepoUrlConsistency,
    TestEmptyMarkdownLinks,
    TestReadmeRiskDisclaimer,
    TestClaudeReferences,
    TestStrategyExampleSignature,
    TestTrackedDocsLinksResolve,
    TestCoverageSourceConsistency,
    TestDaoCountConsistency,
    TestDocsConsistencyScript,
    TestNoteLazyFormatDetection,
    TestNoteLazyFormatRootLayout,
    TestDocsConsistencyScriptExtensions,
    TestRedlinesYamlConsistency,
    TestEnforcementMapping,
    TestEnforcementReverseCoverage,
    TestFletHubCompleteness,
    TestStrategyDescDynamicConsistency,
    TestExceptionsYamlConsistency,
    TestCanonicalTopicsYamlConsistency,
    TestCanonicalDocsAreGated,
    TestEvalsCaseIndexConsistency,
    TestAgentsMdSync,
    TestAgentsMinVerifyCommands,
    TestClaudeExecutiveSync,
    TestClaudeSelfCheckSync,
    TestRulesetMetadataConsistency,
    TestDecisionTreeMapping,
    TestCanonicalRouting,
    TestCanonicalCompletionCriteria,
    TestDocsIndexCompleteness,
    TestRulesetChangelogVersion,
    TestReviewsIndexCompleteness,
    TestGovernanceIdReferences,
    TestAgentsDeclaration,
    TestCoreModulesCompleteness,
    TestGovernanceIdGlossary,
    TestGovernanceIdGenericForm,
    TestAdrIndexCompleteness,
    TestScriptsIndexCompleteness,
    TestGuillemetReferences,
    TestCheckedDocsExclusions,
    TestCheckedDocsGitSource,
    TestProseMetaGuards,
    TestContractCountProse,
    TestSingletonCountProse,
    TestNonRegisteredSingletonDoc,
    TestAdrSupersedeChain,
    TestPrecommitHookAndWorkflowEnum,
    TestDocsWorkflowMergeQueueContract,
)
