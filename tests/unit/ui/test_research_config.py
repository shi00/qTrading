"""研究配置统一入口与摘要 Prompt 默认资产单测（W-18）。

覆盖：
- 摘要 Prompt 默认资产与 W-10 压缩模块逐字对齐、保留条数默认不漂移；
- ``utils/config/llm.py`` 读写为单一来源（非法输入整体不提交）；
- 设置页 VM 与配置来源双向一致（改一处两处生效）；
- 「恢复默认」清空自定义值并回落默认资产；
- i18n 中英双语键齐备。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from core.prompt_base import RESEARCH_SUMMARY_PROMPT_DEFAULT
from services.agent_service.context_compaction import _SUMMARY_PROMPT, CompactionConfig
from ui.i18n import I18n
from ui.viewmodels import ai_brain_settings_view_model as vm_module
from ui.viewmodels.ai_brain_settings_view_model import (
    SAVE_ERROR,
    SAVE_SUCCESS,
    AIBrainSettingsViewModel,
)
from utils.config import llm as llm_module
from utils.config.llm import (
    DEFAULT_RESEARCH_ROLE,
    DEFAULT_RESEARCH_SUMMARY_ENABLED,
    DEFAULT_RESEARCH_SUMMARY_KEEP_MESSAGES,
    get_agent_research_role,
    get_agent_research_summary_enabled,
    get_agent_research_summary_keep_messages,
    get_agent_research_summary_prompt,
    is_agent_research_summary_prompt_default,
    reset_agent_research_summary_prompt,
    save_agent_research_config,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeConfigHandler:
    """最小配置后端：内存字典 + 本页其它读方法，验证单一来源读写链路。"""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.saved_payloads: list[dict[str, object]] = []

    def get_typed(self, key: str, typ: type, default: object) -> object:
        return self.data.get(key, default)

    def save_config(self, mapping: dict[str, object]) -> bool:
        self.saved_payloads.append(dict(mapping))
        self.data.update(mapping)
        return True

    def get_ai_max_candidates(self) -> int:
        return 30

    def get_strategy_min_turnover(self) -> float:
        return 2.0

    def get_ai_system_prompt(self) -> str:
        return "prompt"

    def get_ai_news_prompt(self) -> str:
        return "news"

    def get_ai_role_prompt_append(self, _role_key: str) -> str:
        return ""

    def get_ai_role_prompt_version(self, _role_key: str) -> str:
        return "v1"

    def is_ai_local_only_mode(self) -> bool:
        return False


@pytest.fixture
def fake_config(monkeypatch: pytest.MonkeyPatch) -> _FakeConfigHandler:
    fake = _FakeConfigHandler()
    monkeypatch.setattr(llm_module.cfg, "ConfigHandler", fake)
    monkeypatch.setattr(vm_module, "ConfigHandler", fake)
    return fake


@pytest.fixture
def sync_thread_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    tp_instance = MagicMock()

    async def _run(task_type, func, *args, **kwargs):
        return func(*args, **kwargs)

    tp_instance.run_async = _run
    monkeypatch.setattr(vm_module, "ThreadPoolManager", lambda: tp_instance)


class TestSummaryDefaultAssetAlignment:
    def test_default_asset_matches_w10_summary_prompt(self) -> None:
        assert RESEARCH_SUMMARY_PROMPT_DEFAULT == _SUMMARY_PROMPT

    def test_default_keep_messages_matches_compaction_config(self) -> None:
        compaction = CompactionConfig()
        assert compaction.enabled == DEFAULT_RESEARCH_SUMMARY_ENABLED
        assert compaction.keep_messages == DEFAULT_RESEARCH_SUMMARY_KEEP_MESSAGES

    def test_empty_store_falls_back_to_default_asset(self, fake_config: _FakeConfigHandler) -> None:
        assert get_agent_research_summary_prompt() == RESEARCH_SUMMARY_PROMPT_DEFAULT
        assert is_agent_research_summary_prompt_default() is True


class TestSingleSourceConfig:
    def test_save_then_read_roundtrip(self, fake_config: _FakeConfigHandler) -> None:
        saved = save_agent_research_config("sub", False, 12, "自定义摘要 Prompt")

        assert saved is True
        assert get_agent_research_role() == "sub"
        assert get_agent_research_summary_enabled() is False
        assert get_agent_research_summary_keep_messages() == 12
        assert get_agent_research_summary_prompt() == "自定义摘要 Prompt"
        assert is_agent_research_summary_prompt_default() is False

    @pytest.mark.parametrize(
        ("role", "enabled", "keep", "prompt"),
        [
            ("unknown", True, 6, ""),
            ("main", "yes", 6, ""),
            ("main", True, 0, ""),
            ("main", True, 51, ""),
            ("main", True, True, ""),
            ("main", True, 6, 123),
        ],
    )
    def test_invalid_input_is_not_persisted(
        self,
        fake_config: _FakeConfigHandler,
        role: str,
        enabled: object,
        keep: object,
        prompt: object,
    ) -> None:
        saved = save_agent_research_config(role, enabled, keep, prompt)  # type: ignore[arg-type]

        assert saved is False
        assert fake_config.saved_payloads == []

    def test_keep_messages_is_clamped_on_read(self, fake_config: _FakeConfigHandler) -> None:
        fake_config.data["agent_research_summary_keep_messages"] = 999
        assert get_agent_research_summary_keep_messages() == 50

    def test_invalid_role_falls_back_to_documented_default(self, fake_config: _FakeConfigHandler) -> None:
        fake_config.data["agent_research_role"] = "bogus"
        assert get_agent_research_role() == DEFAULT_RESEARCH_ROLE

    def test_reset_writes_empty_and_reads_default(self, fake_config: _FakeConfigHandler) -> None:
        save_agent_research_config("main", True, 6, "自定义")
        assert reset_agent_research_summary_prompt() is True

        assert fake_config.data["agent_research_summary_prompt"] == ""
        assert get_agent_research_summary_prompt() == RESEARCH_SUMMARY_PROMPT_DEFAULT


class TestSettingsViewModelSharedSource:
    def test_vm_loads_values_from_config_source(self, fake_config: _FakeConfigHandler) -> None:
        fake_config.data.update(
            {
                "agent_research_role": "sub",
                "agent_research_summary_enabled": False,
                "agent_research_summary_keep_messages": 9,
                "agent_research_summary_prompt": "",
                "llm_provider": "openai",
                "llm_model": "gpt-x",
            }
        )

        state = AIBrainSettingsViewModel().state

        assert state.research_role == "sub"
        assert state.research_summary_enabled is False
        assert state.research_summary_keep_messages_value == "9"
        assert state.research_summary_prompt_value == RESEARCH_SUMMARY_PROMPT_DEFAULT
        assert state.research_summary_prompt_is_default is True
        assert state.research_model_label == "openai/gpt-x"

    @pytest.mark.asyncio
    async def test_vm_save_writes_source_and_reloads(
        self, fake_config: _FakeConfigHandler, sync_thread_pool: None
    ) -> None:
        vm = AIBrainSettingsViewModel()
        vm.set_research_role("sub")
        vm.set_research_summary_enabled(False)
        vm.set_research_summary_keep_messages("11")
        vm.set_research_summary_prompt("自定义摘要")

        saved = await vm.save_research_config()

        assert saved is True
        assert vm.state.research_save_state == SAVE_SUCCESS
        # 配置来源（llm.py getter）与设置页 state 一致：改一处两处生效
        assert get_agent_research_role() == "sub"
        assert get_agent_research_summary_enabled() is False
        assert get_agent_research_summary_keep_messages() == 11
        assert get_agent_research_summary_prompt() == "自定义摘要"
        assert vm.state.research_role == "sub"
        assert vm.state.research_summary_keep_messages_value == "11"

    @pytest.mark.asyncio
    async def test_vm_invalid_keep_sets_warning_without_write(
        self, fake_config: _FakeConfigHandler, sync_thread_pool: None
    ) -> None:
        vm = AIBrainSettingsViewModel()
        vm.set_research_summary_keep_messages("abc")

        saved = await vm.save_research_config()

        assert saved is False
        assert vm.state.research_save_state == SAVE_ERROR
        assert vm.state.research_warning_key == "research_config_keep_invalid"
        assert fake_config.saved_payloads == []

    def test_vm_reset_prompt_restores_asset_then_saves_empty(self, fake_config: _FakeConfigHandler) -> None:
        vm = AIBrainSettingsViewModel()
        vm.set_research_summary_prompt("自定义摘要")

        vm.reset_research_summary_prompt()

        assert vm.state.research_summary_prompt_value == RESEARCH_SUMMARY_PROMPT_DEFAULT
        assert vm.state.research_summary_prompt_is_default is True


class TestResearchConfigI18n:
    KEYS = (
        "agent_workbench_title",
        "agent_workbench_toggle",
        "agent_workbench_tab_overview",
        "agent_workbench_tab_evidence",
        "agent_workbench_tab_conclusion",
        "agent_workbench_tab_config",
        "agent_workbench_empty_overview",
        "agent_workbench_empty_evidence",
        "agent_workbench_empty_conclusion",
        "agent_workbench_empty_config",
        "agent_workbench_unavailable",
        "agent_workbench_value_missing",
        "agent_workbench_config_entry_hint",
        "agent_workbench_query_label",
        "agent_workbench_query_hint",
        "agent_workbench_fact_session_title",
        "agent_workbench_fact_session_id",
        "agent_workbench_fact_updated",
        "agent_workbench_fact_pending",
        "agent_workbench_fact_messages",
        "agent_workbench_fact_artifacts",
        "agent_workbench_fact_executions",
        "agent_workbench_fact_answer",
        "agent_workbench_fact_delivery",
        "agent_workbench_fact_budget",
        "agent_workbench_fact_cost_round",
        "agent_workbench_fact_cost_cumulative",
        "agent_workbench_fact_model",
        "agent_workbench_fact_role",
        "agent_composer_menu_open",
        "agent_composer_menu_command",
        "agent_composer_menu_reference",
        "agent_composer_menu_skill",
        "agent_composer_menu_mcp",
        "research_config_title",
        "research_config_desc",
        "research_config_model_label",
        "research_config_model_hint",
        "research_config_budget_label",
        "research_config_budget_hint",
        "research_config_value_unset",
        "research_config_role_label",
        "research_config_summary_enabled",
        "research_config_summary_keep_label",
        "research_config_summary_keep_hint",
        "research_config_summary_prompt_label",
        "research_config_summary_prompt_hint",
        "research_config_prompt_source_default",
        "research_config_prompt_source_custom",
        "research_config_prompt_reset",
        "research_config_prompt_reset_title",
        "research_config_prompt_reset_body",
        "research_config_save",
        "research_config_saved",
        "research_config_save_failed",
        "research_config_keep_invalid",
    )

    def test_keys_present_in_both_locales(self) -> None:
        zh = json.loads((_REPO_ROOT / "locales" / "zh_CN" / "strings.json").read_text(encoding="utf-8"))
        en = json.loads((_REPO_ROOT / "locales" / "en_US" / "strings.json").read_text(encoding="utf-8"))

        for key in self.KEYS:
            assert zh.get(key), f"zh_CN missing {key}"
            assert en.get(key), f"en_US missing {key}"

    def test_keep_hint_placeholder_format(self) -> None:
        zh = json.loads((_REPO_ROOT / "locales" / "zh_CN" / "strings.json").read_text(encoding="utf-8"))
        formatted = zh["research_config_summary_keep_hint"].format(min=1, max=50)
        assert "1" in formatted and "50" in formatted


class TestResearchConfigCardWiring:
    def test_card_renders_and_wires_every_control(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import types

        import flet as ft

        import ui.views.settings_tabs.research_config_card as card_mod
        from tests.unit.ui.render_helper import render_component
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsState

        # SectionHeader 内部用 ft.use_state、DashboardCard 为 @ft.component（需 page 上下文），
        # 纯渲染测试替换为静态控件。
        monkeypatch.setattr(card_mod, "SectionHeader", lambda title, **_kwargs: ft.Text(title))
        monkeypatch.setattr(card_mod, "DashboardCard", lambda content, **_kwargs: ft.Container(content=content))

        vm = MagicMock()
        requested: list[str] = []
        state = AIBrainSettingsState(
            research_role="sub",
            research_summary_enabled=False,
            research_summary_keep_messages_value="9",
            research_summary_prompt_value="自定义摘要",
            research_summary_prompt_is_default=False,
            research_model_label="openai/gpt-x",
        )

        control = render_component(
            card_mod.ResearchConfigCard,
            state=state,
            vm=vm,
            budget_text="100",
            on_save=lambda: requested.append("save"),
            on_request_reset_prompt=lambda: requested.append("reset"),
        )

        widgets = list(_walk(control))
        dropdown = next(widget for widget in widgets if isinstance(widget, ft.Dropdown))
        assert dropdown.value == "sub"
        assert [option.key for option in dropdown.options] == ["main", "sub"]
        cast(Any, dropdown.on_select)(types.SimpleNamespace(control=types.SimpleNamespace(value="main")))
        vm.set_research_role.assert_called_once_with("main")

        switch = next(widget for widget in widgets if isinstance(widget, ft.Switch))
        assert switch.value is False
        cast(Any, switch.on_change)(types.SimpleNamespace(control=types.SimpleNamespace(value=True)))
        vm.set_research_summary_enabled.assert_called_once_with(True)

        keep_field = next(
            widget
            for widget in widgets
            if isinstance(widget, ft.TextField) and widget.keyboard_type == ft.KeyboardType.NUMBER
        )
        cast(Any, keep_field.on_change)(types.SimpleNamespace(control=types.SimpleNamespace(value="12")))
        vm.set_research_summary_keep_messages.assert_called_once_with("12")

        prompt_field = next(
            widget for widget in widgets if isinstance(widget, ft.TextField) and widget.multiline is True
        )
        assert prompt_field.value == "自定义摘要"
        cast(Any, prompt_field.on_change)(types.SimpleNamespace(control=types.SimpleNamespace(value="新摘要")))
        vm.set_research_summary_prompt.assert_called_once_with("新摘要")

        reset_button = next(widget for widget in widgets if isinstance(widget, ft.TextButton))
        cast(Any, reset_button.on_click)(MagicMock())
        save_button = next(widget for widget in widgets if isinstance(widget, ft.Button) and widget.disabled is False)
        cast(Any, save_button.on_click)(MagicMock())

        assert requested == ["reset", "save"]
        texts = [widget.value for widget in widgets if isinstance(widget, ft.Text)]
        assert I18n.get("research_config_prompt_source_custom") in texts
        assert "openai/gpt-x" in texts
        assert "100" in texts


def _walk(control: object):
    """深度遍历 Flet 控件树（仅测试断言用）。"""
    yield control
    children = getattr(control, "controls", None) or []
    content = getattr(control, "content", None)
    if content is not None:
        children = [*children, content]
    for child in children:
        yield from _walk(child)
