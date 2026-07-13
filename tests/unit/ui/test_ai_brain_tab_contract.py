"""ui/views/settings_tabs/ai_brain_tab.py 声明式契约守护测试 (Phase E.1).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/PageRefMixin/
   _page_ref/local_model_vm/handle_resize/refresh_locale/_on_locale_change)
2. 三阶段保存状态机存在性 (_SAVE_IDLE/_SAVE_SAVING/_SAVE_SUCCESS/_SAVE_ERROR)
3. 模块级纯函数测试 (_get_page/_validate_prompt_or_warn/_show_saved_snack)

业务逻辑覆盖（ConfigHandler 读写 + 异步保存路径 + VM 协作 + 子面板消费）由集成测试
（flet_test_page fixture）承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名 (作为变更说明) 导致字符串匹配误判。
    """
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.views.settings_tabs.ai_brain_tab as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.settings_tabs.ai_brain_tab as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (AIBrainTab)
# ============================================================================


class TestAIBrainTabContract:
    """AIBrainTab 声明式契约守护测试 (Phase E.1)。"""

    def test_ai_brain_tab_is_ft_component(self):
        """DoD: AIBrainTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        assert hasattr(AIBrainTab, "__wrapped__"), "AIBrainTab 必须用 @ft.component 装饰"

    def test_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "AIBrainTab 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class AIBrainTab(" not in _code_source(), "AIBrainTab 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def AIBrainTab(...) -> ft.Container。"""
        assert "def AIBrainTab(" in _code_source(), "必须是函数定义"
        assert "-> ft.Container" in _code_source(), "返回类型必须为 ft.Container"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        assert "_on_locale_change" not in _code_source(), "不应使用 _on_locale_change (声明式自动重渲染)"

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme (声明式通过 Observable state 自动重渲染)。"""
        assert "update_theme" not in _code_source(), "不应使用 update_theme (声明式自动重渲染)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联 (子组件自管)。"""
        assert "handle_resize" not in _code_source(), "不应使用 handle_resize (命令式)"

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_local_model_vm_attr(self):
        """DoD: 禁止 local_model_vm 命令式 VM 持有 (声明式用 use_viewmodel factory)。"""
        assert "local_model_vm" not in _code_source(), "不应使用 local_model_vm (命令式 VM 持有)"

    def test_no_use_ref_cache(self):
        """DoD: 禁止 use_ref cache 命令式实例。"""
        assert "ft.use_ref" not in _code_source(), "不应直接使用 ft.use_ref"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 get_observable_state (i18n 自动重渲染)。"""
        assert "get_observable_state" in _raw_source(), "必须订阅 get_observable_state"

    def test_subscribes_theme(self):
        """DoD: 必须订阅 AppColors.get_observable_state (theme 自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source(), "必须订阅 AppColors.get_observable_state"

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page (try/except 守卫)。"""
        assert "ft.context.page" in _code_source(), "page 访问必须通过 ft.context.page"

    def test_uses_use_viewmodel(self):
        """DoD: 必须通过 use_viewmodel hook 消费子 VM。"""
        assert "use_viewmodel" in _raw_source(), "必须使用 use_viewmodel hook"

    def test_consumes_llm_config_panel(self):
        """DoD: 必须函数调用消费 LLMConfigPanel (vm props 推送)。"""
        assert "LLMConfigPanel(" in _code_source(), "必须函数调用 LLMConfigPanel(vm=llm_vm)"

    def test_consumes_failover_config_panel(self):
        """DoD: 必须函数调用消费 FailoverConfigPanel (vm props 推送)。"""
        assert "FailoverConfigPanel(" in _code_source(), "必须函数调用 FailoverConfigPanel(vm=failover_vm)"

    def test_consumes_local_model_config_panel(self):
        """DoD: 必须函数调用消费 LocalModelConfigPanel (vm props 推送)。"""
        assert "LocalModelConfigPanel(" in _code_source(), "必须函数调用 LocalModelConfigPanel(vm=local_vm)"

    def test_no_page_ref_param(self):
        """DoD: AIBrainTab 签名不应包含 page_ref 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        sig = inspect.signature(AIBrainTab.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page_ref" not in params, "AIBrainTab 不应接收 page_ref 参数"
        assert "show_snack_callback" in params, "AIBrainTab 必须接收 show_snack_callback"


# ============================================================================
# R2 CancelledError 传播契约 (CLAUDE.md §3 红线 R2)
# ============================================================================


class TestR2CancelledErrorPropagation:
    """R2 红线: asyncio.CancelledError 必须显式 raise, 不被 except Exception 吞没。"""

    def test_has_cancelled_error_guard(self):
        """DoD: 含 await 的 async handler 必须有 except asyncio.CancelledError: raise。"""
        code = _raw_source()
        assert "except asyncio.CancelledError:" in code, "必须有 CancelledError 捕获"
        assert "raise  # R2" in code, "CancelledError 必须 raise (R2)"

    def test_no_bare_exception_swallows_cancelled_error(self):
        """DoD: except Exception 前必须有 except asyncio.CancelledError 守卫。"""
        code = _raw_source()
        except_exception_count = code.count("except Exception")
        cancelled_guard_count = code.count("except asyncio.CancelledError")
        assert cancelled_guard_count >= except_exception_count, (
            f"R2 违规: {except_exception_count} 处 except Exception 但仅 {cancelled_guard_count} 处 CancelledError 守卫"
        )


# ============================================================================
# 三阶段保存状态机契约
# ============================================================================


class TestSaveStateMachineContract:
    """三阶段保存状态机 (idle/saving/success/error) 契约守护测试 (Phase E.1)。"""

    def test_has_save_idle_constant(self):
        """DoD: 必须定义 _SAVE_IDLE 状态常量。"""
        assert "_SAVE_IDLE" in _code_source(), "必须定义 _SAVE_IDLE 状态常量"

    def test_has_save_saving_constant(self):
        """DoD: 必须定义 _SAVE_SAVING 状态常量。"""
        assert "_SAVE_SAVING" in _code_source(), "必须定义 _SAVE_SAVING 状态常量"

    def test_has_save_success_constant(self):
        """DoD: 必须定义 _SAVE_SUCCESS 状态常量。"""
        assert "_SAVE_SUCCESS" in _code_source(), "必须定义 _SAVE_SUCCESS 状态常量"

    def test_has_save_error_constant(self):
        """DoD: 必须定义 _SAVE_ERROR 状态常量。"""
        assert "_SAVE_ERROR" in _code_source(), "必须定义 _SAVE_ERROR 状态常量"

    def test_save_state_is_use_state_driven(self):
        """DoD: save_state 必须通过 ft.use_state 持久化 (state 驱动渲染)。"""
        assert "ft.use_state(_SAVE_IDLE)" in _code_source(), "save_state 必须用 ft.use_state(_SAVE_IDLE) 初始化"

    def test_save_state_drives_button_disabled(self):
        """DoD: 按钮的 disabled 必须由 save_state 派生 (状态驱动渲染)。"""
        assert "is_saving" in _code_source(), "必须定义 is_saving 派生状态"
        assert "disabled=is_saving" in _code_source(), "按钮 disabled 必须由 is_saving 派生"

    def test_save_state_drives_progress_ring(self):
        """DoD: ProgressRing 的 visible 必须由 save_state 派生 (状态驱动渲染)。"""
        assert "visible=is_saving" in _code_source(), "ProgressRing visible 必须由 is_saving 派生"

    def test_save_flow_sets_saving_state(self):
        """DoD: 保存流程开始时必须 set_save_state(_SAVE_SAVING)。"""
        assert "set_save_state(_SAVE_SAVING)" in _code_source(), "保存流程必须先 set _SAVE_SAVING"

    def test_save_flow_sets_success_state(self):
        """DoD: 保存成功时必须 set_save_state(_SAVE_SUCCESS)。"""
        assert "set_save_state(_SAVE_SUCCESS)" in _code_source(), "保存成功必须 set _SAVE_SUCCESS"

    def test_save_flow_sets_error_state(self):
        """DoD: 保存失败/异常时必须 set_save_state(_SAVE_ERROR)。"""
        assert "set_save_state(_SAVE_ERROR)" in _code_source(), "保存失败必须 set _SAVE_ERROR"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """ft.context.page 可用时返回 page 实例。"""
        from ui.views.settings_tabs.ai_brain_tab import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.views.settings_tabs.ai_brain_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.views.settings_tabs.ai_brain_tab import _get_page

        with patch("ui.views.settings_tabs.ai_brain_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None


class TestValidatePromptOrWarn:
    """_validate_prompt_or_warn 模块级纯函数测试 (Prompt 安全验证)。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.ai_brain_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.ai_brain_tab.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_true_when_prompt_valid(self):
        """Prompt 验证通过时返回 True, 不调用 show_snack。"""
        from ui.views.settings_tabs.ai_brain_tab import _validate_prompt_or_warn

        show_snack = MagicMock()
        with patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)):
            result = _validate_prompt_or_warn("valid prompt", show_snack)

        assert result is True
        show_snack.assert_not_called()

    def test_returns_false_and_warns_when_injection_detected(self):
        """Prompt 含注入时返回 False, 调用 show_snack 显示警告。"""
        from ui.views.settings_tabs.ai_brain_tab import _validate_prompt_or_warn

        show_snack = MagicMock()
        with patch(
            "ui.views.settings_tabs.ai_brain_tab.validate_prompt",
            return_value=(False, "prompt_err_injection"),
        ):
            result = _validate_prompt_or_warn("bad prompt", show_snack)

        assert result is False
        show_snack.assert_called_once()
        call_kwargs = show_snack.call_args
        assert call_kwargs.kwargs.get("color") == self.mock_ac.WARNING

    def test_length_error_uses_formatted_message(self):
        """长度错误时使用 I18n.get('prompt_err_length').format(max=...) 格式化消息。"""
        from ui.views.settings_tabs.ai_brain_tab import _validate_prompt_or_warn

        show_snack = MagicMock()

        # 模拟真实 i18n 行为: prompt_err_length 返回含 {max} 占位符的模板
        def _mock_get(key, *a, **kw):
            if key == "prompt_err_length":
                return "err length max={max}"
            return f"i18n[{key}]"

        self.mock_i18n.get.side_effect = _mock_get
        with patch(
            "ui.views.settings_tabs.ai_brain_tab.validate_prompt",
            return_value=(False, "prompt_err_length"),
        ):
            with patch("ui.views.settings_tabs.ai_brain_tab.MAX_PROMPT_LENGTH", 8000):
                result = _validate_prompt_or_warn("x" * 9000, show_snack)

        assert result is False
        show_snack.assert_called_once()
        msg = show_snack.call_args.args[0]
        assert "err length max=8000" in msg


class TestShowSavedSnack:
    """_show_saved_snack 模块级纯函数测试 (配置保存成功 snack)。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
        self.patches = [
            patch("ui.views.settings_tabs.ai_brain_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.ai_brain_tab.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_calls_show_snack_with_success_color(self):
        """调用 show_snack 传入 settings_verify_success 文案 + SUCCESS 颜色。"""
        from ui.views.settings_tabs.ai_brain_tab import _show_saved_snack

        show_snack = MagicMock()
        _show_saved_snack(show_snack)

        show_snack.assert_called_once_with(
            "i18n[settings_verify_success]",
            color=self.mock_ac.SUCCESS,
        )
