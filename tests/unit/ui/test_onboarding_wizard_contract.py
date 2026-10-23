"""ui/views/onboarding_wizard.py 声明式契约守护测试 (Phase F.1).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/
   _on_locale_change/refresh_locale/_rebuild_steps_after_locale_change/
   handle_resize/PageRefMixin/_page_ref/_bind_vm)
2. 模块级纯函数测试 (_get_page/_render_message/_commit_model_stage)

业务逻辑覆盖（7 步状态机 + config panel 消费 + 同步流程）由集成测试
（flet_test_page fixture）承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import ast
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

    def test_consumes_model_connection_form(self):
        """DoD: 模型阶段必须函数调用消费共用 ModelConnectionForm (S5 §11.6)。"""
        assert "ModelConnectionForm(" in _code_source(), "必须函数调用 ModelConnectionForm(vm=...)"
        assert "ModelConnectionsViewModel" in _code_source(), "必须经独立 ModelConnectionsViewModel 消费"

    def test_consumes_scenario_route_editor(self):
        """DoD: 模型阶段必须函数调用消费共用 ScenarioRouteEditor (S5 §11.6)。"""
        assert "ScenarioRouteEditor(" in _code_source(), "必须函数调用 ScenarioRouteEditor(vm=...)"
        assert "ModelRoutesViewModel" in _code_source(), "必须经独立 ModelRoutesViewModel 消费"

    def test_consumes_shared_model_config_service(self):
        """DoD: 宿主必须共享 ModelConfigurationService 并原子提交 (§11.6 一次 save_draft)。"""
        assert "ModelConfigurationService(" in _code_source(), "必须创建共享 ModelConfigurationService"
        assert "save_draft(" in _code_source(), "必须经 config_service.save_draft 原子提交"

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
        assert "STEP_CONFIGS" in _raw_source(), "必须使用 STEP_CONFIGS 驱动 7 步状态机"

    def test_uses_use_state_model_substep(self):
        """DoD: 模型阶段子步骤号由宿主 View 本地 ft.use_state 承载 (S5 §11.6)。"""
        assert "model_substep, set_model_substep = ft.use_state(" in _code_source(), (
            "模型阶段三子步骤号必须由 View 本地 use_state 承载"
        )


# ============================================================================
# UX-09 (P2-04): wizard 关闭 config panel / 共用组件的 Enter 提交与内部保存
# ============================================================================


class TestWizardPanelsDisableEnterSubmit:
    """UX-09 (§11.6): wizard 消费 config panel / 共用组件时禁用 Enter 提交。

    wizard 首次配置流程中, Enter 不应触发网络验证（卡流程）, 故面板调用必须显式
    ``enable_enter_submit=False``；模型阶段共用 ``ModelConnectionForm`` 还需
    ``show_internal_save=False``（向导隐藏组件内部保存按钮，由宿主显式按钮推进）。
    ``ScenarioRouteEditor`` 不接收 ``enable_enter_submit``（其内部无字段 Enter 提交）。
    用 AST 精确断言关键字参数实际值为 ``False``（非字符串/弱断言）。
    """

    _PANEL_NAMES = (
        "DatabaseConfigPanel",
        "TushareConfigPanel",
        "ModelConnectionForm",
        "ScenarioRouteEditor",
    )
    #: 必须显式传 enable_enter_submit=False 的面板。
    _ENTER_SUBMIT_PANELS = ("DatabaseConfigPanel", "TushareConfigPanel", "ModelConnectionForm")

    def _panel_calls(self):
        """AST 提取 config panel / 共用组件函数调用的关键字参数。"""
        tree = ast.parse(_raw_source())
        panel_calls: dict[str, dict[str, ast.AST]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self._PANEL_NAMES:
                kwargs: dict[str, ast.AST] = {}
                for kw in node.keywords:
                    if kw.arg is not None:
                        kwargs[kw.arg] = kw.value
                panel_calls[node.func.id] = kwargs
        return panel_calls

    def test_wizard_consumes_all_panels_and_shared_components(self):
        """4 个消费点均存在（DatabaseConfigPanel / TushareConfigPanel / 共用组件）。"""
        calls = self._panel_calls()
        assert set(calls.keys()) == set(self._PANEL_NAMES), f"wizard 应消费 4 个组件, 实际 {set(calls.keys())}"

    def test_panels_passed_enable_enter_submit_false(self):
        """面板调用均传 enable_enter_submit=False。"""
        calls = self._panel_calls()
        for name in self._ENTER_SUBMIT_PANELS:
            kwargs = calls[name]
            assert "enable_enter_submit" in kwargs, f"{name} 应传 enable_enter_submit=False"
            val = kwargs["enable_enter_submit"]
            assert isinstance(val, ast.Constant)
            assert val.value is False, f"{name}.enable_enter_submit 应为 False"

    def test_database_panel_disable_enter_submit(self):
        """AST: DatabaseConfigPanel 传 enable_enter_submit=False。"""
        calls = self._panel_calls()
        val = calls["DatabaseConfigPanel"]["enable_enter_submit"]
        assert isinstance(val, ast.Constant)
        assert val.value is False

    def test_tushare_panel_disable_enter_submit(self):
        """AST: TushareConfigPanel 传 enable_enter_submit=False。"""
        calls = self._panel_calls()
        val = calls["TushareConfigPanel"]["enable_enter_submit"]
        assert isinstance(val, ast.Constant)
        assert val.value is False

    def test_model_connection_form_disable_enter_submit(self):
        """AST: ModelConnectionForm 传 enable_enter_submit=False。"""
        calls = self._panel_calls()
        val = calls["ModelConnectionForm"]["enable_enter_submit"]
        assert isinstance(val, ast.Constant)
        assert val.value is False

    def test_model_connection_form_hides_internal_save(self):
        """AST (§11.6): ModelConnectionForm 传 show_internal_save=False（隐藏组件内部保存按钮）。"""
        calls = self._panel_calls()
        val = calls["ModelConnectionForm"]["show_internal_save"]
        assert isinstance(val, ast.Constant)
        assert val.value is False

    def test_scenario_route_editor_no_enable_enter_submit(self):
        """ScenarioRouteEditor 不接收 enable_enter_submit（其内部无字段 Enter 提交）。"""
        calls = self._panel_calls()
        assert "enable_enter_submit" not in calls["ScenarioRouteEditor"]


class TestWizardScheduleStepEnterSubmit:
    """UX-09: schedule step 时间 TextField Enter 提交绑定 ``on_enter_next``。

    ``_build_schedule_step`` 始终将时间输入 ``on_submit`` 绑定到 ``on_enter_next``
    （wizard 传 ``_on_next``, 即下一步主动作）；运行时行为见 ComponentBody 测试。
    """

    def test_time_field_on_submit_bound_to_on_enter_next(self):
        """源码: 时间 TextField on_submit 与 on_enter_next 绑定。"""
        src = _raw_source()
        assert "on_submit=safe_on_change(on_enter_next) if on_enter_next else None" in src

    def test_schedule_step_wired_to_on_next(self):
        """AST: ``_build_schedule_step`` 调用第 5 个位置参数为 ``_on_next``。

        即 schedule 时间输入 Enter 提交 = 下一步主动作（wizard 传 ``_on_next``）。
        """
        tree = ast.parse(_raw_source())
        call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_build_schedule_step"
        )
        assert len(call.args) == 5
        fifth = call.args[4]
        assert isinstance(fifth, ast.Name)
        assert fifth.id == "_on_next"


# ============================================================================
# UX-09 / MAJOR-01: 欢迎步投资风险提示 + 确认勾选 + 下一步禁用联动
# ============================================================================


class TestWizardWelcomeRiskAck:
    """欢迎步渲染风险提示卡；未勾选时 NEXT 按钮 disabled=True。"""

    def _render_welcome(self, mock_onboarding_vms, risk_acknowledged: bool):
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=0, risk_acknowledged=risk_acknowledged)
        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        return render_once(component)

    def _next_button(self, container) -> ft.Button:
        from ui.i18n import I18n

        buttons = [
            c
            for c in _collect_controls(container)
            if isinstance(c, ft.Button) and c.content == I18n.get("wizard_btn_start")
        ]
        assert len(buttons) == 1, f"欢迎步应恰有 1 个 NEXT 按钮, 实际 {len(buttons)}"
        return buttons[0]

    def test_welcome_renders_risk_checkbox_and_notice(
        self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms
    ):
        from ui.i18n import I18n

        result = self._render_welcome(mock_onboarding_vms, False)
        all_controls = _collect_controls(result)
        checkboxes = [c for c in all_controls if isinstance(c, ft.Checkbox)]
        assert len(checkboxes) == 1, "欢迎步应恰有 1 个风险提示确认 Checkbox"
        assert checkboxes[0].value is False
        texts = {c.value for c in all_controls if isinstance(c, ft.Text)}
        assert I18n.get("wizard_risk_title") in texts
        assert I18n.get("wizard_risk_body") in texts
        assert I18n.get("wizard_risk_ack_label") in texts

    def test_welcome_next_disabled_when_not_acknowledged(
        self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms
    ):
        result = self._render_welcome(mock_onboarding_vms, False)
        assert self._next_button(result).disabled is True

    def test_welcome_next_enabled_when_acknowledged(self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms):
        result = self._render_welcome(mock_onboarding_vms, True)
        assert self._next_button(result).disabled is False
        checkboxes = [c for c in _collect_controls(result) if isinstance(c, ft.Checkbox)]
        assert checkboxes[0].value is True

    def test_risk_ack_change_forwards_checked_value_to_vm(
        self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms
    ):
        """勾选框 on_change 经 page.run_task 调用 _persist_risk_ack 并透传勾选值 (R16 异步调度)。

        P-ONB-01: 调度目标改为模块级 _persist_risk_ack（消费 VM 返回值 + 失败 snack），
        VM 命令与勾选值的透传由 args[1]/args[2] 断言。
        """
        result = self._render_welcome(mock_onboarding_vms, False)
        checkbox = next(c for c in _collect_controls(result) if isinstance(c, ft.Checkbox))
        fake_page = MagicMock(name="page")
        event = MagicMock()
        event.control.value = True
        # Flet 事件回调类型为 Optional[ControlEventHandler]，测试直调底层函数故显式 Any。
        on_change: Any = checkbox.on_change
        with patch("ui.views.onboarding_wizard._get_page", return_value=fake_page):
            on_change(event)
        assert fake_page.run_task.call_count == 1
        args = fake_page.run_task.call_args.args
        from ui.views.onboarding_wizard import _persist_risk_ack

        assert args[0] is _persist_risk_ack
        assert args[1] is mock_onboarding_vms["onboarding"]
        assert args[2] is True

    def test_risk_label_click_toggles_live_vm_state(self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms):
        """点击 label 取反 VM 实时勾选态（未勾选 → 提交 True）。"""
        from ui.i18n import I18n

        result = self._render_welcome(mock_onboarding_vms, False)
        label = next(
            c
            for c in _collect_controls(result)
            if isinstance(c, ft.GestureDetector)
            and isinstance(c.content, ft.Text)
            and c.content.value == I18n.get("wizard_risk_ack_label")
        )
        fake_page = MagicMock(name="page")
        on_tap: Any = label.on_tap
        with patch("ui.views.onboarding_wizard._get_page", return_value=fake_page):
            on_tap(MagicMock())
        assert fake_page.run_task.call_count == 1
        assert fake_page.run_task.call_args.args[2] is True

    def test_risk_handlers_noop_without_page(self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms):
        """无 page 上下文（离屏渲染）时两个 handler 均安全 no-op。"""
        result = self._render_welcome(mock_onboarding_vms, False)
        checkbox = next(c for c in _collect_controls(result) if isinstance(c, ft.Checkbox))
        on_change: Any = checkbox.on_change
        with patch("ui.views.onboarding_wizard._get_page", return_value=None):
            on_change(MagicMock())
        assert mock_onboarding_vms["onboarding"].update_risk_disclaimer_acknowledged.call_count == 0

    async def test_persist_risk_ack_failure_shows_error_snack(
        self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms
    ):
        """P-ONB-01: VM 命令返回 False（保存失败）时经 _show_snack 反馈错误。"""
        import ui.views.onboarding_wizard as wizard_mod
        from ui.i18n import I18n

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm.update_risk_disclaimer_acknowledged = AsyncMock(return_value=False)
        fake_page = MagicMock(name="page")
        with patch.object(wizard_mod, "_get_page", return_value=fake_page):
            await wizard_mod._persist_risk_ack(fake_vm, True)
        fake_page.toast.show.assert_called_once_with(I18n.get("sys_snack_save_err"), "error")

    async def test_persist_risk_ack_success_no_snack(self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms):
        """P-ONB-01: VM 命令返回 True（保存成功）时不弹 snack。"""
        import ui.views.onboarding_wizard as wizard_mod

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm.update_risk_disclaimer_acknowledged = AsyncMock(return_value=True)
        fake_page = MagicMock(name="page")
        with patch.object(wizard_mod, "_get_page", return_value=fake_page):
            await wizard_mod._persist_risk_ack(fake_vm, True)
        fake_vm.update_risk_disclaimer_acknowledged.assert_awaited_once_with(True)
        fake_page.toast.show.assert_not_called()


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


class TestCommitModelStage:
    """``_commit_model_stage`` 模块级宿主提交函数测试 (S5 §11.6)。

    「保存并继续」经共享 ``ModelConfigurationService.save_draft`` 单次原子提交
    连接草稿（合并进已提交连接）+ 全部场景路由草稿；冲突/失败返回 False；
    取消显式传播 (R2)。
    """

    @staticmethod
    def _make_vms(*, draft_name: str = "New Conn", status: str = "committed"):
        """构造两个 fake VM + fake config_service（共享 service）。"""
        from core.model_management_types import ConfigurationSaveOutcome

        draft = ModelConnectionDraft(name=draft_name)
        connections = _FakeModelConnectionsViewModel(ModelConnectionsState(draft=draft, config_revision=7))
        connections._raw_connections = ({"connection_id": "existing-1", "name": "Old"},)
        connections.entry_result = {"connection_id": "new-1", "name": draft_name}
        routes = _FakeModelRoutesViewModel(ModelRoutesState(selected_scenario="stock_analysis"))
        routes.payload_result = {"schema_version": 1, "config_revision": 7}
        routes.routes_result = [{"scenario_id": "stock_analysis", "candidates": []}]
        # F13 悬空校验：默认无悬空候选（MagicMock 默认 truthy 会误触发拦截）
        routes.unresolved_candidates = MagicMock(return_value=[])
        config_service = MagicMock(name="config_service")
        config_service.save_draft = AsyncMock(return_value=ConfigurationSaveOutcome(status=status))
        return connections, routes, config_service

    @pytest.mark.asyncio
    async def test_commits_all_connections_and_routes_in_single_save(self):
        """连接草稿 + 场景路由草稿经单次 save_draft 原子提交，返回 True。"""
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms()

        result = await _commit_model_stage(connections, routes, config_service)

        assert result is True
        config_service.save_draft.assert_awaited_once()
        payload, expected_revision, operation_id = config_service.save_draft.call_args.args
        assert expected_revision == 7
        assert isinstance(operation_id, str) and operation_id
        # 已提交连接 existing-1 保留，草稿 new-1 追加（无重复）
        assert [c["connection_id"] for c in payload["connections"]] == ["existing-1", "new-1"]
        # 场景路由草稿随同一 payload 提交
        assert "scenario_routes" in payload

    @pytest.mark.asyncio
    async def test_returns_false_when_not_committed(self):
        """save_draft 结果非 committed 时返回 False。"""
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms(status="not_committed")

        result = await _commit_model_stage(connections, routes, config_service)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_conflict(self):
        """save_draft 抛 ModelConfigurationConflictError 时返回 False（保留草稿）。"""
        from core.model_management_types import ModelConfigurationConflictError
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms()
        config_service.save_draft = AsyncMock(side_effect=ModelConfigurationConflictError(3, 5))

        result = await _commit_model_stage(connections, routes, config_service)

        assert result is False

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self):
        """取消时显式传播 asyncio.CancelledError（R2），不降级为 False。"""
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms()
        config_service.save_draft = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await _commit_model_stage(connections, routes, config_service)
        # 取消来自保存调用本身（非未触达即抛出），确认确已执行到保存边界
        config_service.save_draft.assert_awaited_once()
        assert type(exc_info.value) is asyncio.CancelledError

    @pytest.mark.asyncio
    async def test_empty_draft_name_does_not_insert_connection(self):
        """连接草稿名称为空时不解析凭据/不追加连接，仅提交既有连接与路由。"""
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms(draft_name="")

        result = await _commit_model_stage(connections, routes, config_service)

        assert result is True
        payload = config_service.save_draft.call_args.args[0]
        assert payload["connections"] == [{"connection_id": "existing-1", "name": "Old"}]

    @pytest.mark.asyncio
    async def test_validation_failure_blocks_save(self):
        """连接草稿静态校验失败时返回 False 且不提交（本地校验前置）。"""
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms()
        connections.validation_errors = ("model_connection_error_model_required",)

        result = await _commit_model_stage(connections, routes, config_service)

        assert result is False
        config_service.save_draft.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_validates_only_when_draft_named(self):
        """空草稿名时跳过静态校验（不因缺名阻断只配置场景路由的用户）。"""
        from ui.views.onboarding_wizard import _commit_model_stage

        connections, routes, config_service = self._make_vms(draft_name="")
        connections.validate_draft = MagicMock(return_value=("model_connection_error_name_required",))

        result = await _commit_model_stage(connections, routes, config_service)

        assert result is True
        connections.validate_draft.assert_not_called()  # type: ignore[attr-defined]


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
import inspect
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
from ui.viewmodels.model_connections_view_model import ModelConnectionDraft, ModelConnectionsState
from ui.viewmodels.model_routes_view_model import ModelRoutesState


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
    # UX-09 / MAJOR-01: 欢迎步风险提示确认状态。
    risk_acknowledged: bool = False


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
        #: UIR-F02-003: 记录 View 触发的验证缓存失效步骤。
        self.invalidated_steps: list[str] = []
        # 异步方法需返回 awaitable（event handler 通过 page.run_task 调用）
        self.next_step = AsyncMock()
        self.prev_step = AsyncMock()
        self.skip_step = AsyncMock()
        self.start_sync = AsyncMock()
        self.skip_sync = AsyncMock()
        self.save_language = AsyncMock(return_value=True)
        self.update_risk_disclaimer_acknowledged = AsyncMock(return_value=True)

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

    def invalidate_step(self, step_id: str) -> None:
        self.invalidated_steps.append(step_id)

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


class _FakeModelConnectionsViewModel:
    """模拟 ModelConnectionsViewModel（连接草稿 VM，S5 §11.6 模型阶段）。

    实现向导宿主实际消费的公开 API（``validate_draft`` / ``draft_connection_entry`` /
    ``merged_connections``）；未显式定义的方法/属性经 ``__getattr__`` 返回 MagicMock。
    """

    def __init__(self, state: ModelConnectionsState | None = None) -> None:
        self._state: ModelConnectionsState = state or ModelConnectionsState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self._raw_connections: tuple[Any, ...] = ()
        self.load = AsyncMock()
        self.start_add = MagicMock()
        self.save = AsyncMock(return_value=True)
        #: ``validate_draft`` 返回的静态校验结果（默认无错误）。
        self.validation_errors: tuple[str, ...] = ()
        #: ``draft_connection_entry`` 的固定返回（草稿非空时）。
        self.entry_result: dict[str, Any] | None = None

    @property
    def state(self) -> ModelConnectionsState:
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

    def validate_draft(self) -> tuple[str, ...]:
        return tuple(self.validation_errors)

    async def draft_connection_entry(self, operation_id: str) -> dict[str, Any] | None:
        if not self._state.draft.name.strip():
            return None
        return self.entry_result

    def merged_connections(self, entry: Any) -> list[dict[str, Any]]:
        merged = [dict(c) for c in self._raw_connections]
        if entry is None:
            return merged
        connection_id = str(entry.get("connection_id", ""))
        for index, existing in enumerate(merged):
            if str(existing.get("connection_id")) == connection_id:
                merged[index] = dict(entry)
                return merged
        merged.append(dict(entry))
        return merged

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return MagicMock()


class _FakeModelRoutesViewModel:
    """模拟 ModelRoutesViewModel（场景路由草稿 VM，S5 §11.6 模型阶段）。"""

    def __init__(self, state: ModelRoutesState | None = None) -> None:
        self._state: ModelRoutesState = state or ModelRoutesState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.load = AsyncMock()
        #: F13 自动激活（committed 后宿主调用；默认成功且无告警消息）
        self.activate_fresh_setup = AsyncMock(return_value=True)
        #: ``build_payload`` 返回的完整 payload 基底。
        self.payload_result: dict[str, Any] = {"schema_version": 1, "config_revision": 1}
        #: ``all_routes_payload`` 返回的全部场景草稿序列化。
        self.routes_result: list[dict[str, Any]] = []

    @property
    def state(self) -> ModelRoutesState:
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

    def build_payload(self, scenario_id: str) -> dict[str, Any]:
        return dict(self.payload_result)

    def all_routes_payload(self) -> list[dict[str, Any]]:
        return list(self.routes_result)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return MagicMock()


def _make_fake_page() -> FakePage:
    """创建带 run_task/toast/locale_configuration 的 FakePage。

    ``run_task`` 同步执行协程（测试需验证 cleanup 等异步路径的实际行为）。
    """
    page = FakePage()

    def _run_task(coro_func: Any, *args: Any, **kwargs: Any) -> None:
        coro = coro_func(*args, **kwargs)
        if inspect.iscoroutine(coro):
            asyncio.run(coro)

    page.run_task = _run_task  # type: ignore[method-assign]
    page.toast = MagicMock()  # type: ignore[attr-defined]  # [reason: ToastManager 由 application.py 挂载到 page.toast]
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
    """注入 FakeVM/FakeComponent 替换 OnboardingWizard 消费的 VM 类与共用组件。

    替换：
    - OnboardingViewModel → _FakeOnboardingViewModel
    - DatabaseConfigPanelViewModel / TushareConfigPanelViewModel → _FakePanelViewModel
    - ModelConnectionsViewModel → _FakeModelConnectionsViewModel
    - ModelRoutesViewModel → _FakeModelRoutesViewModel
    - ModelConfigurationService → MagicMock（宿主共享 service）
    - ModelConnectionForm / ScenarioRouteEditor → 记录调用的 Fake 组件（返回带 key 的 Container）

    契约测试聚焦「wizard 消费共用组件 + 独立 VM + 提交/dispose/Enter 行为」，故共用组件
    以 Fake 替换，避免在离屏渲染器中执行其内部 hooks（真实组件行为由各自契约测试覆盖）。
    """
    # 确保 ui.views.onboarding_wizard 模块已加载（monkeypatch.setattr 需要模块属性存在）
    import ui.views.onboarding_wizard as wizard_mod  # noqa: F401

    fake_onboarding_vm = _FakeOnboardingViewModel()
    fake_database_vm = _FakePanelViewModel()
    fake_tushare_vm = _FakePanelViewModel()
    fake_connections_vm = _FakeModelConnectionsViewModel()
    fake_routes_vm = _FakeModelRoutesViewModel()
    fake_config_service = MagicMock(name="config_service")
    fake_config_service.save_draft = AsyncMock()
    fake_connection_form = MagicMock(name="ModelConnectionForm")
    fake_connection_form.return_value = ft.Container(key="fake_model_connection_form")
    fake_route_editor = MagicMock(name="ScenarioRouteEditor")
    fake_route_editor.return_value = ft.Container(key="fake_scenario_route_editor")

    # 生产代码 factory lambda 带关键字参数调用 VM 构造器（如 load_password=True），
    # mock lambda 需接受任意参数返回 fake_vm 实例
    monkeypatch.setattr(wizard_mod, "OnboardingViewModel", lambda *a, **kw: fake_onboarding_vm)
    monkeypatch.setattr(wizard_mod, "DatabaseConfigPanelViewModel", lambda *a, **kw: fake_database_vm)
    monkeypatch.setattr(wizard_mod, "TushareConfigPanelViewModel", lambda *a, **kw: fake_tushare_vm)
    monkeypatch.setattr(wizard_mod, "ModelConnectionsViewModel", lambda *a, **kw: fake_connections_vm)
    monkeypatch.setattr(wizard_mod, "ModelRoutesViewModel", lambda *a, **kw: fake_routes_vm)
    monkeypatch.setattr(wizard_mod, "ModelConfigurationService", lambda *a, **kw: fake_config_service)
    monkeypatch.setattr(wizard_mod, "ModelConnectionForm", fake_connection_form)
    monkeypatch.setattr(wizard_mod, "ScenarioRouteEditor", fake_route_editor)

    return {
        "onboarding": fake_onboarding_vm,
        "database": fake_database_vm,
        "tushare": fake_tushare_vm,
        "connections": fake_connections_vm,
        "routes": fake_routes_vm,
        "config_service": fake_config_service,
        "connection_form": fake_connection_form,
        "route_editor": fake_route_editor,
    }


class TestOnboardingWizardComponentBody:
    """OnboardingWizard @ft.component 组件体测试（attach_fake_page 模式）。

    覆盖 7 步状态机渲染 + VM 生命周期 + 条件可见性，目标覆盖率 ≥80%。
    """

    def test_step0_welcome_renders_language_dropdown_and_overview_cards(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 0 (Welcome)：渲染语言下拉 + 5 张 overview 卡片。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        all_controls = _collect_controls(result)
        # 语言下拉
        assert any(isinstance(c, ft.Dropdown) for c in all_controls)
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

    def test_step3_model_scenario_renders_hub_icon_and_connection_form(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 3 (模型与场景配置)：渲染 HUB 图标 + 共用 ModelConnectionForm 组件。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=3)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        assert len(_find_icons(result, ft.Icons.HUB)) >= 1
        # 首子步骤(1) 渲染共用 ModelConnectionForm（fake 返回带 key 的 Container）
        assert any(getattr(c, "key", None) == "fake_model_connection_form" for c in _collect_controls(result))

    def test_step4_data_sync_renders_sync_buttons_and_progress_bar(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 4 (Data Sync)：渲染同步按钮 + 进度条 + CLOUD_DOWNLOAD 图标。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=4)

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

    def test_step5_schedule_renders_checkbox_and_time_input(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 5 (Schedule)：渲染复选框 + 时间输入 + SCHEDULE 图标。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        assert isinstance(result, ft.Container)
        all_controls = _collect_controls(result)
        assert len(_find_icons(result, ft.Icons.SCHEDULE)) >= 1
        assert any(isinstance(c, ft.Checkbox) for c in all_controls)
        assert any(isinstance(c, ft.TextField) for c in all_controls)

    def test_step6_complete_renders_celebration_icon(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """step 6 (Complete)：渲染 CELEBRATION 图标。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=6)

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
        assert len(mock_onboarding_vms["connections"]._subscribers) > 0
        assert len(mock_onboarding_vms["routes"]._subscribers) > 0

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
        assert mock_onboarding_vms["connections"].dispose_called is True
        assert mock_onboarding_vms["routes"].dispose_called is True

    def test_unmount_cleanup_logs_when_cancel_sync_fails(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """unmount 时 cancel_sync 抛异常, _do_cleanup 的 except 捕获并记 debug 日志.

        覆盖 onboarding_wizard.py L391-395 logger.debug 分支:
        - sync_in_progress=True 触发 cancel_sync 调用
        - cancel_sync 抛异常 → except 捕获 → logger.debug (不传播)
        - _make_fake_page 的 run_task 同步执行 _do_cleanup 协程
        """
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, sync_in_progress=True)
        fake_vm.cancel_sync = AsyncMock(side_effect=RuntimeError("cancel failed"))

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)

        # unmount 触发 _cleanup → page.run_task(_do_cleanup) → cancel_sync 抛异常
        # except 必须捕获, 否则 asyncio.run 会传播 RuntimeError 导致测试失败
        run_unmount_effects(component)

        # cancel_sync 被调用证明 if sync_in_progress 分支已执行
        # assert_called_once_with() 验证无参数调用 (cancel_sync 签名无参, 强断言)
        fake_vm.cancel_sync.assert_called_once_with()

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
        """step 1-5 显示进度条 (visible=True), step 0/6 隐藏 (visible=False)。"""
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

        # Step 6 (complete): indicators hidden
        fake_vm._state = replace(fake_vm._state, current_step=6)
        comp6 = make_component(OnboardingWizard)
        page6 = _make_fake_page()
        run_mount_effects(comp6, page6)
        result6 = render_once(comp6)
        main_col6 = result6.content.controls[0]  # type: ignore[union-attr]
        step_indicators6 = main_col6.controls[2]  # type: ignore[union-attr]
        assert step_indicators6.visible is False

    def test_navigation_bar_button_visibility(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """不同 step 的导航按钮可见性（step 0 无 prev, step 3 有 prev+skip+next）。"""
        from ui.theme import AppColors
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
        # 契约: 底部导航条底色必须与向导页面背景一致 (background), 否则出现"灰色长方形"断层
        assert getattr(nav_container0, "bgcolor", None) == AppColors.BACKGROUND
        nav_row0 = nav_container0.content  # type: ignore[union-attr]
        # Step 0: show_prev=False → first is ft.Container (placeholder)
        assert isinstance(nav_row0.controls[0], ft.Container)
        # show_next=True → at least one ft.Button
        buttons0 = [c for c in nav_row0.controls if isinstance(c, ft.Button)]
        assert len(buttons0) >= 1

        # Step 3 (model_scenario): show_prev + show_skip + show_next
        fake_vm._state = replace(fake_vm._state, current_step=3)
        comp3 = make_component(OnboardingWizard)
        page3 = _make_fake_page()
        run_mount_effects(comp3, page3)
        result3 = render_once(comp3)
        main_col3 = result3.content.controls[0]  # type: ignore[union-attr]
        nav_container3 = main_col3.controls[-1]  # type: ignore[union-attr]
        nav_row3 = nav_container3.content  # type: ignore[union-attr]
        nav_buttons3 = [c for c in nav_row3.controls if isinstance(c, (ft.Button, ft.TextButton))]
        # prev (Button) + skip (TextButton) + next (Button) = 3
        assert len(nav_buttons3) >= 3

    def test_navigation_buttons_trigger_vm_methods(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """点击导航按钮触发 VM 方法：模型阶段首子步骤 prev/skip 直达 VM，next 仅推进子步骤。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=3)

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

        # 模型阶段首子步骤(1)：prev → vm.prev_step；skip → vm.skip_step；next 仅推进子步骤
        fake_vm.prev_step.assert_called_once()
        fake_vm.skip_step.assert_called_once()
        fake_vm.next_step.assert_not_called()

    def test_sync_buttons_trigger_vm_methods(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """点击同步按钮触发 VM 的 start_sync/cancel_sync/skip_sync 方法。"""
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=4)

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

    def test_language_select_failure_triggers_locale_rollback_refresh(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
        monkeypatch,
    ):
        """D17: save_language 失败 → 重设当前 locale 触发重渲染 (下拉刷新回旧值)."""
        from unittest.mock import AsyncMock

        import ui.views.onboarding_wizard as wizard_mod

        # 拦截 I18n.set_locale 以便断言调用 (失败路径应重设当前值触发重渲染)
        set_locale_mock = MagicMock()
        monkeypatch.setattr(wizard_mod.I18n, "set_locale", set_locale_mock)

        component = make_component(wizard_mod.OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        render_once(component)

        # fake VM 的 save_language 返回 False (持久化失败)
        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm.save_language = AsyncMock(return_value=False)

        all_controls = _collect_controls(render_once(component))
        dropdowns = [c for c in all_controls if isinstance(c, ft.Dropdown)]
        assert len(dropdowns) >= 1
        mock_event = MagicMock()
        mock_event.control.value = "en_US"
        dropdowns[0].on_select(mock_event)  # type: ignore[union-attr]

        # run_task 同步执行 → 失败路径调用了 I18n.set_locale(当前 locale) 强制刷新
        set_locale_mock.assert_called_once_with("zh_CN")
        fake_vm.save_language.assert_awaited_once_with("en_US")

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

    def test_step5_schedule_time_enter_submit_triggers_next(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """UX-09: step 5 schedule 时间输入 on_submit → 触发 wizard 下一步 (vm.next_step)。

        时间 TextField ``on_submit`` 绑定 ``_on_next`` → ``page.run_task(_next_step)``
        → ``await onboarding_vm.next_step()``（与下一步按钮等价）。
        """
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        all_controls = _collect_controls(result)
        time_fields = [c for c in all_controls if isinstance(c, ft.TextField)]
        assert len(time_fields) == 1, "step 5 应仅含 schedule 时间输入 1 个 TextField"
        time_field = time_fields[0]
        # 守卫 + 下行触发/next_step 行为断言构成链式验证
        assert time_field.on_submit is not None  # noqa: weak-assertion 守卫, 下行 on_submit/next_step 断言为链式验证

        time_field.on_submit(MagicMock())  # type: ignore[reportCallIssue, reason: Flet stub declares on_submit as 0-arg, but runtime passes event]
        fake_vm.next_step.assert_awaited_once_with()

    def test_step5_schedule_save_failure_shows_error_toast(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
        monkeypatch,
    ):
        """UIR-F02-001: 计划配置落盘失败 (next_step 返回 False) → 停留并显示错误 toast。"""
        from unittest.mock import AsyncMock

        import ui.views.onboarding_wizard as wizard_mod

        monkeypatch.setattr(wizard_mod.I18n, "get", lambda key, **kwargs: key)
        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)
        fake_vm.next_step = AsyncMock(return_value=False)

        component = make_component(wizard_mod.OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        time_fields = [c for c in _collect_controls(result) if isinstance(c, ft.TextField)]
        assert len(time_fields) == 1
        time_fields[0].on_submit(MagicMock())  # type: ignore[reportCallIssue]  # Flet stub declares on_submit as 0-arg, but runtime passes event

        fake_vm.next_step.assert_awaited_once_with()
        page.toast.show.assert_called_once_with("wizard_schedule_save_failed", "error")

    def test_step5_schedule_save_success_no_error_toast(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """UIR-F02-001: 保存成功 (next_step 返回 True) 时不显示错误 toast。"""
        from unittest.mock import AsyncMock

        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)
        fake_vm.next_step = AsyncMock(return_value=True)

        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        result = render_once(component)

        time_fields = [c for c in _collect_controls(result) if isinstance(c, ft.TextField)]
        assert len(time_fields) == 1
        time_fields[0].on_submit(MagicMock())  # type: ignore[reportCallIssue]  # Flet stub declares on_submit as 0-arg, but runtime passes event

        fake_vm.next_step.assert_awaited_once_with()
        page.toast.show.assert_not_called()

    def test_show_snack_with_no_page(self, monkeypatch):
        """_show_snack 在 page 不可用时安全降级（不抛异常）。"""
        from ui.views.onboarding_wizard import _show_snack

        # 清除 _context_page 使 _get_page 返回 None
        from flet.controls.context import _context_page

        _context_page.set(None)
        # 不抛异常即覆盖 page=None 降级路径
        _show_snack("test message", "#FF0000")


# ============================================================================
# UIR-F02-003: schedule 草稿变更失效验证缓存（View 联动）
# ============================================================================


class TestWizardScheduleCacheInvalidation:
    """计划草稿输入 on_change 必须失效 schedule 验证缓存，避免完成页返回后跳过落盘。"""

    def _render_step5(self, mock_onboarding_vms):
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=5)
        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        return fake_vm, render_once(component)

    def test_schedule_checkbox_change_invalidates_validation_cache(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """开关 on_change → set 局部草稿 + invalidate_step("schedule")。"""
        fake_vm, result = self._render_step5(mock_onboarding_vms)
        checkboxes = [c for c in _collect_controls(result) if isinstance(c, ft.Checkbox)]
        assert len(checkboxes) == 1

        event = MagicMock()
        event.control.value = False
        checkboxes[0].on_change(event)  # type: ignore[reportCallIssue]  # Flet stub declares on_change as 0-arg, but runtime passes event

        assert fake_vm.invalidated_steps == ["schedule"]

    def test_schedule_time_change_invalidates_validation_cache(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """时间 on_change → set 局部草稿 + invalidate_step("schedule")。"""
        fake_vm, result = self._render_step5(mock_onboarding_vms)
        time_fields = [c for c in _collect_controls(result) if isinstance(c, ft.TextField)]
        assert len(time_fields) == 1

        event = MagicMock()
        event.control.value = "09:00"
        time_fields[0].on_change(event)  # type: ignore[reportCallIssue]  # Flet stub declares on_change as 0-arg, but runtime passes event

        assert fake_vm.invalidated_steps == ["schedule"]

    def test_schedule_next_button_triggers_same_next_step_command(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_onboarding_vms,
    ):
        """「完成」按钮与时间输入 Enter 走同一命令（vm.next_step）。"""
        from ui.i18n import I18n

        fake_vm, result = self._render_step5(mock_onboarding_vms)
        buttons = [
            c
            for c in _collect_controls(result)
            if isinstance(c, ft.Button) and c.content == I18n.get("wizard_btn_finish")
        ]
        assert len(buttons) == 1

        buttons[0].on_click(MagicMock())  # type: ignore[reportCallIssue]  # Flet stub declares on_click as 0-arg, but runtime passes event

        fake_vm.next_step.assert_awaited_once_with()


# ============================================================================
# UIR-F02-002: data_sync 步同步中禁用公共「下一步」
# ============================================================================


class TestWizardDataSyncNextDisabled:
    """同步进行中公共「下一步」必须禁用（与「上一步」同口径）。"""

    def _render_step4(self, mock_onboarding_vms, sync_in_progress: bool):
        from ui.views.onboarding_wizard import OnboardingWizard

        fake_vm = mock_onboarding_vms["onboarding"]
        fake_vm._state = replace(fake_vm._state, current_step=4, sync_in_progress=sync_in_progress)
        component = make_component(OnboardingWizard)
        page = _make_fake_page()
        run_mount_effects(component, page)
        return render_once(component)

    def _next_button(self, container) -> ft.Button:
        from ui.i18n import I18n

        buttons = [
            c
            for c in _collect_controls(container)
            if isinstance(c, ft.Button) and c.content == I18n.get("wizard_btn_next")
        ]
        assert len(buttons) == 1, f"data_sync 步应恰有 1 个 NEXT 按钮, 实际 {len(buttons)}"
        return buttons[0]

    def test_next_disabled_while_sync_in_progress(self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms):
        """g) 同步中 footer「下一步」disabled=True。"""
        result = self._render_step4(mock_onboarding_vms, sync_in_progress=True)
        assert self._next_button(result).disabled is True

    def test_next_enabled_when_sync_not_in_progress(self, mock_i18n_state, mock_app_colors_state, mock_onboarding_vms):
        result = self._render_step4(mock_onboarding_vms, sync_in_progress=False)
        assert self._next_button(result).disabled is False
