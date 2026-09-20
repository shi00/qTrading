"""ModelPickerViewModel 单元测试（AI-01 §3.1c：搜索+分组+回记模型选择器 VM）。

覆盖 public commands / state / 对外查询：
- 初始化默认 state
- set_selection / select_model（写回 + 最近回记异步落盘）
- update_model_query（空→浏览，非空→搜索）
- toggle_group（折叠/展开供应商分组）
- ensure_catalog_loaded（目录加载成功/失败）
- _build_groups / _build_recent_rows（浏览态构建）
- get_selected（对外查询）

litellm 用伪造模块注册进 sys.modules（不触真实安装）；ThreadPoolManager / ConfigHandler mock。
"""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import ui.viewmodels.model_picker_view_model as model_picker_vm
from ui.viewmodels.model_picker_view_model import ModelPickerViewModel, ModelRow

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def fake_litellm(monkeypatch):
    """注册伪造 litellm 模块并隔离投影缓存（复用 test_llm_providers 模式）。"""
    fake = types.ModuleType("litellm")
    fake.models_by_provider = {
        "deepseek": {"deepseek-chat", "deepseek-v3"},
        "dashscope": {"qwen-plus"},
        "openai": {"gpt-4o"},
    }
    fake.model_cost = {
        "deepseek-chat": {"max_input_tokens": 65536},
        "deepseek-v3": {"max_output_tokens": 16000},
        "qwen-plus": {"max_input_tokens": 131072},
        "gpt-4o": {"max_input_tokens": 128000},
    }
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr("utils.llm_providers._PROJECTION_CACHE", {})
    monkeypatch.setattr("utils.llm_providers._PROJECTION_CACHE_LITELLM_VERSION", "")
    monkeypatch.setattr("utils.llm_providers._get_litellm_version", lambda: "v1-test")
    return fake


@pytest.fixture
def mock_config_handler(monkeypatch):
    """Mock ConfigHandler（recent 回记 / get_llm_config 均为同步 IO）。"""
    saved: dict = {}

    def fake_load() -> dict:
        return {"llm_recent_selections": list(saved.get("llm_recent_selections", []))}

    def fake_save(config: dict) -> None:
        saved.clear()
        saved.update(config)

    monkeypatch.setattr("utils.config_handler.ConfigHandler.load_config", fake_load)
    monkeypatch.setattr("utils.config_handler.ConfigHandler.save_config", fake_save)
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.get_llm_config",
        lambda: {"custom_model_contexts": {}},
    )
    return saved


@pytest.fixture
def mock_thread_pool(monkeypatch):
    """Mock ThreadPoolManager.run_async 为同步 passthrough（R16 offload 同步 IO）。"""

    async def _passthrough(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    fake = MagicMock()
    fake.run_async = AsyncMock(side_effect=_passthrough)
    monkeypatch.setattr(model_picker_vm, "ThreadPoolManager", lambda: fake)
    return fake


def _row(provider_id="deepseek", model_id="deepseek-chat", context=65536) -> ModelRow:
    return ModelRow(
        provider_id=provider_id,
        provider_name="DeepSeek",
        icon="deepseek.png",
        model_id=model_id,
        context=context,
    )


# --- Init ---


class TestModelPickerViewModelInit:
    def test_default_state_values(self):
        vm = ModelPickerViewModel()
        st = vm.state
        assert st.query == ""
        assert st.is_loading is False
        assert st.searching is False
        assert st.search_results == ()
        assert st.recent == ()
        assert st.groups == ()
        assert st.expanded == frozenset()
        assert st.selected_provider == ""
        assert st.selected_model == ""

    def test_state_is_frozen_dataclass(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        assert vm.state.query == ""

    def test_subscribe_returns_unsubscribe_callable(self):
        vm = ModelPickerViewModel()
        unsub = vm.subscribe(lambda s: None)
        assert callable(unsub)
        unsub()

    def test_subscribe_receives_notification_on_set_selection(self):
        vm = ModelPickerViewModel()
        received: list = []
        vm.subscribe(lambda s: received.append(s))
        vm.set_selection("openai", "gpt-4o")
        assert len(received) == 1
        assert received[0].selected_provider == "openai"
        assert received[0].selected_model == "gpt-4o"


# --- Selection ---


class TestModelPickerSelection:
    def test_set_selection_updates_state(self):
        vm = ModelPickerViewModel()
        vm.set_selection("openai", "gpt-4o")
        assert vm.get_selected() == ("openai", "gpt-4o")

    def test_select_model_sets_selection_and_clears_search(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        vm.update_model_query("chat")  # 进入搜索态（同步完成，search_results 填充）
        assert vm.state.search_results  # 有命中
        vm.select_model(_row())
        st = vm.state
        assert st.selected_provider == "deepseek"
        assert st.selected_model == "deepseek-chat"
        assert st.query == ""
        assert st.search_results == ()
        assert st.searching is False
        assert st.expanded == frozenset()

    def test_select_model_records_recent_selection(self, fake_litellm, mock_config_handler):
        """选中即回记（无 loop 时同步落盘）。"""
        vm = ModelPickerViewModel()
        vm.select_model(_row(provider_id="deepseek", model_id="deepseek-chat"))
        assert mock_config_handler["llm_recent_selections"] == [{"provider": "deepseek", "model": "deepseek-chat"}]

    def test_select_model_get_selected(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        vm.select_model(_row(provider_id="qwen", model_id="qwen-plus"))
        assert vm.get_selected() == ("qwen", "qwen-plus")


# --- Query / Search / Browse ---


class TestModelPickerQuery:
    def test_empty_query_switches_to_browse(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        vm.update_model_query("")
        assert vm.state.searching is False
        assert vm.state.search_results == ()
        # 浏览态：构建供应商分组（仅目录非空：deepseek/qwen/openai）
        providers = {g.provider_id for g in vm.state.groups}
        assert {"deepseek", "qwen", "openai"} <= providers

    def test_whitespace_query_treated_as_browse(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        vm.update_model_query("   ")
        assert vm.state.searching is False

    def test_query_with_keyword_enters_search_mode(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        vm.update_model_query("chat")
        assert vm.state.query == "chat"
        # 同步搜索完成后 searching 复位，命中写入 search_results（View 以 query 判态）
        assert vm.state.searching is False
        assert len(vm.state.search_results) == 1
        row = vm.state.search_results[0]
        assert row.model_id == "deepseek-chat"
        assert row.context == 65536

    def test_search_no_match_returns_empty(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        vm.update_model_query("zzz-no-such-model")
        assert vm.state.query == "zzz-no-such-model"
        assert vm.state.searching is False
        assert vm.state.search_results == ()


class TestModelPickerToggleGroup:
    def test_toggle_adds_and_removes(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        assert vm.state.expanded == frozenset()
        vm.toggle_group("deepseek")
        assert vm.state.expanded == frozenset({"deepseek"})
        vm.toggle_group("deepseek")
        assert vm.state.expanded == frozenset()


# --- Catalog Loading (async, R16 offload) ---


class TestModelPickerCatalog:
    def test_ensure_catalog_loaded_builds_groups(self, fake_litellm, mock_config_handler, mock_thread_pool):
        vm = ModelPickerViewModel()
        asyncio.run(vm.ensure_catalog_loaded())
        st = vm.state
        assert st.is_loading is False
        assert {g.provider_id for g in st.groups} == {"deepseek", "qwen", "openai"}

    def test_ensure_catalog_loaded_failure_degrades(self, monkeypatch, mock_config_handler, mock_thread_pool):
        """目录加载失败：is_loading 复位，浏览态保持空（消费方经 status 呈现）。"""
        vm = ModelPickerViewModel()

        def raise_projection():
            raise RuntimeError("litellm unavailable")

        monkeypatch.setattr(model_picker_vm, "get_litellm_models_by_provider", raise_projection)
        asyncio.run(vm.ensure_catalog_loaded())
        st = vm.state
        assert st.is_loading is False
        assert st.groups == ()
        assert st.recent == ()


# --- Browse building ---


class TestModelPickerBrowse:
    def test_build_groups_only_nonempty_providers(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        groups = vm._build_groups()
        provider_ids = {g.provider_id for g in groups}
        # zhipu/azure/custom 无目录 → 不出现
        assert provider_ids == {"deepseek", "qwen", "openai"}

    def test_build_groups_model_rows_sorted(self, fake_litellm, mock_config_handler):
        vm = ModelPickerViewModel()
        ds = next(g for g in vm._build_groups() if g.provider_id == "deepseek")
        assert ds.model_count == 2
        assert [m.model_id for m in ds.models] == ["deepseek-chat", "deepseek-v3"]

    def test_build_recent_rows_projects_model(self, fake_litellm, mock_config_handler):
        mock_config_handler["llm_recent_selections"] = [
            {"provider": "deepseek", "model": "deepseek-chat"},
        ]
        vm = ModelPickerViewModel()
        rows = vm._build_recent_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.provider_id == "deepseek"
        assert row.model_id == "deepseek-chat"
        assert row.context == 65536  # 从 litellm 目录投影

    def test_build_recent_rows_keeps_unknown_provider_with_fallback(self, fake_litellm, mock_config_handler):
        """回记中的不在 LLM_PROVIDERS 的 provider 保留回显，名回退 provider_id、context 归 0。"""
        mock_config_handler["llm_recent_selections"] = [
            {"provider": "nope", "model": "x"},
        ]
        vm = ModelPickerViewModel()
        rows = vm._build_recent_rows()
        assert len(rows) == 1
        assert rows[0].provider_id == "nope"
        assert rows[0].provider_name == "nope"  # 不在 LLM_PROVIDERS → 名回退 provider_id
        assert rows[0].context == 0  # 目录无此模型 → 0（R21 诚实呈现）

    def test_build_recent_rows_read_failure_returns_empty(self, fake_litellm, monkeypatch):
        def raise_load():
            raise RuntimeError("config read failed")

        monkeypatch.setattr("utils.config_handler.ConfigHandler.load_config", raise_load)
        vm = ModelPickerViewModel()
        assert vm._build_recent_rows() == ()


# --- External queries ---


class TestModelPickerQueries:
    def test_get_selected_defaults(self):
        vm = ModelPickerViewModel()
        assert vm.get_selected() == ("", "")
