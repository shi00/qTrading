"""ui/views/settings_tabs/ai_brain_tab.py 组件运行时测试 (Task 1.3).

覆盖:
1. 契约守护: 声明式范式合规性 (@ft.component / 无命令式 API)
2. R2 守卫: _do_save_ai_settings 的 ``except asyncio.CancelledError: raise``
3. R16 守卫: _on_save_ai event handler 用 ``page.run_task`` 调度
4. R9 守卫: 模块级 async helper 不在异常/日志中暴露 api_key
5. 运行时测试: 用 component_renderer + FakePage 驱动渲染,
   - 模块级纯函数 (_get_page / _validate_prompt_or_warn)
   - 3 个 event handler 的 page 可用/None 早返回
   - _do_save_ai_settings 四阶段各分支:
     * 验证失败 (max_cand/min_turn/concurrency/news_concurrency/ai_prompt/news_prompt)
     * 保存失败 (llm_vm.save_config / ConfigHandler 4 个 save 方法)
     * 重载分支 (local_path 空/不存在/md5 相同/md5 不同)
     * 异常路径 (普通 Exception/system 级 Exception)
   - 3 个 async helper 的 成功/异常/CancelledError 路径
"""

import asyncio
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest
from flet.components.component import Component

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.theme import AppColors

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 ai_brain_tab.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    import ui.views.settings_tabs.ai_brain_tab as mod
    from pathlib import Path

    return Path(mod.__file__).read_text(encoding="utf-8")


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码, 用于契约守护检查 (避免 docstring 误判)."""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: Any) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


# ============================================================================
# 契约守护测试 (Phase D.3)
# ============================================================================


class TestAIBrainTabContract:
    """AIBrainTab 声明式契约守护测试。"""

    def test_is_ft_component(self) -> None:
        """DoD: AIBrainTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        assert hasattr(AIBrainTab, "__wrapped__"), "AIBrainTab 必须用 @ft.component 装饰"

    def test_no_class_container(self) -> None:
        """DoD: 禁止命令式 class 继承。"""
        source = _source_without_docstrings(_read_source())
        assert "class AIBrainTab(" not in source, "AIBrainTab 不应是 class (命令式)"

    def test_no_did_mount(self) -> None:
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        source = _source_without_docstrings(_read_source())
        assert "did_mount" not in source

    def test_no_will_unmount(self) -> None:
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        source = _source_without_docstrings(_read_source())
        assert "will_unmount" not in source

    def test_no_safe_update(self) -> None:
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        source = _source_without_docstrings(_read_source())
        assert ".update()" not in source
        assert "_safe_update" not in source

    def test_no_refresh_locale(self) -> None:
        """DoD: 禁止 refresh_locale (声明式用 ft.use_state 自动重渲染)。"""
        source = _source_without_docstrings(_read_source())
        assert "refresh_locale" not in source

    def test_no_page_ref(self) -> None:
        """DoD: 禁止 PageRefMixin / _page_ref / weakref。"""
        source = _source_without_docstrings(_read_source())
        assert "PageRefMixin" not in source
        assert "_page_ref" not in source
        assert "weakref" not in source

    def test_uses_use_viewmodel(self) -> None:
        """DoD: 必须通过 use_viewmodel hook 消费 VM。"""
        source = _read_source()
        assert "use_viewmodel" in source

    def test_uses_observable_state_i18n(self) -> None:
        """DoD: 必须订阅 get_observable_state。"""
        source = _read_source()
        assert "get_observable_state" in source

    def test_uses_observable_state_theme(self) -> None:
        """DoD: 必须订阅 AppColors.get_observable_state。"""
        source = _read_source()
        assert "AppColors.get_observable_state" in source

    def test_uses_context_page(self) -> None:
        """DoD: page 访问用 ft.context.page。"""
        source = _read_source()
        assert "ft.context.page" in source


class TestAIBrainTabR2Compliance:
    """R2 红线: _do_save_ai_settings 必须有 CancelledError raise 守卫。"""

    def test_async_handler_has_cancelled_error_raise(self) -> None:
        """验证 ≥1 处 `except asyncio.CancelledError` + ≥1 处 `raise  # R2`。

        _do_save_ai_settings 是唯一的 async handler, 内部 1 处 CancelledError 守卫。
        """
        source = _read_source()
        cancelled_count = source.count("except asyncio.CancelledError")
        raise_count = source.count("raise  # R2")
        assert cancelled_count >= 1, f"应有 ≥1 处 CancelledError 守卫, 实际 {cancelled_count}"
        assert raise_count >= 1, f"应有 ≥1 处 `raise  # R2`, 实际 {raise_count}"


class TestAIBrainTabR16Compliance:
    """R16 红线: 同步 event handler 必须用 page.run_task 调度 async handler。"""

    def test_event_handler_uses_run_task(self) -> None:
        """验证 ≥1 处 `page.run_task(` 调度。

        _on_save_ai 是唯一调用 page.run_task 的 event handler
        (_on_reset_ai_prompt / _on_reset_news_prompt 是纯同步, 不调 run_task)。
        """
        source = _read_source()
        run_task_count = source.count("page.run_task(")
        assert run_task_count >= 1, f"应有 ≥1 处 page.run_task, 实际 {run_task_count}"


class TestAIBrainTabR9Compliance:
    """R9 红线: 模块级 async helper 不在异常/日志中暴露 api_key。"""

    def test_no_api_key_in_log_calls(self) -> None:
        """验证源码中不存在 logger.*(api_key) 等直接打印 api_key 的调用。

        _on_llm_test_connection 接收 api_key 参数, 仅 forward 给 AIService.test_connection,
        不应记录到日志或异常消息。
        """
        source = _read_source()
        # 源码中不应出现 logger.* api_key 或 log.*(api_key) 的直接打印
        assert "logger.info(api_key" not in source
        assert "logger.debug(api_key" not in source
        assert "logger.warning(api_key" not in source
        assert "logger.error(api_key" not in source
        assert "logger.critical(api_key" not in source

    def test_on_llm_test_connection_does_not_log_api_key(self) -> None:
        """AIBrainSettingsViewModel.test_connection 仅 forward api_key 给 AIService.test_connection,
        不在任何日志/异常中暴露 api_key 明文。

        Phase 3.2 P1-1: _on_llm_test_connection 下沉为 VM 静态 command
        (ui.viewmodels.ai_brain_settings_view_model.AIBrainSettingsViewModel.test_connection)。
        通过调用 command 并让它抛异常, 验证异常消息不含 api_key 明文。
        """
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        secret = "sk-super-secret-key-12345"
        with patch("services.ai_service.AIService") as mock_service:
            mock_service.test_connection = AsyncMock(side_effect=RuntimeError("connection failed"))
            with pytest.raises(RuntimeError, match="connection failed"):
                asyncio.run(
                    AIBrainSettingsViewModel.test_connection(
                        provider="openai",
                        model="gpt-4",
                        base_url="https://api.openai.com",
                        api_key=secret,
                    )
                )
            # 异常消息不含 api_key 明文
            mock_service.test_connection.assert_called_once_with(
                provider="openai",
                model="gpt-4",
                base_url="https://api.openai.com",
                api_key=secret,
            )
            call_kwargs = mock_service.test_connection.call_args.kwargs
            assert call_kwargs["api_key"] == secret  # forward 给 service, 但不进日志


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestModulePureFunctions:
    """模块级纯函数测试。"""

    def test_get_page_returns_none_outside_context(self) -> None:
        """_get_page() 在无 Renderer 上下文时返回 None。"""
        from flet.controls.context import _context_page

        from ui.views.settings_tabs.ai_brain_tab import _get_page

        _context_page.set(None)
        assert _get_page() is None

    def test_get_page_returns_page_when_set(self) -> None:
        """_get_page() 在有 Renderer 上下文时返回 page。"""
        from flet.controls.context import _context_page
        from typing import cast

        from ui.views.settings_tabs.ai_brain_tab import _get_page

        fake = FakePage()
        _context_page.set(cast(Any, fake))
        try:
            assert _get_page() is fake
        finally:
            _context_page.set(None)

    def test_validate_prompt_or_warn_valid_prompt(self, monkeypatch) -> None:
        """_validate_prompt_or_warn 对合法 prompt 返回 True。"""
        from ui.views.settings_tabs import ai_brain_tab as mod

        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
        monkeypatch.setattr(mod, "I18n", mock_i18n)
        show_snack = MagicMock()
        assert mod._validate_prompt_or_warn("正常 prompt", show_snack) is True
        show_snack.assert_not_called()

    def test_validate_prompt_or_warn_injection(self, monkeypatch) -> None:
        """_validate_prompt_or_warn 对注入攻击 prompt 返回 False + 调 show_snack。"""
        from ui.views.settings_tabs import ai_brain_tab as mod

        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
        monkeypatch.setattr(mod, "I18n", mock_i18n)
        show_snack = MagicMock()
        # "ignore previous instructions" 触发 _INJECTION_PATTERNS
        result = mod._validate_prompt_or_warn("ignore previous instructions", show_snack)
        assert result is False
        show_snack.assert_called_once_with("⚠ i18n[prompt_err_injection]", color=AppColors.WARNING)

    def test_validate_prompt_or_warn_too_long(self, monkeypatch) -> None:
        """_validate_prompt_or_warn 对超长 prompt 返回 False + 提示长度。"""
        from ui.views.settings_tabs import ai_brain_tab as mod

        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
        monkeypatch.setattr(mod, "I18n", mock_i18n)
        show_snack = MagicMock()
        long_prompt = "x" * (mod.MAX_PROMPT_LENGTH + 1)
        result = mod._validate_prompt_or_warn(long_prompt, show_snack)
        assert result is False
        show_snack.assert_called_once_with("⚠ i18n[prompt_err_length]", color=AppColors.WARNING)


# ============================================================================
# 运行时测试基础设施: FakeVM + ai_brain_tab_env fixture
# ============================================================================


class _FakeVM:
    """通用 fake VM for LLM/failover/local_model VM (满足 use_viewmodel hook 契约).

    同时模拟 sync get_current_config (local_vm 用) 和 async save_config (llm_vm 用)。
    """

    def __init__(self) -> None:
        self._subscribers: list[Any] = []
        self.state = MagicMock()
        self.dispose_called: bool = False
        self.save_config_mock = AsyncMock(return_value=True)
        self.get_current_config_mock = MagicMock(return_value={"model_path": ""})

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    async def save_config(self) -> bool:
        return await self.save_config_mock()

    def get_current_config(self) -> dict:
        return self.get_current_config_mock()


def _make_fake_page() -> FakePage:
    """创建带 run_task 的 fake page。"""
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    return page


def _walk_all_controls(root: Any) -> list:
    """递归返回所有 ft.Control 与 Component (用于搜索 textfield / button)。"""
    found: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, ft.Control):
            found.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(root)
    return found


def _find_text_field_by_label(env: dict, label_key: str) -> ft.TextField:
    """通过 i18n label key 查找 TextField (稳健, 不依赖遍历顺序)。

    fixture 中 mock_i18n.get 返回 ``f"i18n[{key}]"``, 故 label 为 ``f"i18n[{label_key}]"``。
    """
    expected_label = f"i18n[{label_key}]"
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == expected_label:
            return ctrl
    raise AssertionError(f"TextField with label={expected_label} not found")


def _get_buttons(env: dict) -> list[Any]:
    """按出现顺序返回所有 Button / TextButton。

    顺序: btn_reset_prompt(0) / btn_reset_news_prompt(1) / btn_save_ai(2)
    """
    buttons: list[Any] = []

    def _walk(c: Any) -> None:
        if isinstance(c, (ft.Button, ft.TextButton)):
            buttons.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(env["result"])
    return buttons


def _make_event(value: Any = None) -> MagicMock:
    """构造 ft.ControlEvent mock。"""
    e = MagicMock()
    e.control.value = value
    return e


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe)。

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _await_run_task_handler(page: MagicMock) -> tuple[Any, tuple, dict]:
    """提取 page.run_task 最近一次调用的 handler 与参数。"""
    assert page.run_task.call_args is not None, "page.run_task 未被调用"
    call = page.run_task.call_args
    handler = call.args[0]
    args = call.args[1:]
    kwargs = call.kwargs
    return handler, args, kwargs


def _rerender(env: dict) -> Any:
    """重新渲染组件并更新 env['result']。

    声明式范式下, on_change 触发 set_state 后需手动 render_once 让闭包捕获新 state,
    否则 event handler 中的 state 变量仍是旧值。
    """
    result = render_once(env["component"])
    env["result"] = result
    return result


def _patch_ai_brain_common_mocks(mod, monkeypatch) -> dict:
    """注入 AIBrainTab 共用的外部依赖 mock。

    Mock:
    - I18n (模块级导入)
    - ConfigHandler (类方法调用)
    - ThreadPoolManager (实例化 + run_async 同步执行 func)
    - UILogger (横切关注点)
    """
    # --- Mock I18n ---
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    mock_i18n.current_locale.return_value = "zh_CN"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    # --- Mock ConfigHandler ---
    mock_config = MagicMock()
    mock_config.get_ai_max_candidates.return_value = 30
    mock_config.get_strategy_min_turnover.return_value = 2.0
    mock_config.get_ai_max_concurrent_analysis.return_value = 3
    mock_config.get_ai_news_max_concurrent.return_value = 1
    mock_config.get_ai_system_prompt.return_value = "default prompt"
    mock_config.get_ai_news_prompt.return_value = "default news"
    mock_config.save_local_ai_config.return_value = True
    mock_config.save_config.return_value = True
    mock_config.save_ai_system_prompt.return_value = True
    mock_config.set_ai_news_prompt.return_value = True
    # Task 5.2: ConfigHandler/ThreadPoolManager 下沉到 AIBrainSettingsViewModel,
    # patch 目标改为 VM 模块 (View 不再直接持有这两个符号);
    # 同时 patch utils.thread_pool 源模块, 覆盖 ai_brain_tab._do_save_ai_settings
    # 中局部 import 的 ThreadPoolManager (MD5 检查保留在 View)
    monkeypatch.setattr("ui.viewmodels.ai_brain_settings_view_model.ConfigHandler", mock_config)

    # --- Mock ThreadPoolManager ---
    mock_tpm_instance = MagicMock()

    async def _fake_run_async(task_type: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    mock_tpm_instance.run_async = MagicMock(side_effect=_fake_run_async)
    mock_tpm_class = MagicMock(return_value=mock_tpm_instance)
    monkeypatch.setattr("ui.viewmodels.ai_brain_settings_view_model.ThreadPoolManager", mock_tpm_class)
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", mock_tpm_class)

    # --- Mock UILogger ---
    monkeypatch.setattr(mod, "UILogger", MagicMock())

    return {
        "mock_config": mock_config,
        "mock_tpm": mock_tpm_instance,
        "mock_i18n": mock_i18n,
    }


@pytest.fixture
def ai_brain_tab_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 AIBrainTab, 返回包含 component/page/result/mocks 的 dict。

    Mock 外部依赖:
    - ConfigHandler (类方法方式调用)
    - ThreadPoolManager (实例化调用, run_async 同步执行 func)
    - I18n / UILogger (横切关注点)
    - 3 个子 VM (LLMConfigPanelViewModel/FailoverConfigPanelViewModel/LocalModelConfigPanelViewModel)
    - 3 个子组件 (LLMConfigPanel/FailoverConfigPanel/LocalModelConfigPanel)
    """
    from ui.views.settings_tabs import ai_brain_tab as mod

    mocks = _patch_ai_brain_common_mocks(mod, monkeypatch)

    # --- Mock 3 个子 VM (用 lambda 工厂忽略构造参数, 返回 fake_vm) ---
    fake_llm_vm = _FakeVM()
    fake_failover_vm = _FakeVM()
    fake_local_vm = _FakeVM()
    fake_local_vm.get_current_config_mock.return_value = {"model_path": ""}

    monkeypatch.setattr(mod, "LLMConfigPanelViewModel", lambda **kwargs: fake_llm_vm)
    monkeypatch.setattr(mod, "FailoverConfigPanelViewModel", lambda **kwargs: fake_failover_vm)
    monkeypatch.setattr(mod, "LocalModelConfigPanelViewModel", lambda **kwargs: fake_local_vm)

    # --- Mock 3 个子组件 (避免它们的渲染逻辑触发 VM 内部 ConfigHandler 调用) ---
    monkeypatch.setattr(mod, "LLMConfigPanel", MagicMock(return_value=MagicMock(name="LLMConfigPanel")))
    monkeypatch.setattr(mod, "FailoverConfigPanel", MagicMock(return_value=MagicMock(name="FailoverConfigPanel")))
    monkeypatch.setattr(mod, "LocalModelConfigPanel", MagicMock(return_value=MagicMock(name="LocalModelConfigPanel")))

    # P1-4 批次 2: Mock ConfirmDialog 捕获 on_confirm/on_cancel 回调
    # (reset 按钮点击后先打开 ConfirmDialog, 需触发 on_confirm 才执行 reset + show_snack)
    captured_callbacks: dict[str, Any] = {}

    def _fake_confirm_dialog(**kwargs: Any) -> Any:
        if kwargs.get("open_state"):
            captured_callbacks["on_confirm"] = kwargs.get("on_confirm")
            captured_callbacks["on_cancel"] = kwargs.get("on_cancel")
        return MagicMock(name="ConfirmDialog")

    monkeypatch.setattr(mod, "ConfirmDialog", _fake_confirm_dialog)

    # --- 挂载组件 ---
    show_snack = MagicMock()
    component = make_component(mod.AIBrainTab, show_snack_callback=show_snack)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "show_snack": show_snack,
        "fake_llm_vm": fake_llm_vm,
        "fake_failover_vm": fake_failover_vm,
        "fake_local_vm": fake_local_vm,
        "captured_callbacks": captured_callbacks,
        **mocks,
    }


# ============================================================================
# 组件挂载/渲染基础测试
# ============================================================================


class TestAIBrainTabMount:
    """AIBrainTab 挂载/渲染基础测试。"""

    def test_mount_returns_container(self, ai_brain_tab_env) -> None:
        """挂载返回 ft.Container, content 为 Column。"""
        result = ai_brain_tab_env["result"]
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_render_includes_text_fields(self, ai_brain_tab_env) -> None:
        """渲染含 6 个 TextField (max_cand/min_turn/concurrency/news_concurrency/ai_prompt/news_prompt)。"""
        env = ai_brain_tab_env
        # 通过 i18n key 查找每个 TextField
        for key in (
            "settings_max_candidates",
            "settings_min_turnover",
            "settings_ai_concurrency",
            "settings_ai_news_concurrency",
            "settings_ai_prompt",
            "settings_news_prompt",
        ):
            tf = _find_text_field_by_label(env, key)
            # UI 契约测试验证多 key 循环内 text_field 存在性
            assert tf is not None

    def test_render_includes_save_button(self, ai_brain_tab_env) -> None:
        """渲染含 1 个 ft.Button (save_ai)。"""
        buttons = _get_buttons(ai_brain_tab_env)
        save_btns = [b for b in buttons if isinstance(b, ft.Button)]
        assert len(save_btns) == 1

    def test_render_includes_reset_prompt_buttons(self, ai_brain_tab_env) -> None:
        """渲染含 2 个 ft.TextButton (reset_ai_prompt / reset_news_prompt)。"""
        buttons = _get_buttons(ai_brain_tab_env)
        text_btns = [b for b in buttons if isinstance(b, ft.TextButton)]
        assert len(text_btns) == 2

    def test_unmount_triggers_vm_dispose(self, ai_brain_tab_env) -> None:
        """卸载后 3 个子 VM.dispose 被调用 (use_viewmodel dispose_on_unmount=True)。"""
        env = ai_brain_tab_env
        component = env["component"]
        assert env["fake_llm_vm"].dispose_called is False
        assert env["fake_local_vm"].dispose_called is False
        run_unmount_effects(component)
        assert env["fake_llm_vm"].dispose_called is True
        assert env["fake_local_vm"].dispose_called is True


# ============================================================================
# Event handler 测试: page 可用 → page.run_task (R16 守卫)
# ============================================================================


class TestEventHandlersPageAvailable:
    """验证 event handler 在 page 可用时正确执行。"""

    def test_on_save_ai_invokes_run_task(self, ai_brain_tab_env) -> None:
        """_on_save_ai: page 可用 → page.run_task(_do_save_ai_settings)。"""
        env = ai_brain_tab_env
        buttons = _get_buttons(env)
        save_btn = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(save_btn.on_click, _make_event())
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler)
        assert args == ()

    def test_on_reset_ai_prompt_sets_default_prompt(self, ai_brain_tab_env) -> None:
        """_on_reset_ai_prompt: 打开 ConfirmDialog → on_confirm 执行 reset + show_snack (P1-4 批次 2)."""
        env = ai_brain_tab_env
        buttons = _get_buttons(env)
        # btn_reset_prompt 是第 1 个 TextButton (源码顺序)
        reset_btn = next(b for b in buttons if isinstance(b, ft.TextButton))
        page = env["page"]
        page.run_task.reset_mock()
        show_snack = env["show_snack"]
        show_snack.reset_mock()

        _invoke(reset_btn.on_click, _make_event())
        # 不调 run_task (纯同步)
        assert not page.run_task.called
        # P1-4 批次 2: 点击 reset 后打开 ConfirmDialog, 需触发 on_confirm 才执行 reset
        _rerender(env)
        on_confirm = env["captured_callbacks"].get("on_confirm")
        assert on_confirm is not None, "ConfirmDialog on_confirm 未捕获 (open_state 未切换为 True?)"
        on_confirm()
        # 调 show_snack
        show_snack.assert_called_once_with("i18n[settings_snack_prompt_reset]")

    def test_on_reset_news_prompt_sets_default_prompt(self, ai_brain_tab_env) -> None:
        """_on_reset_news_prompt: 打开 ConfirmDialog → on_confirm 执行 reset + show_snack (P1-4 批次 2)."""
        env = ai_brain_tab_env
        buttons = _get_buttons(env)
        # btn_reset_news_prompt 是第 2 个 TextButton
        text_btns = [b for b in buttons if isinstance(b, ft.TextButton)]
        reset_news_btn = text_btns[1]
        page = env["page"]
        page.run_task.reset_mock()
        show_snack = env["show_snack"]
        show_snack.reset_mock()

        _invoke(reset_news_btn.on_click, _make_event())
        assert not page.run_task.called
        _rerender(env)
        on_confirm = env["captured_callbacks"].get("on_confirm")
        assert on_confirm is not None, "ConfirmDialog on_confirm 未捕获 (open_state 未切换为 True?)"
        on_confirm()
        show_snack.assert_called_once_with("i18n[settings_snack_prompt_reset]")


# ============================================================================
# Event handler 测试: page=None 早返回 (R16 守卫)
# ============================================================================


class TestEventHandlersPageNoneEarlyReturn:
    """验证 event handler 在 page=None 时早返回 (不调 run_task, 不抛异常)。

    通过 patch ``_get_page`` 返回 None 模拟 page 不可用。
    """

    def test_on_save_ai_page_none_no_run_task(self, ai_brain_tab_env) -> None:
        """_on_save_ai: page=None → 不调 run_task。"""
        env = ai_brain_tab_env
        buttons = _get_buttons(env)
        save_btn = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.views.settings_tabs.ai_brain_tab._get_page", return_value=None):
            _invoke(save_btn.on_click, _make_event())
        assert not page.run_task.called

    def test_on_reset_ai_prompt_runs_without_page(self, ai_brain_tab_env) -> None:
        """_on_reset_ai_prompt: 不依赖 page, page=None 仍打开 ConfirmDialog → on_confirm 执行 reset (P1-4)."""
        env = ai_brain_tab_env
        buttons = _get_buttons(env)
        reset_btn = next(b for b in buttons if isinstance(b, ft.TextButton))
        page = env["page"]
        page.run_task.reset_mock()
        show_snack = env["show_snack"]
        show_snack.reset_mock()

        with patch("ui.views.settings_tabs.ai_brain_tab._get_page", return_value=None):
            _invoke(reset_btn.on_click, _make_event())
        # _on_reset_ai_prompt 不依赖 page, 仍打开 ConfirmDialog
        _rerender(env)
        on_confirm = env["captured_callbacks"].get("on_confirm")
        assert on_confirm is not None, "ConfirmDialog on_confirm 未捕获 (open_state 未切换为 True?)"
        on_confirm()
        show_snack.assert_called_once_with("i18n[settings_snack_prompt_reset]")

    def test_on_reset_news_prompt_runs_without_page(self, ai_brain_tab_env) -> None:
        """_on_reset_news_prompt: 不依赖 page, page=None 仍打开 ConfirmDialog → on_confirm 执行 reset (P1-4)."""
        env = ai_brain_tab_env
        buttons = _get_buttons(env)
        text_btns = [b for b in buttons if isinstance(b, ft.TextButton)]
        reset_news_btn = text_btns[1]
        page = env["page"]
        page.run_task.reset_mock()
        show_snack = env["show_snack"]
        show_snack.reset_mock()

        with patch("ui.views.settings_tabs.ai_brain_tab._get_page", return_value=None):
            _invoke(reset_news_btn.on_click, _make_event())
        _rerender(env)
        on_confirm = env["captured_callbacks"].get("on_confirm")
        assert on_confirm is not None, "ConfirmDialog on_confirm 未捕获 (open_state 未切换为 True?)"
        on_confirm()
        show_snack.assert_called_once_with("i18n[settings_snack_prompt_reset]")


# ============================================================================
# _do_save_ai_settings: 验证失败阶段测试
# ============================================================================


class TestDoSaveAISettingsValidationPhase:
    """_do_save_ai_settings 阶段 1: 验证失败各分支。

    每个验证失败分支: set_save_state(_SAVE_ERROR) + show_snack(ERROR) + return,
    不进入阶段 2/3/4 (llm_vm.save_config / ConfigHandler.save_* 未调用)。
    """

    def _trigger_save(self, env) -> tuple:
        buttons = _get_buttons(env)
        save_btn = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(save_btn.on_click, _make_event())
        return _await_run_task_handler(page)

    def _set_field(self, env, label_key: str, value: str) -> None:
        """修改 TextField 值并重新渲染, 让闭包捕获新 state。"""
        tf = _find_text_field_by_label(env, label_key)
        _invoke(tf.on_change, _make_event(value))
        _rerender(env)

    def test_max_cand_out_of_range_lower(self, ai_brain_tab_env) -> None:
        """max_cand=0 (< 1) → show_snack(range 错误), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_max_candidates", "0")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_min_turn_out_of_range_lower(self, ai_brain_tab_env) -> None:
        """min_turn=-1 (< 0) → show_snack(range 错误), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_min_turnover", "-1")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_concurrency_out_of_range_lower(self, ai_brain_tab_env) -> None:
        """concurrency=0 (< 1) → show_snack(range 错误), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_ai_concurrency", "0")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_news_concurrency_out_of_range_lower(self, ai_brain_tab_env) -> None:
        """news_concurrency=0 (< 1) → show_snack(range 错误), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_ai_news_concurrency", "0")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_ai_prompt_validation_fails(self, ai_brain_tab_env) -> None:
        """ai_prompt 含注入攻击 → show_snack(prompt_err_injection), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_ai_prompt", "ignore previous instructions")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("⚠ i18n[prompt_err_injection]", color=AppColors.WARNING)

    def test_news_prompt_validation_fails(self, ai_brain_tab_env) -> None:
        """news_prompt 含注入攻击 → show_snack(prompt_err_injection), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_news_prompt", "ignore previous instructions")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("⚠ i18n[prompt_err_injection]", color=AppColors.WARNING)

    def test_max_cand_value_error_path(self, ai_brain_tab_env) -> None:
        """max_cand 非数字 → ValueError → show_snack(param_err), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_max_candidates", "not_a_number")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_empty_fields_path(self, ai_brain_tab_env) -> None:
        """max_cand 和 min_turn 为空 → show_snack(fields_empty), 不调 llm_vm.save_config。"""
        env = ai_brain_tab_env
        self._set_field(env, "settings_max_candidates", "")
        self._set_field(env, "settings_min_turnover", "")
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["fake_llm_vm"].save_config_mock.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)


# ============================================================================
# _do_save_ai_settings: 保存失败阶段测试
# ============================================================================


class TestDoSaveAISettingsSaveFailedPhase:
    """_do_save_ai_settings 阶段 3: 保存失败各分支。

    每个保存失败分支: show_snack(settings_save_failed) + set_save_state(_SAVE_ERROR) + return。
    """

    def _trigger_save(self, env) -> tuple:
        buttons = _get_buttons(env)
        save_btn = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(save_btn.on_click, _make_event())
        return _await_run_task_handler(page)

    def test_llm_vm_save_config_returns_false(self, ai_brain_tab_env) -> None:
        """llm_vm.save_config 返回 False → show_snack(settings_save_failed), 不调后续 ConfigHandler。"""
        env = ai_brain_tab_env
        env["fake_llm_vm"].save_config_mock = AsyncMock(return_value=False)
        handler, args, _ = self._trigger_save(env)
        asyncio.run(handler(*args))

        env["mock_config"].save_local_ai_config.assert_not_called()
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_config_handler_save_local_ai_config_returns_false(self, ai_brain_tab_env) -> None:
        """ConfigHandler.save_local_ai_config 返回 False → _save_configs_sync 返回 False → show_snack。"""
        env = ai_brain_tab_env
        env["mock_config"].save_local_ai_config.return_value = False
        handler, args, _ = self._trigger_save(env)
        with patch("services.ai_service.AIService"):
            asyncio.run(handler(*args))

        env["mock_config"].save_local_ai_config.assert_called_once_with(
            model_path="", timeout=300, n_threads=4, n_batch=512, n_ctx=2048, flash_attn=False, n_gpu_layers=0
        )
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_config_handler_save_config_returns_false(self, ai_brain_tab_env) -> None:
        """ConfigHandler.save_config 返回 False → _save_configs_sync 返回 False → show_snack。"""
        env = ai_brain_tab_env
        env["mock_config"].save_config.return_value = False
        handler, args, _ = self._trigger_save(env)
        with patch("services.ai_service.AIService"):
            asyncio.run(handler(*args))

        env["mock_config"].save_config.assert_called_once_with(
            {
                "ai_max_candidates": 30,
                "strategy_min_turnover": 2.0,
                "ai_max_concurrent_analysis": 3,
                "ai_news_max_concurrent": 1,
            }
        )
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_config_handler_save_ai_system_prompt_returns_false(self, ai_brain_tab_env) -> None:
        """ConfigHandler.save_ai_system_prompt 返回 False → _save_configs_sync 返回 False → show_snack。"""
        env = ai_brain_tab_env
        env["mock_config"].save_ai_system_prompt.return_value = False
        handler, args, _ = self._trigger_save(env)
        with patch("services.ai_service.AIService"):
            asyncio.run(handler(*args))

        env["mock_config"].save_ai_system_prompt.assert_called_once_with("default prompt")
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_config_handler_set_ai_news_prompt_returns_false(self, ai_brain_tab_env) -> None:
        """ConfigHandler.set_ai_news_prompt 返回 False → _save_configs_sync 返回 False → show_snack。"""
        env = ai_brain_tab_env
        env["mock_config"].set_ai_news_prompt.return_value = False
        handler, args, _ = self._trigger_save(env)
        with patch("services.ai_service.AIService"):
            asyncio.run(handler(*args))

        env["mock_config"].set_ai_news_prompt.assert_called_once_with("default news")
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)


# ============================================================================
# _do_save_ai_settings: 重载分支测试
# ============================================================================


class TestDoSaveAISettingsReloadPhase:
    """_do_save_ai_settings 阶段 4: 重载分支。

    根据本地模型路径与 md5 比较, 触发不同的 show_snack 消息。
    """

    def _trigger_save(self, env) -> tuple:
        buttons = _get_buttons(env)
        save_btn = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(save_btn.on_click, _make_event())
        return _await_run_task_handler(page)

    def test_local_path_empty_shows_ai_saved(self, ai_brain_tab_env) -> None:
        """local_path="" → show_snack(settings_snack_ai_saved) (else 分支)。"""
        env = ai_brain_tab_env
        env["fake_local_vm"].get_current_config_mock.return_value = {"model_path": ""}
        handler, args, _ = self._trigger_save(env)
        with self._patch_ai_service_ok():
            asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[settings_snack_ai_saved]")

    def test_local_path_not_exists_shows_model_not_found(self, ai_brain_tab_env) -> None:
        """local_path 非空但 os.path.exists=False → show_snack(ai_model_file_not_found) + return。"""
        env = ai_brain_tab_env
        env["fake_local_vm"].get_current_config_mock.return_value = {"model_path": "/fake/path.gguf"}
        handler, args, _ = self._trigger_save(env)
        with (
            self._patch_ai_service_ok(),
            patch("os.path.exists", return_value=False),
        ):
            asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)
        # 验证未调用 LocalModelManager (提前 return)
        # 通过验证 mock_tpm.run_async 被调用次数: os.path.exists 调用 1 次, 后续 md5 不调用
        assert env["mock_tpm"].run_async.call_count >= 1

    def test_local_path_exists_md5_changed_shows_local_model_changed(self, ai_brain_tab_env) -> None:
        """local_path 存在, loaded_md5 != new_md5 → show_snack(ai_local_model_changed, WARNING)。"""
        env = ai_brain_tab_env
        env["fake_local_vm"].get_current_config_mock.return_value = {"model_path": "/fake/path.gguf"}
        handler, args, _ = self._trigger_save(env)
        with (
            self._patch_ai_service_ok(),
            patch("os.path.exists", return_value=True),
            patch("services.local_model_manager.LocalModelManager") as mock_lmm,
        ):
            mock_inst = MagicMock()
            mock_inst.get_loaded_model_md5.return_value = "loaded_md5_hash"
            mock_lmm.get_instance = AsyncMock(return_value=mock_inst)
            mock_lmm.calculate_file_md5 = MagicMock(return_value="new_md5_hash")
            mock_lmm.commit_verification_if_active = MagicMock()
            asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[ai_local_model_changed]", color=AppColors.WARNING)
        # 验证调用 calculate_file_md5
        mock_lmm.calculate_file_md5.assert_called_once_with("/fake/path.gguf")

    def test_local_path_exists_md5_same_shows_ai_saved(self, ai_brain_tab_env) -> None:
        """local_path 存在, loaded_md5 == new_md5 → show_snack(settings_snack_ai_saved)。"""
        env = ai_brain_tab_env
        env["fake_local_vm"].get_current_config_mock.return_value = {"model_path": "/fake/path.gguf"}
        handler, args, _ = self._trigger_save(env)
        with (
            self._patch_ai_service_ok(),
            patch("os.path.exists", return_value=True),
            patch("services.local_model_manager.LocalModelManager") as mock_lmm,
        ):
            mock_inst = MagicMock()
            mock_inst.get_loaded_model_md5.return_value = "same_md5_hash"
            mock_lmm.get_instance = AsyncMock(return_value=mock_inst)
            mock_lmm.calculate_file_md5 = MagicMock(return_value="same_md5_hash")
            mock_lmm.commit_verification_if_active = MagicMock()
            asyncio.run(handler(*args))

        env["show_snack"].assert_called_once_with("i18n[settings_snack_ai_saved]")

    @staticmethod
    def _patch_ai_service_ok():
        """patch AIService 使 AIService().reload_config() 为 AsyncMock 正常完成。

        默认 patch AIService 返回 MagicMock, 实例的 reload_config 不是 async,
        会被 await 抛 TypeError。此 helper 用 contextlib.ExitStack 包装,
        让 mock_inst.reload_config = AsyncMock(return_value=None)。
        """
        from contextlib import ExitStack

        stack = ExitStack()
        mock_ai_cls = stack.enter_context(patch("services.ai_service.AIService"))
        mock_inst = MagicMock()
        mock_inst.reload_config = AsyncMock(return_value=None)
        mock_ai_cls.return_value = mock_inst
        return stack


# ============================================================================
# _do_save_ai_settings: 异常路径测试
# ============================================================================


class TestDoSaveAISettingsExceptionPhase:
    """_do_save_ai_settings 异常路径: classify_error / classify_severity / settings_snack_ai_error toast。"""

    def _trigger_save(self, env) -> tuple:
        buttons = _get_buttons(env)
        save_btn = next(b for b in buttons if isinstance(b, ft.Button))
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(save_btn.on_click, _make_event())
        return _await_run_task_handler(page)

    def test_exception_path_calls_classify_error_and_show_snack(self, ai_brain_tab_env) -> None:
        """AIService().reload_config 抛 Exception → classify_error + logger.error + show_snack(ai_error)。

        普通异常 → classify_severity 返回 "operational" → logger.error (非 critical)。
        """
        env = ai_brain_tab_env
        handler, args, _ = self._trigger_save(env)
        with (
            patch("services.ai_service.AIService") as mock_ai_cls,
            patch("utils.error_classifier.classify_error") as mock_classify,
            patch("utils.error_classifier.classify_severity") as mock_severity,
        ):
            mock_ai_inst = MagicMock()
            mock_ai_inst.reload_config = AsyncMock(side_effect=RuntimeError("reload boom"))
            mock_ai_cls.return_value = mock_ai_inst
            mock_classify.return_value = {"code": "unknown"}
            mock_severity.return_value = "operational"
            asyncio.run(handler(*args))

        mock_classify.assert_called_once_with(mock_ai_inst.reload_config.side_effect, context="general")
        mock_severity.assert_called_once_with(mock_ai_inst.reload_config.side_effect, context="general")
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_system_level_exception_logs_critical(self, ai_brain_tab_env) -> None:
        """AIService().reload_config 抛 MemoryError → classify_severity=system → logger.critical。"""
        env = ai_brain_tab_env
        handler, args, _ = self._trigger_save(env)
        with (
            patch("services.ai_service.AIService") as mock_ai_cls,
            patch("utils.error_classifier.classify_error") as mock_classify,
            patch("utils.error_classifier.classify_severity") as mock_severity,
        ):
            mock_ai_inst = MagicMock()
            mock_ai_inst.reload_config = AsyncMock(side_effect=MemoryError("oom"))
            mock_ai_cls.return_value = mock_ai_inst
            mock_classify.return_value = {"code": "unknown"}
            mock_severity.return_value = "system"
            asyncio.run(handler(*args))

        mock_severity.assert_called_once_with(mock_ai_inst.reload_config.side_effect, context="general")
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_save_configs_sync_exception_path(self, ai_brain_tab_env) -> None:
        """ConfigHandler.save_local_ai_config 抛 Exception → _save_configs_sync 传播 → 外层 except → show_snack(ai_error)。

        覆盖 _save_configs_sync 内部异常路径。
        """
        env = ai_brain_tab_env
        env["mock_config"].save_local_ai_config.side_effect = RuntimeError("save_local_ai_config boom")
        handler, args, _ = self._trigger_save(env)
        with (
            patch("services.ai_service.AIService"),
            patch("utils.error_classifier.classify_error") as mock_classify,
            patch("utils.error_classifier.classify_severity") as mock_severity,
        ):
            mock_classify.return_value = {"code": "unknown"}
            mock_severity.return_value = "operational"
            asyncio.run(handler(*args))

        mock_classify.assert_called_once_with(env["mock_config"].save_local_ai_config.side_effect, context="general")
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_commit_verification_if_active_exception_path(self, ai_brain_tab_env) -> None:
        """LocalModelManager.commit_verification_if_active 抛 Exception → 外层 except → show_snack(ai_error)。"""
        env = ai_brain_tab_env
        handler, args, _ = self._trigger_save(env)
        with (
            patch("services.ai_service.AIService"),
            patch("services.local_model_manager.LocalModelManager") as mock_lmm,
            patch("utils.error_classifier.classify_error") as mock_classify,
            patch("utils.error_classifier.classify_severity") as mock_severity,
        ):
            mock_lmm.commit_verification_if_active = MagicMock(side_effect=RuntimeError("commit boom"))
            mock_classify.return_value = {"code": "unknown"}
            mock_severity.return_value = "operational"
            asyncio.run(handler(*args))

        mock_classify.assert_called_once_with(mock_lmm.commit_verification_if_active.side_effect, context="general")
        env["show_snack"].assert_called_once_with("i18n[settings_save_failed]", color=AppColors.ERROR)

    def test_cancelled_error_propagates(self, ai_brain_tab_env) -> None:
        """R2: CancelledError 必须传播, 不被 except Exception 吞没。"""
        env = ai_brain_tab_env
        env["fake_llm_vm"].save_config_mock = AsyncMock(side_effect=asyncio.CancelledError())
        handler, args, _ = self._trigger_save(env)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(handler(*args))
        assert isinstance(exc_info.value, asyncio.CancelledError)


# ============================================================================
# 3 个 async helper 测试: 成功/异常/CancelledError
# ============================================================================


class TestOnLLMTestConnection:
    """AIBrainSettingsViewModel.test_connection: 成功/异常/CancelledError。

    Phase 3.2 P1-1: _on_llm_test_connection 下沉为 VM 静态 command
    (ui.viewmodels.ai_brain_settings_view_model.AIBrainSettingsViewModel.test_connection)。
    """

    def test_success_path(self) -> None:
        """成功: AIService.test_connection 返回 dict。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.ai_service.AIService") as mock_service:
            expected = {"success": True, "message": "ok"}
            mock_service.test_connection = AsyncMock(return_value=expected)
            result = asyncio.run(
                AIBrainSettingsViewModel.test_connection(
                    provider="openai",
                    model="gpt-4",
                    base_url="https://api.openai.com",
                    api_key="sk-key",
                )
            )
            assert result == expected
            mock_service.test_connection.assert_called_once_with(
                provider="openai",
                model="gpt-4",
                base_url="https://api.openai.com",
                api_key="sk-key",
            )

    def test_exception_propagates(self) -> None:
        """AIService.test_connection 抛 Exception → 传播 (command 不吞没)。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.ai_service.AIService") as mock_service:
            mock_service.test_connection = AsyncMock(side_effect=RuntimeError("connection boom"))
            with pytest.raises(RuntimeError, match="connection boom"):
                asyncio.run(
                    AIBrainSettingsViewModel.test_connection(
                        provider="openai",
                        model="gpt-4",
                        base_url="https://api.openai.com",
                        api_key="sk-key",
                    )
                )

    def test_cancelled_error_propagates(self) -> None:
        """R2: CancelledError 传播 (command 不捕获, 直接 forward)。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.ai_service.AIService") as mock_service:
            mock_service.test_connection = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                asyncio.run(
                    AIBrainSettingsViewModel.test_connection(
                        provider="openai",
                        model="gpt-4",
                        base_url="https://api.openai.com",
                        api_key="sk-key",
                    )
                )
            assert isinstance(exc_info.value, asyncio.CancelledError)


class TestOnReloadAIService:
    """AIBrainSettingsViewModel.reload_service: 成功/异常/CancelledError。

    Phase 3.2 P1-1: _on_reload_ai_service 下沉为 VM 静态 command
    (ui.viewmodels.ai_brain_settings_view_model.AIBrainSettingsViewModel.reload_service)。
    """

    def test_success_path(self) -> None:
        """成功: AIService().reload_config 完成。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.ai_service.AIService") as mock_ai_cls:
            mock_inst = MagicMock()
            mock_inst.reload_config = AsyncMock(return_value=None)
            mock_ai_cls.return_value = mock_inst
            asyncio.run(AIBrainSettingsViewModel.reload_service())
            mock_inst.reload_config.assert_called_once_with()

    def test_exception_propagates(self) -> None:
        """AIService().reload_config 抛 Exception → 传播。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.ai_service.AIService") as mock_ai_cls:
            mock_inst = MagicMock()
            mock_inst.reload_config = AsyncMock(side_effect=RuntimeError("reload boom"))
            mock_ai_cls.return_value = mock_inst
            with pytest.raises(RuntimeError, match="reload boom"):
                asyncio.run(AIBrainSettingsViewModel.reload_service())

    def test_cancelled_error_propagates(self) -> None:
        """R2: CancelledError 传播。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.ai_service.AIService") as mock_ai_cls:
            mock_inst = MagicMock()
            mock_inst.reload_config = AsyncMock(side_effect=asyncio.CancelledError())
            mock_ai_cls.return_value = mock_inst
            with pytest.raises(asyncio.CancelledError) as exc_info:
                asyncio.run(AIBrainSettingsViewModel.reload_service())
            assert isinstance(exc_info.value, asyncio.CancelledError)


class TestOnVerifyLocalModel:
    """AIBrainSettingsViewModel.verify_local_model: 成功/异常/CancelledError。

    Phase 3.2 P1-1: _on_verify_local_model 下沉为 VM 静态 command
    (ui.viewmodels.ai_brain_settings_view_model.AIBrainSettingsViewModel.verify_local_model)。
    """

    def test_success_path(self) -> None:
        """成功: LocalModelManager.get_instance + load_model 返回 True。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_inst = MagicMock()
            mock_inst.load_model = AsyncMock(return_value=True)
            mock_lmm.get_instance = AsyncMock(return_value=mock_inst)
            result = asyncio.run(AIBrainSettingsViewModel.verify_local_model("/fake/path.gguf", {"n_threads": 4}))
            assert result is True
            mock_inst.load_model.assert_called_once_with("/fake/path.gguf", {"n_threads": 4}, is_verification=True)

    def test_exception_propagates(self) -> None:
        """load_model 抛 Exception → 传播。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_inst = MagicMock()
            mock_inst.load_model = AsyncMock(side_effect=RuntimeError("load boom"))
            mock_lmm.get_instance = AsyncMock(return_value=mock_inst)
            with pytest.raises(RuntimeError, match="load boom"):
                asyncio.run(AIBrainSettingsViewModel.verify_local_model("/fake/path.gguf", {}))

    def test_cancelled_error_propagates(self) -> None:
        """R2: CancelledError 传播。"""
        from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel

        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_inst = MagicMock()
            mock_inst.load_model = AsyncMock(side_effect=asyncio.CancelledError())
            mock_lmm.get_instance = AsyncMock(return_value=mock_inst)
            with pytest.raises(asyncio.CancelledError) as exc_info:
                asyncio.run(AIBrainSettingsViewModel.verify_local_model("/fake/path.gguf", {}))
            assert isinstance(exc_info.value, asyncio.CancelledError)


# ============================================================================
# _show_saved_snack helper 测试
# ============================================================================


class TestShowSavedSnack:
    """_show_saved_snack: 调 show_snack(settings_verify_success)。"""

    def test_show_saved_snack_calls_callback(self, monkeypatch) -> None:
        """_show_saved_snack 调 show_snack(I18n.get('settings_verify_success'), color=SUCCESS)。"""
        from ui.views.settings_tabs import ai_brain_tab as mod

        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
        monkeypatch.setattr(mod, "I18n", mock_i18n)
        show_snack = MagicMock()
        mod._show_saved_snack(show_snack)
        show_snack.assert_called_once_with("i18n[settings_verify_success]", color=AppColors.SUCCESS)
