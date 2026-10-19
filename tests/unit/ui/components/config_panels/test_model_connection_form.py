"""ModelConnectionForm 组件运行时测试 (S5 双入口, reviews/模型统一.md §11.2/§11.3)。

覆盖契约 (R19)：
- 契约守卫：@ft.component / use_viewmodel(vm=) 外部 VM 模式 / ft.context.page / 签名默认值
- adapter_kind 条件字段：litellm_chat 显 provider/model/api_base/凭据；
  gguf_chat 显对话文件；gguf_sentiment 显编码器/head/tokenizer 三路径
- 分类费用型（encoder_input_tokens）只显 encoder 输入价；llm_tokens 显输入/输出价
- enable_enter_submit 控制字段 on_submit（False 不提交）
- show_internal_save 控制内部保存按钮是否渲染
- 凭据初始为空（不回显）；空输入只 update_credential，不触发显式清除
- FilePicker 注册（mount）/ cleanup（unmount）各一次
- 字段事件 → vm 命令
- 模块级 _select_file 纯函数（正常 / 取消 / 异常）

风格参照 test_local_model_config_panel.py（component_renderer 基础设施 + _walk_controls）。
"""

import asyncio
import contextlib
import inspect
from typing import Any, cast
from unittest.mock import MagicMock, patch

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
from ui.components.config_panels import model_connection_form as form_module
from ui.components.config_panels.model_connection_form import (
    ModelConnectionForm,
    _select_file,
)
from ui.viewmodels.model_connections_view_model import (
    ADAPTER_GGUF_CHAT,
    ADAPTER_GGUF_SENTIMENT,
    ADAPTER_LITELLM,
    ModelConnectionDraft,
    ModelConnectionsState,
)

pytestmark = pytest.mark.unit

# --- 字段 label / i18n key（与组件模块常量对齐） ---
LABEL_NAME = "model_connection_field_name"
LABEL_ADAPTER = "model_connection_field_adapter"
LABEL_ENABLED = "model_connection_field_enabled"
LABEL_PROVIDER = "model_connection_field_provider"
LABEL_MODEL = "model_connection_field_model"
LABEL_API_BASE = "model_connection_field_api_base"
LABEL_CREDENTIAL = "model_connection_field_credential"
LABEL_TIMEOUT = "model_connection_field_timeout"
LABEL_CONCURRENCY = "model_connection_field_concurrency"
LABEL_INPUT_PRICE = "model_connection_field_input_price"
LABEL_OUTPUT_PRICE = "model_connection_field_output_price"
LABEL_ENCODER_PRICE = "model_connection_field_encoder_price"
LABEL_DIALOG_PATH = "model_connection_field_dialog_model_path"
LABEL_ENCODER_PATH = "model_connection_field_encoder_model_path"
LABEL_HEAD_PATH = "model_connection_field_head_path"
LABEL_TOKENIZER_PATH = "model_connection_field_tokenizer_path"

_VM_COMMANDS = (
    "update_adapter_kind",
    "update_enabled",
    "update_name",
    "update_provider",
    "update_model",
    "update_api_base",
    "update_billing_mode",
    "update_billing_unit",
    "update_credential",
    "update_credential_cleared",
    "update_timeout_seconds",
    "update_concurrency_limit",
    "update_context_window",
    "update_input_price",
    "update_output_price",
    "update_encoder_price",
    "update_dialog_model_path",
    "update_encoder_model_path",
    "update_head_path",
    "update_tokenizer_path",
    "set_editor_step",
    "save",
    "test",
    "dispose",
)


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler（绕过 pyright 的 Optional/0 参 stub 限制）。"""
    handler(*args)


def _make_event(value: Any = None) -> MagicMock:
    e = MagicMock()
    e.control.value = value
    return e


def _walk_controls(root: Any) -> list[Any]:
    if root is None:
        return []
    if isinstance(root, Component):
        return _walk_controls(render_once(root))
    if not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_walk_controls(child))
    content_attr = getattr(root, "content", None)
    if isinstance(content_attr, (ft.Control, Component)):
        result.extend(_walk_controls(content_attr))
    return result


def _find_text_field(root: Any, label: str) -> ft.TextField | None:
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.TextField) and getattr(ctrl, "label", None) == label:
            return ctrl
    return None


def _find_switch(root: Any, label: str) -> ft.Switch | None:
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Switch) and getattr(ctrl, "label", None) == label:
            return ctrl
    return None


def _find_dropdown(root: Any, label: str) -> ft.Dropdown | None:
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.Dropdown) and getattr(ctrl, "label", None) == label:
            return ctrl
    return None


def _find_radio_group(root: Any) -> ft.RadioGroup | None:
    for ctrl in _walk_controls(root):
        if isinstance(ctrl, ft.RadioGroup):
            return ctrl
    return None


def _find_buttons(root: Any, icon: Any) -> list[ft.Button]:
    return [c for c in _walk_controls(root) if isinstance(c, ft.Button) and getattr(c, "icon", None) == icon]


def _text_field_labels(root: Any) -> set[str]:
    """收集渲染树中所有 TextField 的字符串 label（i18n key）。"""
    labels: set[str] = set()
    for c in _walk_controls(root):
        if isinstance(c, ft.TextField) and isinstance(c.label, str):
            labels.add(c.label)
    return labels


def _require_text_field(root: Any, label: str) -> ft.TextField:
    ctrl = _find_text_field(root, label)
    assert ctrl is not None, f"缺少 TextField: {label}"
    return ctrl


def _require_switch(root: Any, label: str) -> ft.Switch:
    ctrl = _find_switch(root, label)
    assert ctrl is not None, f"缺少 Switch: {label}"
    return ctrl


def _require_dropdown(root: Any, label: str) -> ft.Dropdown:
    ctrl = _find_dropdown(root, label)
    assert ctrl is not None, f"缺少 Dropdown: {label}"
    return ctrl


def _require_radio_group(root: Any) -> ft.RadioGroup:
    ctrl = _find_radio_group(root)
    assert ctrl is not None, "缺少 RadioGroup"
    return ctrl


def _async_return(value: Any) -> Any:
    async def _ret() -> Any:
        return value

    return _ret()


class _FakeModelConnectionsVM:
    """模拟 ModelConnectionsViewModel（满足 use_viewmodel(vm=) 外部 VM 模式契约）。"""

    def __init__(self, state: ModelConnectionsState | None = None) -> None:
        self._state = state if state is not None else ModelConnectionsState()
        self._subscribers: list[Any] = []
        for name in _VM_COMMANDS:
            setattr(self, name, MagicMock())

    @property
    def state(self) -> ModelConnectionsState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsub() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsub

    def dispose(self) -> None:
        self._subscribers.clear()


def _render_form(
    state: ModelConnectionsState | None = None,
    *,
    editor_step: int = 1,
    compact: bool = False,
    enable_enter_submit: bool = True,
    show_internal_save: bool = False,
    page: FakePage | None = None,
) -> tuple[_FakeModelConnectionsVM, FakePage, Any, Any]:
    """渲染 ModelConnectionForm，返回 (vm, page, result, component)。"""
    vm = _FakeModelConnectionsVM(state=state)
    if page is None:
        page = FakePage()
    page.run_task = MagicMock()  # type: ignore[reportAttributeAccessIssue]  # reason: FakePage 不定义 run_task, 测试注入

    with contextlib.ExitStack() as stack:
        mock_i18n = stack.enter_context(patch.object(form_module, "I18n"))
        mock_i18n.get.side_effect = lambda key, **kw: kw.get("default", key) if "default" in kw else key
        stack.enter_context(patch.object(form_module, "AppColors"))
        component = make_component(
            ModelConnectionForm,
            vm=vm,
            editor_step=editor_step,
            compact=compact,
            enable_enter_submit=enable_enter_submit,
            show_internal_save=show_internal_save,
        )
        run_mount_effects(component, page=page)
        result = render_once(component)

    return vm, page, result, component


def _draft(**kwargs: Any) -> ModelConnectionDraft:
    return ModelConnectionDraft(**kwargs)


# ============================================================================
# 契约守卫
# ============================================================================


class TestModelConnectionFormContract:
    def test_is_ft_component(self) -> None:
        assert hasattr(ModelConnectionForm, "__wrapped__"), "ModelConnectionForm 必须用 @ft.component 装饰"

    def test_signature(self) -> None:
        sig = inspect.signature(ModelConnectionForm)
        assert "vm" in sig.parameters
        assert "editor_step" in sig.parameters
        assert sig.parameters["compact"].default is False
        assert sig.parameters["enable_enter_submit"].default is True
        assert sig.parameters["show_internal_save"].default is False

    def test_uses_use_viewmodel_external_mode(self) -> None:
        source = form_module.__file__
        assert source  # 触发模块加载
        import pathlib

        text = pathlib.Path(source).read_text(encoding="utf-8")
        assert "use_viewmodel(vm=vm)" in text
        assert "ft.context.page" in text
        assert "page.services.append" in text


# ============================================================================
# adapter_kind 条件字段
# ============================================================================


class TestAdapterConditionalFields:
    def test_litellm_shows_api_fields(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_LITELLM))
        _, _, result, _ = _render_form(state, editor_step=1)

        assert _text_field_labels(result) >= {LABEL_PROVIDER, LABEL_MODEL, LABEL_API_BASE, LABEL_CREDENTIAL}
        # gguf 专属路径不出现
        assert _text_field_labels(result).isdisjoint(
            {LABEL_DIALOG_PATH, LABEL_ENCODER_PATH, LABEL_HEAD_PATH, LABEL_TOKENIZER_PATH}
        )

    def test_gguf_chat_shows_dialog_file(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_GGUF_CHAT))
        _, _, result, _ = _render_form(state, editor_step=1)

        labels = _text_field_labels(result)
        assert LABEL_DIALOG_PATH in labels
        assert labels.isdisjoint({LABEL_PROVIDER, LABEL_MODEL, LABEL_API_BASE, LABEL_CREDENTIAL})

    def test_gguf_sentiment_shows_three_paths(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_GGUF_SENTIMENT))
        _, _, result, _ = _render_form(state, editor_step=1)

        labels = _text_field_labels(result)
        assert labels >= {LABEL_ENCODER_PATH, LABEL_HEAD_PATH, LABEL_TOKENIZER_PATH}
        assert LABEL_DIALOG_PATH not in labels
        assert LABEL_PROVIDER not in labels

    def test_adapter_selector_always_present(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_GGUF_CHAT))
        _, _, result, _ = _render_form(state, editor_step=1)
        controls = _walk_controls(result)
        assert any(isinstance(c, ft.Dropdown) and c.label == LABEL_ADAPTER for c in controls)
        assert any(isinstance(c, ft.Switch) and c.label == LABEL_ENABLED for c in controls)


class TestBillingPricingFields:
    def test_llm_pricing_shows_input_output(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(
            draft=_draft(
                adapter_kind=ADAPTER_LITELLM,
                billing_mode="estimate_cost",
                billing_unit="llm_tokens",
            )
        )
        _, _, result, _ = _render_form(state, editor_step=2)

        labels = _text_field_labels(result)
        assert labels >= {LABEL_INPUT_PRICE, LABEL_OUTPUT_PRICE}
        assert LABEL_ENCODER_PRICE not in labels

    def test_encoder_pricing_shows_only_encoder_price(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(
            draft=_draft(
                adapter_kind=ADAPTER_GGUF_SENTIMENT,
                billing_mode="estimate_cost",
                billing_unit="encoder_input_tokens",
            )
        )
        _, _, result, _ = _render_form(state, editor_step=2)

        assert _find_text_field(result, LABEL_ENCODER_PRICE) is not None
        assert _find_text_field(result, LABEL_INPUT_PRICE) is None
        assert _find_text_field(result, LABEL_OUTPUT_PRICE) is None

    def test_usage_only_hides_price_fields(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_LITELLM, billing_mode="usage_only"))
        _, _, result, _ = _render_form(state, editor_step=2)

        assert _find_radio_group(result) is not None
        assert _find_text_field(result, LABEL_INPUT_PRICE) is None
        assert _find_text_field(result, LABEL_OUTPUT_PRICE) is None


# ============================================================================
# enable_enter_submit / show_internal_save
# ============================================================================


class TestEnterSubmit:
    def test_enter_submit_bound_when_enabled(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, page, result, _ = _render_form(enable_enter_submit=True)
        name = _require_text_field(result, LABEL_NAME)
        on_submit = name.on_submit
        assert callable(on_submit)

        run_task = cast(MagicMock, cast(Any, page).run_task)
        run_task.reset_mock()
        _invoke(on_submit, _make_event())
        run_task.assert_called_once_with(vm.save)

    def test_enter_submit_none_when_disabled(self, mock_i18n_state, mock_app_colors_state) -> None:
        _, _, result, _ = _render_form(enable_enter_submit=False)
        name = _require_text_field(result, LABEL_NAME)
        assert name.on_submit is None


class TestInternalSave:
    def test_no_save_button_when_hidden(self, mock_i18n_state, mock_app_colors_state) -> None:
        _, _, result, _ = _render_form(show_internal_save=False)
        assert _find_buttons(result, ft.Icons.SAVE) == []

    def test_save_button_rendered_and_schedules_save(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, page, result, _ = _render_form(show_internal_save=True)
        buttons = _find_buttons(result, ft.Icons.SAVE)
        assert len(buttons) == 1

        run_task = cast(MagicMock, cast(Any, page).run_task)
        run_task.reset_mock()
        _invoke(buttons[0].on_click, _make_event())
        run_task.assert_called_once_with(vm.save)


# ============================================================================
# 凭据：不回显 + 留空 ≠ 清除
# ============================================================================


class TestCredentialHandling:
    def test_credential_not_echoed(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_LITELLM, credential_api_key="secret"))
        _, _, result, _ = _render_form(state, editor_step=1)
        credential = _require_text_field(result, LABEL_CREDENTIAL)
        # draft 中即便有值，输入框仍为空（不回显）
        assert credential.value == ""

    def test_empty_credential_change_keeps_existing(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form(editor_step=1)
        credential = _require_text_field(result, LABEL_CREDENTIAL)

        _invoke(credential.on_change, _make_event(""))
        vm.update_credential.assert_called_once_with("")
        # 留空不触发显式清除
        vm.update_credential_cleared.assert_not_called()


# ============================================================================
# FilePicker 生命周期
# ============================================================================


class TestFilePickerLifecycle:
    def test_setup_registers_picker_once(self, mock_i18n_state, mock_app_colors_state) -> None:
        _, page, _, _ = _render_form(editor_step=1)
        pickers = [s for s in page.services if isinstance(s, ft.FilePicker)]
        assert len(pickers) == 1

    def test_cleanup_removes_picker(self, mock_i18n_state, mock_app_colors_state) -> None:
        _, page, _, component = _render_form(editor_step=1)
        assert len([s for s in page.services if isinstance(s, ft.FilePicker)]) == 1

        run_unmount_effects(component)
        assert [s for s in page.services if isinstance(s, ft.FilePicker)] == []


# ============================================================================
# 字段事件 → vm 命令
# ============================================================================


class TestFieldEvents:
    def test_adapter_select_calls_update_adapter_kind(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form(editor_step=1)
        dd = _require_dropdown(result, LABEL_ADAPTER)
        _invoke(dd.on_select, _make_event(ADAPTER_GGUF_CHAT))
        vm.update_adapter_kind.assert_called_once_with(ADAPTER_GGUF_CHAT)

    def test_enabled_switch_calls_update_enabled(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form(editor_step=1)
        sw = _require_switch(result, LABEL_ENABLED)
        _invoke(sw.on_change, _make_event(False))
        vm.update_enabled.assert_called_once_with(False)

    def test_name_change_calls_update_name(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form(editor_step=1)
        name = _require_text_field(result, LABEL_NAME)
        _invoke(name.on_change, _make_event("conn-a"))
        vm.update_name.assert_called_once_with("conn-a")

    def test_billing_mode_change_calls_update_billing_mode(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form(editor_step=2)
        group = _require_radio_group(result)
        _invoke(group.on_change, _make_event("estimate_cost"))
        vm.update_billing_mode.assert_called_once_with("estimate_cost")

    def test_timeout_change_calls_update_timeout(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, result, _ = _render_form(editor_step=1)
        timeout = _require_text_field(result, LABEL_TIMEOUT)
        _invoke(timeout.on_change, _make_event("600"))
        vm.update_timeout_seconds.assert_called_once_with("600")

    def test_gguf_dialog_change_calls_update_dialog_path(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(draft=_draft(adapter_kind=ADAPTER_GGUF_CHAT))
        vm, _, result, _ = _render_form(state, editor_step=1)
        dialog = _require_text_field(result, LABEL_DIALOG_PATH)
        _invoke(dialog.on_change, _make_event("/models/chat.gguf"))
        vm.update_dialog_model_path.assert_called_once_with("/models/chat.gguf")


# ============================================================================
# 校验展示
# ============================================================================


class TestValidationDisplay:
    def test_validation_errors_rendered(self, mock_i18n_state, mock_app_colors_state) -> None:
        state = ModelConnectionsState(validation_errors=("model_connection_error_name_required",))
        _, _, result, _ = _render_form(state, editor_step=1)
        texts = [c.value for c in _walk_controls(result) if isinstance(c, ft.Text)]
        assert "model_connection_error_name_required" in texts


# ============================================================================
# 模块级 _select_file 纯函数
# ============================================================================


class TestSelectFileHelper:
    def test_select_file_normal_updates(self) -> None:
        picker = MagicMock(spec=ft.FilePicker)
        fake_file = MagicMock(path="/models/chat.gguf")
        picker.pick_files = MagicMock(return_value=_async_return([fake_file]))
        update = MagicMock()

        asyncio.run(_select_file(picker, update, ["gguf"]))

        update.assert_called_once_with("/models/chat.gguf")

    def test_select_file_cancel_skips_update(self) -> None:
        picker = MagicMock(spec=ft.FilePicker)
        picker.pick_files = MagicMock(return_value=_async_return(None))
        update = MagicMock()

        asyncio.run(_select_file(picker, update, ["gguf"]))

        update.assert_not_called()

    def test_select_file_error_logs_and_no_raise(self) -> None:
        picker = MagicMock(spec=ft.FilePicker)

        async def _raise() -> None:
            raise RuntimeError("picker unavailable")

        picker.pick_files = MagicMock(side_effect=_raise)
        update = MagicMock()

        with patch.object(form_module, "logger") as mock_logger:
            asyncio.run(_select_file(picker, update, ["gguf"]))

        assert mock_logger.error.call_count == 1
        assert mock_logger.error.call_args.args[0] == "[ModelConnectionForm] File pick failed: %s"
        update.assert_not_called()


# ============================================================================
# VM 订阅生命周期（外部 VM 模式）
# ============================================================================


class TestVMLifecycle:
    def test_mount_subscribes(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, _, _ = _render_form(editor_step=1)
        assert len(vm._subscribers) > 0

    def test_unmount_unsubscribes(self, mock_i18n_state, mock_app_colors_state) -> None:
        vm, _, _, component = _render_form(editor_step=1)
        run_unmount_effects(component)
        assert len(vm._subscribers) == 0
