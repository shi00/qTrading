"""ui/views/onboarding_wizard.py 声明式契约守护测试 (Phase F.1).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/
   _on_locale_change/refresh_locale/_rebuild_steps_after_locale_change/
   handle_resize/PageRefMixin/_page_ref/_bind_vm)
2. 模块级纯函数测试 (_get_page/_render_message/_validate_cloud_ai/_validate_local_model)

业务逻辑覆盖（8 步状态机 + config panel 消费 + 同步流程）由集成测试
（flet_test_page fixture）承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
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
    import ui.views.onboarding_wizard as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.onboarding_wizard as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (OnboardingWizard)
# ============================================================================


class TestOnboardingWizardContract:
    """OnboardingWizard 声明式契约守护测试 (Phase F.1)。"""

    def test_onboarding_wizard_is_ft_component(self):
        """DoD: OnboardingWizard 必须被 @ft.component 装饰。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "__wrapped__"), "OnboardingWizard 必须用 @ft.component 装饰"

    def test_onboarding_wizard_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "OnboardingWizard 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class OnboardingWizard(" not in _code_source(), "OnboardingWizard 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def OnboardingWizard(...) -> ft.Container。"""
        assert "def OnboardingWizard(" in _code_source(), "必须是函数定义"
        assert "-> ft.Container" in _code_source(), "返回类型必须为 ft.Container"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        assert "_on_locale_change" not in _code_source(), "不应使用 _on_locale_change (声明式自动重渲染)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_rebuild_steps_after_locale_change(self):
        """DoD: 禁止命令式 _rebuild_steps_after_locale_change (声明式自动重渲染)。"""
        assert "_rebuild_steps_after_locale_change" not in _code_source(), (
            "不应使用 _rebuild_steps_after_locale_change (声明式自动重渲染)"
        )

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联 (子组件自管)。"""
        assert "handle_resize" not in _code_source(), "不应使用 handle_resize (命令式)"

    def test_no_self_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_bind_vm(self):
        """DoD: 禁止命令式 _bind_vm (改用 use_viewmodel hook + 内联 bind)。"""
        assert "_bind_vm" not in _code_source(), "不应使用 _bind_vm (声明式用 use_viewmodel)"

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
        """DoD: 必须通过 use_viewmodel hook 消费 OnboardingViewModel。"""
        assert "use_viewmodel" in _raw_source(), "必须使用 use_viewmodel hook"
        assert "OnboardingViewModel" in _raw_source(), "必须消费 OnboardingViewModel"

    def test_consumes_database_config_panel(self):
        """DoD: 必须函数调用消费 DatabaseConfigPanel (props 推送)。"""
        assert "DatabaseConfigPanel(" in _code_source(), "必须函数调用 DatabaseConfigPanel(vm=...)"

    def test_consumes_tushare_config_panel(self):
        """DoD: 必须函数调用消费 TushareConfigPanel (props 推送)。"""
        assert "TushareConfigPanel(" in _code_source(), "必须函数调用 TushareConfigPanel(vm=...)"

    def test_consumes_llm_config_panel(self):
        """DoD: 必须函数调用消费 LLMConfigPanel (props 推送)。"""
        assert "LLMConfigPanel(" in _code_source(), "必须函数调用 LLMConfigPanel(vm=...)"

    def test_consumes_local_model_config_panel(self):
        """DoD: 必须函数调用消费 LocalModelConfigPanel (props 推送)。"""
        assert "LocalModelConfigPanel(" in _code_source(), "必须函数调用 LocalModelConfigPanel(vm=...)"

    def test_no_page_param_in_signature(self):
        """DoD: OnboardingWizard 签名不应包含 page 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.views.onboarding_wizard import OnboardingWizard

        sig = inspect.signature(OnboardingWizard.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page" not in params, "OnboardingWizard 不应接收 page 参数"
        assert "on_complete" in params, "OnboardingWizard 必须接收 on_complete"

    def test_uses_step_configs(self):
        """DoD: 必须使用 STEP_CONFIGS 驱动导航按钮。"""
        assert "STEP_CONFIGS" in _raw_source(), "必须使用 STEP_CONFIGS 驱动 8 步状态机"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """ft.context.page 可用时返回 page 实例。"""
        from ui.views.onboarding_wizard import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.views.onboarding_wizard.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.views.onboarding_wizard import _get_page

        with patch("ui.views.onboarding_wizard.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None


class TestRenderMessage:
    """_render_message 模块级纯函数测试。"""

    def test_returns_empty_string_for_none(self):
        """None 消息返回空字符串。"""
        from ui.views.onboarding_wizard import _render_message

        assert _render_message(None) == ""

    def test_returns_translated_text_for_message(self):
        """Message 对象返回 I18n.get 翻译文本。"""
        from ui.viewmodels import Message
        from ui.views.onboarding_wizard import _render_message

        msg = Message("wizard_status_ready")
        with patch("ui.views.onboarding_wizard.I18n") as mock_i18n:
            mock_i18n.get.return_value = "Ready"
            assert _render_message(msg) == "Ready"


class TestValidateCloudAi:
    """_validate_cloud_ai 模块级纯函数测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.patches = [
            patch("ui.views.onboarding_wizard._show_snack"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    @pytest.mark.asyncio
    async def test_returns_true_when_connection_and_save_succeed(self):
        """连接测试 + 保存均成功时返回 True。"""
        from ui.views.onboarding_wizard import _validate_cloud_ai

        llm_vm = MagicMock()
        llm_vm.verify_connection = AsyncMock(return_value=True)
        llm_vm.save_config = AsyncMock(return_value=True)

        result = await _validate_cloud_ai(llm_vm)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_connection_fails(self):
        """连接测试失败时返回 False。"""
        from ui.views.onboarding_wizard import _validate_cloud_ai

        llm_vm = MagicMock()
        llm_vm.verify_connection = AsyncMock(return_value=False)
        llm_vm.save_config = AsyncMock(return_value=True)

        result = await _validate_cloud_ai(llm_vm)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_save_fails(self):
        """连接成功但保存失败时返回 False。"""
        from ui.views.onboarding_wizard import _validate_cloud_ai

        llm_vm = MagicMock()
        llm_vm.verify_connection = AsyncMock(return_value=True)
        llm_vm.save_config = AsyncMock(return_value=False)

        result = await _validate_cloud_ai(llm_vm)
        assert result is False


class TestValidateLocalModel:
    """_validate_local_model 模块级纯函数测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.patches = [
            patch("ui.views.onboarding_wizard._show_snack"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    @pytest.mark.asyncio
    async def test_returns_true_when_model_path_empty(self):
        """空路径时跳过验证返回 True。"""
        from ui.views.onboarding_wizard import _validate_local_model

        local_model_vm = MagicMock()
        local_model_vm.state.model_path = "  "

        result = await _validate_local_model(local_model_vm)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_verify_and_save_succeed(self):
        """模型验证 + 保存均成功时返回 True。"""
        from ui.views.onboarding_wizard import _validate_local_model

        local_model_vm = MagicMock()
        local_model_vm.state.model_path = "/path/to/model"
        local_model_vm.verify_model = AsyncMock(return_value=True)
        local_model_vm.save_config = AsyncMock(return_value=True)

        result = await _validate_local_model(local_model_vm)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_verify_fails(self):
        """模型验证失败时返回 False。"""
        from ui.views.onboarding_wizard import _validate_local_model

        local_model_vm = MagicMock()
        local_model_vm.state.model_path = "/path/to/model"
        local_model_vm.verify_model = AsyncMock(return_value=False)
        local_model_vm.save_config = AsyncMock(return_value=True)

        result = await _validate_local_model(local_model_vm)
        assert result is False


class TestDefaultOnComplete:
    """_default_on_complete 模块级纯函数测试。"""

    @pytest.mark.asyncio
    async def test_is_noop(self):
        """默认完成回调为 no-op。"""
        from ui.views.onboarding_wizard import _default_on_complete

        await _default_on_complete()  # 不抛异常即通过


class TestCreateOverviewCard:
    """_create_overview_card 模块级纯函数测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"translated_{key}"
        self.patches = [
            patch("ui.views.onboarding_wizard.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_container(self):
        """返回 ft.Container 实例。"""
        from ui.views.onboarding_wizard import _create_overview_card

        card = _create_overview_card(
            icon=ft.Icons.STORAGE,
            color="#000000",
            title_key="wizard_overview_db_title",
            desc_key="wizard_overview_db_desc",
            required=True,
            is_hovered=False,
            on_hover=lambda e: None,
        )
        assert isinstance(card, ft.Container)

    def test_hovered_card_has_thicker_border(self):
        """hovered 状态下 border 宽度为 1.5。"""
        from ui.views.onboarding_wizard import _create_overview_card

        card_hovered = _create_overview_card(
            icon=ft.Icons.STORAGE,
            color="#000000",
            title_key="test",
            desc_key="test",
            required=True,
            is_hovered=True,
            on_hover=lambda e: None,
        )
        card_normal = _create_overview_card(
            icon=ft.Icons.STORAGE,
            color="#000000",
            title_key="test",
            desc_key="test",
            required=True,
            is_hovered=False,
            on_hover=lambda e: None,
        )
        hovered_content = card_hovered.content  # type: ignore[union-attr]
        normal_content = card_normal.content  # type: ignore[union-attr]
        assert hovered_content.border.top.width == 1.5  # type: ignore[union-attr]
        assert normal_content.border.top.width == 1  # type: ignore[union-attr]
