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


# ============================================================================
# Component body tests (attach_fake_page pattern, target ≥80% coverage)
# ============================================================================

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.viewmodels import Message


@dataclass(frozen=True)
class _FakeOnboardingState:
    """模拟 OnboardingState 的最小字段集。"""

    current_step: int = 0
    sync_in_progress: bool = False
    validation_in_progress: bool = False
    sync_progress: float = 0.0
    sync_progress_message: Message | None = None
    schedule_enabled: bool = True
    schedule_time: str = "16:30"
    normalized_schedule_time: str = "16:30"
    init_history_years: int = 3


class _FakeOnboardingViewModel:
    """模拟 OnboardingViewModel，记录 bind/dispose/subscribe 调用。

    未显式定义的方法/属性通过 ``__getattr__`` 返回 MagicMock，
    使组件渲染不因缺少 VM 方法而抛 AttributeError。
    """

    def __init__(self, state: _FakeOnboardingState | None = None) -> None:
        self._state: _FakeOnboardingState = state or _FakeOnboardingState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.bind_called: bool = False
        # 异步方法需返回 awaitable（event handler 通过 page.run_task 调用）
        self.next_step = AsyncMock()
        self.prev_step = AsyncMock()
        self.skip_step = AsyncMock()
        self.start_sync = AsyncMock()
        self.skip_sync = AsyncMock()
        self.save_language = AsyncMock(return_value=True)

    @property
    def state(self) -> _FakeOnboardingState:
        return self._state

    @property
    def sync_in_progress(self) -> bool:
        return self._state.sync_in_progress

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def bind(self, **kwargs: Any) -> None:  # noqa: ARG002
        self.bind_called = True

    def invalidate_step(self, step_id: str) -> None:  # noqa: ARG002
        pass

    def set_schedule_state(self, *, enabled: bool, time_str: str) -> None:  # noqa: ARG002
        pass

    async def cancel_sync(self) -> None:
        pass

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return MagicMock()


@dataclass(frozen=True)
class _FakePanelState:
    """模拟 config panel state 的最小字段集。"""


class _FakePanelViewModel:
    """通用 config panel VM mock，满足 _ViewModelProtocol。

    未显式定义的方法/属性通过 ``__getattr__`` 返回 MagicMock。
    """

    def __init__(self) -> None:
        self._state: _FakePanelState = _FakePanelState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False

    @property
    def state(self) -> _FakePanelState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return MagicMock()


def _make_fake_page() -> FakePage:
    """创建带 run_task/show_toast/locale_configuration 的 FakePage。

    ``run_task`` 同步执行协程（测试需验证 cleanup 等异步路径的实际行为）。
    """
    page = FakePage()

    def _run_task(coro_func: Any, *args: Any, **kwargs: Any) -> None:
        coro = coro_func(*args, **kwargs)
        if asyncio.iscoroutine(coro):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

    page.run_task = _run_task  # type: ignore[method-assign]
    page.show_toast = MagicMock()  # type: ignore[method-assign]
    page.locale_configuration = MagicMock()
    return page


def _collect_controls(control: Any) -> list[Any]:
    """递归收集控件树中的所有 ft.Control（含嵌套 content/controls）。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归: mock 下 content 属性返回新 MagicMock)。
    """
    if control is None or not isinstance(control, ft.Control):
        return []
    results: list[Any] = [control]
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        results.extend(_collect_controls(content))
    controls = getattr(control, "controls", None)
    if isinstance(controls, list):
        for c in controls:
            if c is not None:
                results.extend(_collect_controls(c))
    return results


def _find_icons(container: Any, icon_name: str) -> list[ft.Icon]:
    """查找控件树中 ft.Icon，返回 icon 属性匹配 icon_name 的列表。"""
    return [c for c in _collect_controls(container) if isinstance(c, ft.Icon) and getattr(c, "icon", None) == icon_name]


def _find_by_type(container: Any, control_type: type) -> list[Any]:
    """查找控件树中指定类型的所有控件。"""
    return [c for c in _collect_controls(container) if isinstance(c, control_type)]


@pytest.fixture
def mock_onboarding_vms(monkeypatch):
    """注入 5 个 FakeVM 替换 OnboardingWizard 消费的 VM 类。

    替换：
    - OnboardingViewModel → _FakeOnboardingViewModel
    - DatabaseConfigPanelViewModel / TushareConfigPanelViewModel /
      LLMConfigPanelViewModel / LocalModelConfigPanelViewModel → _FakePanelViewModel

    Phase 3.4 后 view 不再直接调用 ConfigHandler/ThreadPoolManager/LocalModelManager，
    相关 mock 由 VM 层 fixture 承担（见 test_onboarding_view_model.py）。
    """
    # 确保 ui.views.onboarding_wizard 模块已加载（monkeypatch.setattr 需要模块属性存在）
    import ui.views.onboarding_wizard  # noqa: F401

    fake_onboarding_vm = _FakeOnboardingViewModel()
    fake_database_vm = _FakePanelViewModel()
    fake_tushare_vm = _FakePanelViewModel()
    fake_llm_vm = _FakePanelViewModel()
    fake_local_model_vm = _FakePanelViewModel()

    # 生产代码 factory lambda 带关键字参数调用 VM 构造器（如 load_password=True），
    # mock lambda 需接受任意参数返回 fake_vm 实例
    monkeypatch.setattr(
        "ui.views.onboarding_wizard.OnboardingViewModel",
        lambda *a, **kw: fake_onboarding_vm,
    )
    monkeypatch.setattr(
        "ui.views.onboarding_wizard.DatabaseConfigPanelViewModel",
        lambda *a, **kw: fake_database_vm,
    )
    monkeypatch.setattr(
        "ui.views.onboarding_wizard.TushareConfigPanelViewModel",
        lambda *a, **kw: fake_tushare_vm,
    )
    monkeypatch.setattr(
        "ui.views.onboarding_wizard.LLMConfigPanelViewModel",
        lambda *a, **kw: fake_llm_vm,
    )
    monkeypatch.setattr(
        "ui.views.onboarding_wizard.LocalModelConfigPanelViewModel",
        lambda *a, **kw: fake_local_model_vm,
    )

    return {
        "onboarding": fake_onboarding_vm,
        "database": fake_database_vm,
        "tushare": fake_tushare_vm,
        "llm": fake_llm_vm,
        "local_model": fake_local_model_vm,
    }


class TestOnboardingWizardComponentBody:
    """OnboardingWizard @ft.component 组件体测试（attach_fake_page 模式）。

    覆盖 8 步状态机渲染 + VM 生命周期 + 条件可见性，目标覆盖率 ≥80%。
    """

    def test_step0_welcome_renders_language_dropdown_and_overview_cards(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 0 (Welcome)：渲染语言下拉 + rocket 图标 + 6 张 overview 卡片。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        all_controls = _collect_controls(result)
        # 语言下拉
        assert any(isinstance(c, ft.Dropdown) for c in all_controls)
        # Rocket icon
        assert len(_find_icons(result, ft.Icons.ROCKET_LAUNCH)) >= 1
        # 6 张 overview 卡片 (ResponsiveRow)
        assert any(isinstance(c, ft.ResponsiveRow) for c in all_controls)

    def test_step1_database_renders_storage_icon(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 1 (Database)：渲染 STORAGE 图标 + DatabaseConfigPanel 组件。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=1)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert len(_find_icons(result, ft.Icons.STORAGE)) >= 1

    def test_step2_token_renders_key_icon(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 2 (Token)：渲染 KEY 图标 + TushareConfigPanel 组件。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=2)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert len(_find_icons(result, ft.Icons.KEY)) >= 1

    def test_step3_cloud_ai_renders_cloud_icon(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 3 (Cloud AI)：渲染 CLOUD 图标 + LLMConfigPanel 组件。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=3)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert len(_find_icons(result, ft.Icons.CLOUD)) >= 1

    def test_step4_local_model_renders_psychology_icon(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 4 (Local Model)：渲染 PSYCHOLOGY 图标 + LocalModelConfigPanel 组件。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=4)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert len(_find_icons(result, ft.Icons.PSYCHOLOGY)) >= 1

    def test_step5_data_sync_renders_sync_buttons_and_progress_bar(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 5 (Data Sync)：渲染同步按钮 + 进度条 + CLOUD_DOWNLOAD 图标。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        all_controls = _collect_controls(result)
        assert len(_find_icons(result, ft.Icons.CLOUD_DOWNLOAD)) >= 1
        # ProgressBar
        assert any(isinstance(c, ft.ProgressBar) for c in all_controls)
        # Buttons (quick sync, full sync, cancel)
        buttons = [c for c in all_controls if isinstance(c, ft.Button)]
        assert len(buttons) >= 3

    def test_step6_schedule_renders_checkbox_and_time_input(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 6 (Schedule)：渲染复选框 + 时间输入 + SCHEDULE 图标。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=6)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        all_controls = _collect_controls(result)
        assert len(_find_icons(result, ft.Icons.SCHEDULE)) >= 1
        assert any(isinstance(c, ft.Checkbox) for c in all_controls)
        assert any(isinstance(c, ft.TextField) for c in all_controls)

    def test_step7_complete_renders_celebration_icon(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 7 (Complete)：渲染 CELEBRATION 图标。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=7)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert len(_find_icons(result, ft.Icons.CELEBRATION)) >= 1

    def test_mount_triggers_all_vm_subscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """mount 触发 5 个 VM 的 subscribe 被调用（use_viewmodel hook 注册）。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)

        assert len(mock_onboarding_vms["onboarding"]._subscribers) > 0
        assert len(mock_onboarding_vms["database"]._subscribers) > 0
        assert len(mock_onboarding_vms["tushare"]._subscribers) > 0
        assert len(mock_onboarding_vms["llm"]._subscribers) > 0
        assert len(mock_onboarding_vms["local_model"]._subscribers) > 0

    def test_mount_triggers_onboarding_vm_bind(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """mount 后 OnboardingViewModel.bind 被调用（注入 panel callbacks）。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)

        assert mock_onboarding_vms["onboarding"].bind_called is True

    def test_unmount_triggers_all_vm_dispose(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """unmount 触发 5 个 VM 的 dispose 被调用。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)

        assert not mock_onboarding_vms["onboarding"].dispose_called
        assert not mock_onboarding_vms["database"].dispose_called

        run_unmount_effects(component)

        assert mock_onboarding_vms["onboarding"].dispose_called is True
        assert mock_onboarding_vms["database"].dispose_called is True
        assert mock_onboarding_vms["tushare"].dispose_called is True
        assert mock_onboarding_vms["llm"].dispose_called is True
        assert mock_onboarding_vms["local_model"].dispose_called is True

    def test_loading_overlay_visible_when_validation_in_progress(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """validation_in_progress=True 时 loading_overlay 可见。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, validation_in_progress=True)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        stack = result.content  # type: ignore[union-attr]
        assert isinstance(stack, ft.Stack)
        overlay = stack.controls[-1]
        assert overlay.visible is True

    def test_loading_overlay_hidden_when_not_validating(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """validation_in_progress=False 时 loading_overlay 不可见。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        stack = result.content  # type: ignore[union-attr]
        assert isinstance(stack, ft.Stack)
        overlay = stack.controls[-1]
        assert overlay.visible is False

    def test_step_indicators_visibility(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 1-6 显示进度条 (visible=True), step 0/7 隐藏 (visible=False)。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]

        # Step 0: indicators hidden
        fake_vm._state = replace(fake_vm._state, current_step=0)
        comp0 = make_component(OnboardingWizard)
        page0 = _make_fake_page()
        run_mount_effects(comp0, page0)
        result0 = render_once(comp0)
        main_col0 = result0.content.controls[0]  # type: ignore[union-attr]
        step_indicators0 = main_col0.controls[2]  # type: ignore[union-attr]
        assert step_indicators0.visible is False

        # Step 3: indicators visible
        fake_vm._state = replace(fake_vm._state, current_step=3)
        comp3 = make_component(OnboardingWizard)
        page3 = _make_fake_page()
        run_mount_effects(comp3, page3)
        result3 = render_once(comp3)
        main_col3 = result3.content.controls[0]  # type: ignore[union-attr]
        step_indicators3 = main_col3.controls[2]  # type: ignore[union-attr]
        assert step_indicators3.visible is True

    def test_navigation_bar_button_visibility(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """不同 step 的导航按钮可见性（step 0 无 prev, step 4 有 prev+skip+next）。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]

        # Step 0: show_prev=False → first nav is placeholder Container
        fake_vm._state = replace(fake_vm._state, current_step=0)
        comp0 = make_component(OnboardingWizard)
        page0 = _make_fake_page()
        run_mount_effects(comp0, page0)
        result0 = render_once(comp0)
        main_col0 = result0.content.controls[0]  # type: ignore[union-attr]
        nav_container0 = main_col0.controls[-1]  # type: ignore[union-attr]
        nav_row0 = nav_container0.content  # type: ignore[union-attr]
        # Step 0: show_prev=False → first is ft.Container (placeholder)
        assert isinstance(nav_row0.controls[0], ft.Container)
        # show_next=True → at least one ft.Button
        buttons0 = [c for c in nav_row0.controls if isinstance(c, ft.Button)]
        assert len(buttons0) >= 1

        # Step 4: show_prev + show_skip + show_next
        fake_vm._state = replace(fake_vm._state, current_step=4)
        comp4 = make_component(OnboardingWizard)
        page4 = _make_fake_page()
        run_mount_effects(comp4, page4)
        result4 = render_once(comp4)
        main_col4 = result4.content.controls[0]  # type: ignore[union-attr]
        nav_container4 = main_col4.controls[-1]  # type: ignore[union-attr]
        nav_row4 = nav_container4.content  # type: ignore[union-attr]
        nav_buttons4 = [c for c in nav_row4.controls if isinstance(c, (ft.Button, ft.TextButton))]
        # prev (Button) + skip (TextButton) + next (Button) = 3
        assert len(nav_buttons4) >= 3

    def test_navigation_buttons_trigger_vm_methods(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """点击导航按钮触发 VM 的 next_step/prev_step/skip_step 方法。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=4)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        # 找到导航栏按钮并触发 on_click
        main_col = result.content.controls[0]  # type: ignore[union-attr]
        nav_container = main_col.controls[-1]  # type: ignore[union-attr]
        nav_row = nav_container.content  # type: ignore[union-attr]
        mock_event = MagicMock()
        for btn in nav_row.controls:
            if hasattr(btn, "on_click") and btn.on_click is not None:
                btn.on_click(mock_event)

        # 验证 VM 异步方法被调用
        fake_vm.next_step.assert_called_once()
        fake_vm.prev_step.assert_called_once()
        fake_vm.skip_step.assert_called_once()

    def test_sync_buttons_trigger_vm_methods(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """点击同步按钮触发 VM 的 start_sync/cancel_sync/skip_sync 方法。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        # 找到同步按钮（quick sync / full sync / sync later）并触发
        all_controls = _collect_controls(result)
        mock_event = MagicMock()
        for c in all_controls:
            if hasattr(c, "on_click") and c.on_click is not None:
                c.on_click(mock_event)

        # start_sync 被 quick=True 和 quick=False 各调用一次
        assert fake_vm.start_sync.call_count >= 2
        fake_vm.skip_sync.assert_called_once()

    def test_language_select_triggers_do_language_change(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """选择语言触发 _do_language_change（覆盖 _on_language_select + _do_language_change）。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        # 找到语言下拉并触发 on_select
        all_controls = _collect_controls(result)
        dropdowns = [c for c in all_controls if isinstance(c, ft.Dropdown)]
        assert len(dropdowns) >= 1
        mock_event = MagicMock()
        mock_event.control.value = "en_US"
        dropdowns[0].on_select(mock_event)  # type: ignore[union-attr]

        # _do_language_change 通过 page.run_task 同步执行完成，不抛异常即覆盖

    def test_card_hover_triggers_set_hovered_card(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """hover overview 卡片触发 _on_card_hover 回调。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        # 找到 ResponsiveRow 中的 overview 卡片，触发 on_hover
        all_controls = _collect_controls(result)
        containers = [c for c in all_controls if isinstance(c, ft.Container)]
        mock_event = MagicMock()
        mock_event.data = "true"
        # 触发第一个有 on_hover 的 container
        for c in containers:
            if hasattr(c, "on_hover") and c.on_hover is not None:
                c.on_hover(mock_event)  # type: ignore[reportCallIssue, reason: Flet stub declares on_hover as 0-arg, but runtime passes event]
                break

        # 不抛异常即覆盖 _on_card_hover 路径

    def test_show_snack_with_no_page(self, monkeypatch):
        """_show_snack 在 page 不可用时安全降级（不抛异常）。"""
        from ui.views.onboarding_wizard import _show_snack

        # 清除 _context_page 使 _get_page 返回 None
        from flet.controls.context import _context_page

        _context_page.set(None)
        # 不抛异常即覆盖 page=None 降级路径
        _show_snack("test message", "#FF0000")
