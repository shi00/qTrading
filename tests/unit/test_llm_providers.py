"""utils.llm_providers 单元测试（AI-01 §3.1c：模型清单改依赖 litellm 官方目录）。

供应商/模型清单不再预置静态 models/tag/URL（由 ModelPicker 经 litellm 目录选择）。
本测试覆盖：
- 供应商识别元数据 / 分类 / 图标 / 常量
- litellm 官方目录投影（get_litellm_models_by_provider）：归一去重 / 稳定排序 /
  context 提取 / zhipu·azure·custom 空列表 / 缓存命中与版本失效
- 跨供应商搜索（search_models）
- 最近选用回记（get_recent_selections / record_selection）
- 模型信息对外契约（get_model_info）

litellm 惰性加载，测试用伪造模块注册进 sys.modules，不触真实安装。
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import utils.llm_providers as llm_providers
from utils.llm_providers import (
    AZURE_API_VERSIONS,
    AZURE_DEFAULT_API_VERSION,
    LLM_PROVIDERS,
    PROVIDER_CATEGORIES,
    RECENT_SELECTIONS_LIMIT,
    get_all_providers,
    get_cached_litellm_projection,
    get_enabled_provider_ids,
    get_litellm_models_by_provider,
    get_model_info,
    get_provider_by_id,
    get_provider_icon,
    get_provider_icon_path,
    get_providers_by_category,
    get_recent_selections,
    record_selection,
    search_models,
    search_providers,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def fake_litellm(monkeypatch):
    """注册伪造 litellm 模块（models_by_provider + model_cost）并隔离投影缓存。

    fake.models_by_provider 只覆盖需要断言的供应商；其余供应商 catalog key 缺失 → 空列表。
    _get_litellm_version patch 为固定值，避免依赖真实安装的 litellm 元数据。
    """
    fake = types.ModuleType("litellm")
    fake.models_by_provider = {
        "deepseek": {"deepseek-chat", "deepseek/v2", "deepseek-v3"},
        # review-pr1073 D1：dashscope 目录含第三方托管模型（deepseek/glm/kimi），投影须品牌过滤
        "dashscope": {"qwen-plus", "qwen/qwen-max", "deepseek-v4-flash", "glm-5.1", "kimi-k2.7-code"},
        "zai": {"glm-4.6", "zai/glm-5", "glm-5-flash"},
        "openai": {"gpt-4o", "gpt-4o-mini", "gpt-5-fallback"},
    }
    fake.model_cost = {
        "deepseek-chat": {"max_input_tokens": 65536},
        "deepseek/v2": {"max_tokens": 32000},
        "deepseek-v3": {"max_output_tokens": 16000},
        "qwen-plus": {"max_input_tokens": 131072},
        "qwen/qwen-max": {},
        "deepseek-v4-flash": {"max_input_tokens": 131072},
        "glm-5.1": {"max_input_tokens": 131072},
        "kimi-k2.7-code": {"max_input_tokens": 131072},
        "glm-4.6": {"max_input_tokens": 200000},
        "zai/glm-5": {"max_input_tokens": 128000},
        "glm-5-flash": {"max_tokens": 64000},
        "gpt-4o": {"max_input_tokens": 128000},
        "gpt-4o-mini": {"max_tokens": 64000},
    }

    # review-pr1073 A3/C2：model_cost 缺 max_* 时经 get_model_info 第二通道补齐。
    # gpt-4o（model_cost 有 max_input_tokens）不受影响；gpt-5-fallback（无条目）走第二通道。
    fake.get_model_info = lambda model: {"max_tokens": 16384} if "gpt-5-fallback" in model else {}

    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(llm_providers, "_PROJECTION_CACHE", {})
    monkeypatch.setattr(llm_providers, "_PROJECTION_CACHE_LITELLM_VERSION", "")
    monkeypatch.setattr(llm_providers, "_get_litellm_version", lambda: "v1-test")
    return fake


class TestGetProviderById:
    def test_existing_provider(self):
        provider = get_provider_by_id("deepseek")
        assert provider is not None
        assert "name" in provider

    def test_nonexistent_provider_returns_custom(self):
        provider = get_provider_by_id("nonexistent_provider")
        assert provider is not None
        assert provider.get("id") == "custom" or "name" in provider

    def test_custom_provider(self):
        provider = get_provider_by_id("custom")
        assert isinstance(provider, dict)


class TestGetAllProviders:
    def test_returns_dict(self):
        assert isinstance(get_all_providers(), dict)

    def test_has_deepseek(self):
        assert "deepseek" in get_all_providers()

    def test_has_custom(self):
        assert "custom" in get_all_providers()


class TestGetProvidersByCategory:
    def test_existing_category(self):
        for cat in PROVIDER_CATEGORIES:
            providers = get_providers_by_category(cat)
            assert isinstance(providers, list)

    def test_nonexistent_category(self):
        assert get_providers_by_category("nonexistent_category") == []


class TestGetProviderIconPath:
    def test_nonexistent_icon_returns_default(self):
        result = get_provider_icon_path("nonexistent_icon.png")
        assert "custom.png" in result

    @patch.object(Path, "exists", return_value=True)
    def test_existing_icon(self, mock_exists):
        result = get_provider_icon_path("deepseek.png")
        assert "deepseek.png" in result


class TestGetProviderIcon:
    def test_deepseek_icon(self):
        result = get_provider_icon("deepseek")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nonexistent_provider_icon(self):
        result = get_provider_icon("nonexistent_provider")
        assert isinstance(result, str)


class TestLLMProviderConstants:
    def test_azure_default_api_version(self):
        assert AZURE_DEFAULT_API_VERSION == "2025-04-01-preview"

    def test_azure_api_versions_is_list(self):
        assert isinstance(AZURE_API_VERSIONS, list)
        assert len(AZURE_API_VERSIONS) >= 1

    def test_recent_selections_limit_is_positive(self):
        assert isinstance(RECENT_SELECTIONS_LIMIT, int)
        assert RECENT_SELECTIONS_LIMIT > 0

    def test_llm_providers_has_core_providers(self):
        for pid in ("deepseek", "openai", "azure", "custom"):
            assert pid in LLM_PROVIDERS

    def test_provider_has_required_metadata_keys(self):
        required_keys = {"name", "name_en", "icon", "base_url", "key_prefix"}
        for pid, provider in LLM_PROVIDERS.items():
            missing = required_keys - set(provider.keys())
            assert not missing, f"Provider {pid} missing keys: {missing}"

    def test_no_static_models_in_providers(self):
        """§3.1c：供应商不应再携带硬编码 models 数组或 tag/URL 字段。"""
        for pid, provider in LLM_PROVIDERS.items():
            assert "models" not in provider, f"Provider {pid} 不应携带静态 models"
            assert "tag" not in provider, f"Provider {pid} 不应携带 tag"
            assert "url" not in provider, f"Provider {pid} 不应携带 url"

    def test_provider_categories_has_expected_entries(self):
        for cat in ("domestic", "international", "custom"):
            assert cat in PROVIDER_CATEGORIES

    def test_azure_marked_not_enumerable(self):
        assert LLM_PROVIDERS["azure"].get("azure_config") is True

    def test_custom_marked_custom(self):
        assert LLM_PROVIDERS["custom"].get("custom") is True


class TestLLMProviderCatalogKeyDecoupling:
    """§3.1c-review #9：litellm_catalog_key（UI 枚举）与 litellm_prefix（运行路由）解耦。"""

    def test_qwen_catalog_key_is_dashscope_not_openai(self):
        # qwen litellm_prefix 为 "openai"，但目录 key 应为 dashscope，避免与 GPT 撞车。
        assert LLM_PROVIDERS["qwen"]["litellm_catalog_key"] == "dashscope"

    def test_openai_catalog_key_is_itself(self):
        assert LLM_PROVIDERS["openai"]["litellm_catalog_key"] == "openai"

    def test_provider_without_catalog_key_falls_back_to_own_id(self):
        # custom 显式配置空 catalog key（自由文本，无目录）→ 回退自身 provider_id。
        # 注意：zhipu 已配置 zai 目录 key（与 qwen→dashscope 同范式），不再作为"无目录"样例。
        assert LLM_PROVIDERS["custom"].get("litellm_catalog_key", "") == ""

    def test_zhipu_catalog_key_is_zai(self):
        # review-pr1073 A1：litellm 官方目录中智谱键为 "zai"（Z.ai 品牌），
        # 与 qwen→dashscope 同范式，使 zhipu 可枚举官方模型且可计价。
        assert LLM_PROVIDERS["zhipu"]["litellm_catalog_key"] == "zai"


class TestLLMProviderName:
    """Tests for LLM provider name (merged from tests/unit/test_onboarding_wizard.py)."""

    def test_qwen_provider_name_is_correct(self):
        """Test that qwen provider name is '通义千问' not '阿里云通义千问'"""
        qwen = LLM_PROVIDERS.get("qwen", {})
        assert qwen.get("name") == "通义千问"


class TestGetLitellmModelsByProvider:
    def test_returns_only_supported_providers(self, fake_litellm):
        result = get_litellm_models_by_provider()
        assert set(result.keys()) == set(LLM_PROVIDERS.keys())

    def test_deepseek_models_normalized_and_sorted(self, fake_litellm):
        result = get_litellm_models_by_provider()
        ids = [m["id"] for m in result["deepseek"]]
        # deepseek/v2 → v2（按 / 右侧归一）；整体按 id 稳定排序。
        assert ids == ["deepseek-chat", "deepseek-v3", "v2"]

    def test_deepseek_context_extracted_from_cost(self, fake_litellm):
        result = get_litellm_models_by_provider()
        by_id = {m["id"]: m["context"] for m in result["deepseek"]}
        assert by_id["deepseek-chat"] == 65536  # max_input_tokens
        assert by_id["v2"] == 32000  # max_tokens（原始 deepseek/v2）
        assert by_id["deepseek-v3"] == 16000  # max_output_tokens

    def test_qwen_uses_dashscope_catalog_key(self, fake_litellm):
        result = get_litellm_models_by_provider()
        ids = [m["id"] for m in result["qwen"]]
        assert "qwen-plus" in ids
        assert "qwen-max" in ids  # qwen/qwen-max → qwen-max

    def test_qwen_projection_filters_cross_brand_models(self, fake_litellm):
        """review-pr1073 D1：dashscope 目录含跨品牌模型，投影到 qwen 组必须品牌过滤。

        用户不应在"通义(qwen)"组看到 DeepSeek/GLM/Kimi 模型（跨牌混配会误导误选）。
        """
        result = get_litellm_models_by_provider()
        ids = [m["id"] for m in result["qwen"]]
        assert "deepseek-v4-flash" not in ids
        assert "glm-5.1" not in ids
        assert "kimi-k2.7-code" not in ids

    def test_zhipu_uses_zai_catalog_key(self, fake_litellm):
        """review-pr1073 A1/M1：zhipu → litellm 目录键 zai，投影出 GLM 模型（归一去重 + context）。"""
        result = get_litellm_models_by_provider()
        ids = [m["id"] for m in result["zhipu"]]
        assert ids == ["glm-4.6", "glm-5", "glm-5-flash"]  # zai/glm-5 → glm-5（按 / 右侧归一）
        by_id = {m["id"]: m["context"] for m in result["zhipu"]}
        assert by_id["glm-4.6"] == 200000  # max_input_tokens

    def test_missing_catalog_key_provider_returns_empty(self, fake_litellm):
        """若某供应商目录 key 在 litellm 中不存在（升级韧性）→ 空列表，不崩 UI。"""
        result = get_litellm_models_by_provider()
        # fake 未提供 moonshot 目录 → 空列表兜底。
        assert result["moonshot"] == []

    def test_azure_and_custom_always_empty(self, fake_litellm):
        result = get_litellm_models_by_provider()
        assert result["azure"] == []
        assert result["custom"] == []

    def test_context_fallback_to_get_model_info(self, fake_litellm):
        """review-pr1073 A3/C2：model_cost 缺 max_* 时经 get_model_info 第二通道补齐。

        实机 gpt-4o model_cost 的 max_* 全为 None，但 get_model_info().max_tokens 有值；
        此处用 gpt-5-fallback（model_cost 无条目）验证回退路径。
        """
        result = get_litellm_models_by_provider()
        by_id = {m["id"]: m["context"] for m in result["openai"]}
        assert by_id["gpt-5-fallback"] == 16384  # 经 fake.get_model_info 补齐

    def test_cache_hit_isolates_from_fake_mutation(self, fake_litellm):
        """命中缓存后不应因 litellm 数据变化而重投影。"""
        get_litellm_models_by_provider()
        fake_litellm.models_by_provider["deepseek"] = {"new-model"}
        result = get_litellm_models_by_provider()
        assert any(m["id"] == "deepseek-chat" for m in result["deepseek"])

    def test_cache_invalidated_when_litellm_version_changes(self, fake_litellm, monkeypatch):
        """§3.1c：缓存键含 litellm 版本号，升级后失效重算。"""
        get_litellm_models_by_provider()
        fake_litellm.models_by_provider["deepseek"] = {"brand-new"}
        monkeypatch.setattr(llm_providers, "_get_litellm_version", lambda: "v2-test")
        result = get_litellm_models_by_provider()
        assert [m["id"] for m in result["deepseek"]] == ["brand-new"]


class TestGetEnabledProviderIds:
    """get_enabled_provider_ids: 供应商下拉清单的 litellm 目录推导（联动改造）。

    规则：azure/custom 无目录但特殊保留；目录型供应商仅当投影目录非空时保留；
    目录投影未加载（首帧）时回退 LLM_PROVIDERS 全量（R16 不触发惰性 import）。
    """

    def test_cache_unloaded_returns_all_providers(self, fake_litellm):
        """目录投影未加载（缓存为空）→ 回退全量（避免 UI 首帧触发 litellm 惰性 import）。"""
        result = get_enabled_provider_ids()
        assert set(result) == set(LLM_PROVIDERS.keys())
        assert result[0] == "deepseek"  # 按 PROVIDER_CATEGORIES 分类顺序

    def test_loaded_cache_filters_empty_catalog_providers(self, fake_litellm):
        """目录已加载 → 仅保留目录非空目录型供应商 + azure/custom（moonshot 等空目录隐藏）。"""
        get_litellm_models_by_provider()  # 触发投影并填充模块级缓存
        result = get_enabled_provider_ids()
        assert set(result) == {"deepseek", "qwen", "zhipu", "openai", "azure", "custom"}

    def test_loaded_cache_keeps_azure_and_custom(self, fake_litellm):
        """azure（部署制）/custom（自由文本）无目录但始终保留配置通道。"""
        get_litellm_models_by_provider()
        result = get_enabled_provider_ids()
        assert "azure" in result
        assert "custom" in result
        # 目标目录型供应商若目录为空 → 不出现
        assert "moonshot" not in result

    def test_untrusted_catalog_not_cached(self, monkeypatch):
        """目录不可信（litellm 为 MagicMock，测试会话全局 mock 场景）→ 全空投影不入缓存。

        原缺陷：全空投影以真实版本号入缓存，get_enabled_provider_ids 读到
        「全部供应商无模型」假象 → 下拉被清空。本测试固化「不入缓存 + 回退全量」。
        """
        fake = types.ModuleType("litellm")
        fake.models_by_provider = MagicMock()  # 会话级 mock：属性访问返回 MagicMock
        monkeypatch.setitem(sys.modules, "litellm", fake)
        monkeypatch.setattr(llm_providers, "_PROJECTION_CACHE", {})
        monkeypatch.setattr(llm_providers, "_PROJECTION_CACHE_LITELLM_VERSION", "")
        monkeypatch.setattr(llm_providers, "_get_litellm_version", lambda: "1.100.1")

        result = get_litellm_models_by_provider()
        assert set(result) == set(LLM_PROVIDERS.keys())  # 全空覆盖不崩调用方
        assert llm_providers._PROJECTION_CACHE == {}  # 未写入缓存
        assert get_cached_litellm_projection() is None
        # 缓存读取方不受污染：供应商下拉回退全量
        assert set(get_enabled_provider_ids()) == set(LLM_PROVIDERS.keys())


class TestSearchProviders:
    """search_providers: provider-only 选择器搜索（目录推导清单 + 名称子串过滤）。"""

    def test_empty_keyword_returns_empty(self):
        assert search_providers("") == []
        assert search_providers("   ") == []

    def test_matches_provider_id_substring(self):
        result = search_providers("deep")
        assert [r["provider_id"] for r in result] == ["deepseek"]

    def test_matches_chinese_display_name(self):
        """命中 name（如「通义千问」）也能检索到 qwen。"""
        assert any(r["provider_id"] == "qwen" for r in search_providers("通义"))

    def test_matches_name_en(self):
        assert any(r["provider_id"] == "moonshot" for r in search_providers("kimi"))

    def test_no_match_returns_empty(self):
        assert search_providers("zzz-no-such-provider") == []

    def test_result_stable_category_order(self):
        """按分类顺序（国内在前）稳定排序，与 get_enabled_provider_ids 一致。"""
        result = search_providers("a")  # 命中多个（deepseek/anthropic/mistral/google...）
        orders = [llm_providers._provider_order(r["provider_id"]) for r in result]
        assert orders == sorted(orders)
        assert len(result) >= 2

    def test_empty_catalog_fallback_still_lists_all(self):
        """目录未加载（缓存空）→ 回退全量清单，搜索照常工作（R16 不触发惰性 import）。"""
        result = search_providers("deep")
        assert result == [{"provider_id": "deepseek", "provider_name": "DeepSeek", "icon": "deepseek.png"}]


class TestSearchModels:
    def test_empty_keyword_returns_empty(self, fake_litellm):
        assert search_models("") == []
        assert search_models("   ") == []

    def test_matches_model_substring_case_insensitive(self, fake_litellm):
        result = search_models("chat")
        assert result
        assert all("chat" in row["model_id"].lower() for row in result)

    def test_matches_provider_name_boost(self, fake_litellm):
        result = search_models("openai")
        model_ids = {row["model_id"] for row in result}
        assert {"gpt-4o", "gpt-4o-mini"} <= model_ids

    def test_result_schema(self, fake_litellm):
        result = search_models("gpt-4o")
        assert result
        row = result[0]
        assert {"provider_id", "provider_name", "icon", "model_id", "context"} <= set(row.keys())

    def test_sorted_stable_by_order_then_name(self, fake_litellm):
        """按（分类顺序, provider name_en, 模型 id）稳定排序；domestic 分类先于 international。"""
        result = search_models("plus")
        assert result
        orders = [llm_providers._provider_order(row["provider_id"]) for row in result]
        assert orders == sorted(orders)


class TestGetRecentSelections:
    def test_malformed_config_returns_empty(self, monkeypatch):
        monkeypatch.setattr("utils.config_handler.ConfigHandler.load_config", lambda: "not-a-dict")
        assert get_recent_selections() == []

    def test_load_failure_returns_empty(self, monkeypatch):
        def raise_load():
            raise RuntimeError("boom")

        monkeypatch.setattr("utils.config_handler.ConfigHandler.load_config", raise_load)
        assert get_recent_selections() == []

    def test_deduplicates_and_preserves_order(self, monkeypatch):
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            lambda: {
                "llm_recent_selections": [
                    {"provider": "deepseek", "model": "m1"},
                    {"provider": "deepseek", "model": "m1"},
                    {"provider": "qwen", "model": "m2"},
                    {"provider": "qwen", "model": "m2"},
                ]
            },
        )
        assert get_recent_selections() == [
            {"provider": "deepseek", "model": "m1"},
            {"provider": "qwen", "model": "m2"},
        ]

    def test_filters_invalid_entries(self, monkeypatch):
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            lambda: {"llm_recent_selections": [{"provider": "deepseek"}, "junk", None]},
        )
        assert get_recent_selections() == []


class TestRecordSelection:
    @pytest.fixture
    def mock_config_handler(self, monkeypatch):
        saved = {}

        def fake_save(config):
            saved.clear()
            saved.update(config)

        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.load_config",
            lambda: {"llm_recent_selections": saved.get("llm_recent_selections", [])},
        )
        monkeypatch.setattr("utils.config_handler.ConfigHandler.save_config", fake_save)
        return saved

    def test_empty_input_noop(self, mock_config_handler):
        record_selection("", "m1")
        record_selection("deepseek", "")
        assert mock_config_handler == {}

    def test_prepends_new_selection(self, mock_config_handler):
        record_selection("deepseek", "m1")
        assert mock_config_handler["llm_recent_selections"] == [{"provider": "deepseek", "model": "m1"}]

    def test_duplicate_moves_to_front(self, mock_config_handler):
        record_selection("deepseek", "m1")
        record_selection("qwen", "m2")
        record_selection("deepseek", "m1")
        assert mock_config_handler["llm_recent_selections"] == [
            {"provider": "deepseek", "model": "m1"},
            {"provider": "qwen", "model": "m2"},
        ]

    def test_enforces_limit(self, mock_config_handler):
        for i in range(RECENT_SELECTIONS_LIMIT + 5):
            record_selection("p", f"m{i}")
        assert len(mock_config_handler["llm_recent_selections"]) == RECENT_SELECTIONS_LIMIT
        # 新的在最前
        assert mock_config_handler["llm_recent_selections"][0] == {
            "provider": "p",
            "model": f"m{RECENT_SELECTIONS_LIMIT + 4}",
        }

    def test_concurrent_threads_no_lost_tail(self, mock_config_handler):
        """Mi3（review-pr1073）：多线程并发回记不丢尾部条目（读改写加锁原子性）。

        两个 ModelPicker 实例（主配置面板 + failover 对话框）并发记录时，若不加锁，
        读缓存→改→写非原子会丢失尾部 1-2 条；本测试并发记录不同 (provider, model)
        后断言全部条目都在（顺序不要求，去重/上限之外不丢）。
        """
        import threading

        def _record(idx: int) -> None:
            record_selection(f"p{idx}", f"m{idx}")

        threads = [threading.Thread(target=_record, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entries = mock_config_handler["llm_recent_selections"]
        keys = {(e["provider"], e["model"]) for e in entries}
        assert len(keys) == 6
        assert all((f"p{i}", f"m{i}") in keys for i in range(6))


class TestGetModelInfo:
    def test_contract_shape_for_unknown_model(self, monkeypatch):
        """对外契约：始终返回 {"id","name","context"}。"""
        monkeypatch.setattr(llm_providers, "get_litellm_models_by_provider", lambda: {})
        monkeypatch.setattr("utils.config_handler.ConfigHandler.get_llm_config", lambda: {"custom_model_contexts": {}})
        info = get_model_info("deepseek", "unknown")
        assert info == {"id": "unknown", "name": "unknown", "context": 0}

    def test_context_from_projection(self, monkeypatch):
        monkeypatch.setattr(
            llm_providers,
            "get_litellm_models_by_provider",
            lambda: {"deepseek": [{"id": "deepseek-chat", "context": 65536}]},
        )
        info = get_model_info("deepseek", "deepseek-chat")
        assert info["context"] == 65536

    def test_fallback_to_custom_model_contexts(self, monkeypatch):
        """litellm 未计量（context 0）时回退 llm_custom_model_contexts 运行时覆盖。"""
        monkeypatch.setattr(llm_providers, "get_litellm_models_by_provider", lambda: {})
        monkeypatch.setattr(
            "utils.config_handler.ConfigHandler.get_llm_config",
            lambda: {"custom_model_contexts": {"deepseek": {"my-model": 2048}}},
        )
        info = get_model_info("deepseek", "my-model")
        assert info["context"] == 2048

    def test_litellm_lookup_failure_degrades_to_zero(self, monkeypatch):
        def raise_lookup():
            raise RuntimeError("fatal")

        monkeypatch.setattr(llm_providers, "get_litellm_models_by_provider", raise_lookup)
        monkeypatch.setattr("utils.config_handler.ConfigHandler.get_llm_config", lambda: {"custom_model_contexts": {}})
        info = get_model_info("deepseek", "any")
        assert info["context"] == 0


class TestLitellmHelperFunctions:
    def test_resolve_catalog_key_explicit(self):
        assert llm_providers._resolve_catalog_key("qwen") == "dashscope"
        assert llm_providers._resolve_catalog_key("zhipu") == "zai"
        assert llm_providers._resolve_catalog_key("moonshot") == "moonshot"

    def test_resolve_catalog_key_falls_back_to_own_id(self):
        # 未显式配置 catalog key 的 provider → 回退自身 provider_id（不是 litellm_prefix="openai"）。
        assert llm_providers._resolve_catalog_key("custom") == "custom"
        assert llm_providers._resolve_catalog_key("openai") == "openai"

    def test_normalize_model_id(self):
        assert llm_providers._normalize_model_id("deepseek/v2") == "v2"
        assert llm_providers._normalize_model_id("gpt-4o") == "gpt-4o"

    def test_extract_context_prioritizes_input_tokens(self):
        assert llm_providers._extract_context({"max_input_tokens": 100, "max_tokens": 50}) == 100
        assert llm_providers._extract_context({"max_tokens": 50}) == 50
        assert llm_providers._extract_context({}) == 0
        assert llm_providers._extract_context(None) == 0
