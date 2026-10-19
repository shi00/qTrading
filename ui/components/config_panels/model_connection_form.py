"""ModelConnectionForm — 「模型连接」共用表单组件（S5 双入口, reviews/模型统一.md §11.2/§11.3）。

职责（§11.2 / §11.3）：
- 同一连接字段组（协议下拉 / 启用开关 / 计量互斥选项 / 时限·单价·并发数字输入）
  供创建向导与编辑入口共用，不重走创建向导。
- 按 ``adapter_kind`` 条件显示：``litellm_chat`` 显 provider / model / 地址 / 凭据；
  ``gguf_chat`` 显对话文件；``gguf_sentiment`` 显编码器 / head / tokenizer 三路径；
  分类费用型（``encoder_input_tokens``）只显 encoder 输入价。
- 三步创建（①连接信息 ②统计方式 ③检查并保存）由宿主经 ``editor_step`` 控制，
  组件按步骤决定显示哪些分组，前后切换不丢输入（草稿由 VM 持有）。
- 凭据不回显：输入框初始为空 + 占位「留空保持现有」；留空 ≠ 显式清除（清除走单独命令）。
- 地址不擅自改 host / 端口 / ``/v1``（原样透传，仅经 VM 命令写回）。
- FilePicker 注册 / cleanup 沿 ``page.services`` 生命周期（§11.2：不复制第二套实现）。
- Enter 提交由 ``enable_enter_submit`` 控制；内部保存按钮由 ``show_internal_save`` 控制
  （向导传 False，由宿主按钮推进）。

MVVM 声明式（CLAUDE.md §3.2 / §3.3）：
- ``@ft.component`` 函数组件，无 class 子类 / 生命周期回调 / 手动 update。
- ``use_viewmodel(vm=vm)`` 订阅外部 VM（生命周期由宿主管理）；组件只读 state、调 vm 命令。
- 组件不得直接读写 ConfigHandler / 凭据文件 / 推进宿主。

R2：async 文件选择回调显式 ``except asyncio.CancelledError: raise``。
R23：颜色用 ``AppColors.*``、字号 / 宽度 / 间距用 ``AppStyles.*``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_on_change,
    safe_on_click,
)
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
from ui.viewmodels.model_connections_view_model import (
    ADAPTER_GGUF_CHAT,
    ADAPTER_GGUF_SENTIMENT,
    ADAPTER_LITELLM,
    ModelConnectionsViewModel,
)
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

# --- 适配器选项 ---
_ADAPTER_OPTIONS = (ADAPTER_LITELLM, ADAPTER_GGUF_CHAT, ADAPTER_GGUF_SENTIMENT)
_BILLING_USAGE_ONLY = "usage_only"
_BILLING_ESTIMATE_COST = "estimate_cost"
_PRICE_UNIT_ENCODER = "encoder_input_tokens"
_GGUF_EXTENSIONS = ["gguf"]

# --- i18n key（字段 label / 按钮 / 提示；labels 供条件渲染断言复用） ---
KEY_NAME = "model_connection_field_name"
KEY_ADAPTER = "model_connection_field_adapter"
KEY_ENABLED = "model_connection_field_enabled"
KEY_PROVIDER = "model_connection_field_provider"
KEY_MODEL = "model_connection_field_model"
KEY_API_BASE = "model_connection_field_api_base"
KEY_CREDENTIAL = "model_connection_field_credential"
KEY_CREDENTIAL_HINT = "model_connection_field_credential_hint"
KEY_TIMEOUT = "model_connection_field_timeout"
KEY_CONCURRENCY = "model_connection_field_concurrency"
KEY_INPUT_PRICE = "model_connection_field_input_price"
KEY_OUTPUT_PRICE = "model_connection_field_output_price"
KEY_ENCODER_PRICE = "model_connection_field_encoder_price"
KEY_DIALOG_PATH = "model_connection_field_dialog_model_path"
KEY_ENCODER_PATH = "model_connection_field_encoder_model_path"
KEY_HEAD_PATH = "model_connection_field_head_path"
KEY_TOKENIZER_PATH = "model_connection_field_tokenizer_path"
KEY_BILLING_USAGE_ONLY = "model_connection_billing_usage_only"
KEY_BILLING_ESTIMATE_COST = "model_connection_billing_estimate_cost"
KEY_SECTION_BILLING = "model_connection_section_billing"
KEY_SECTION_REVIEW = "model_connection_section_review"
KEY_BTN_SELECT_FILE = "model_connection_btn_select_file"
KEY_BTN_SAVE = "model_connection_btn_save"
KEY_BTN_CLEAR_CREDENTIAL = "model_connection_btn_clear_credential"


# ---------------------------------------------------------------------------
# 事件处理器 / 副作用（模块级，便于单测与复用）
# ---------------------------------------------------------------------------


async def _select_file(
    file_picker: ft.FilePicker,
    update: Callable[[str], None],
    allowed_extensions: list[str] | None,
) -> None:
    """打开文件选择器并把所选路径写回 VM 命令（async 原生原语，按其 await 模型执行）。"""
    try:
        result = await file_picker.pick_files(
            allowed_extensions=allowed_extensions,
            dialog_title=I18n.get(KEY_BTN_SELECT_FILE),
        )
        if result:
            update(result[0].path or "")
    except asyncio.CancelledError:
        raise  # R2
    except Exception as e:
        logger.error(
            "[ModelConnectionForm] File pick failed: %s",
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )


def _on_select_file_click_factory(
    file_picker: ft.FilePicker | None,
    update: Callable[[str], None],
    allowed_extensions: list[str] | None,
) -> Callable[[ft.ControlEvent], None]:
    """文件选择按钮 on_click：经 page.run_task 调度 _select_file。"""

    def _on_click(_e: ft.ControlEvent) -> None:
        if file_picker is None:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_select_file, file_picker, update, allowed_extensions)
        except RuntimeError:
            logger.debug("[ModelConnectionForm] page not available for file pick")

    return _on_click


def _on_save_click_factory(
    vm: ModelConnectionsViewModel,
) -> Callable[[ft.ControlEvent], None]:
    """保存按钮 / Enter 提交 on_click / on_submit：经 page.run_task 调度 vm.save。"""

    def _on_save(_e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.save)
        except RuntimeError:
            logger.debug("[ModelConnectionForm] page not available for save")

    return _on_save


# ---------------------------------------------------------------------------
# 组件
# ---------------------------------------------------------------------------


@ft.component
def ModelConnectionForm(
    vm: ModelConnectionsViewModel,
    *,
    editor_step: int,
    compact: bool = False,
    enable_enter_submit: bool = True,
    show_internal_save: bool = False,
) -> ft.Control:
    """同一连接字段组的共用表单（创建向导 + 编辑入口）。

    Args:
        vm: 宿主经 ``use_viewmodel`` factory 创建后传入的 ModelConnectionsViewModel
            （外部 VM 模式，生命周期由宿主管理）。
        editor_step: 创建步骤（1 连接信息 / 2 统计方式 / 3 检查并保存）；编辑态（已选连接）
            直接展开连接 + 统计分组，不受步骤折叠影响。
        compact: 紧凑布局（向导内嵌）。
        enable_enter_submit: 字段 Enter 是否触发表单提交（向导传 False 禁隐式保存）。
        show_internal_save: 是否渲染组件内部保存按钮（向导传 False，由宿主按钮推进）。
    """
    state, _ = use_viewmodel(vm=vm)

    # i18n / 主题状态源订阅：locale 或主题切换自动重渲染。
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- FilePicker 生命周期：沿 page.services 注册 / cleanup（§11.2） ---
    file_picker = ft.use_ref(ft.FilePicker).current

    def _setup_file_picker() -> None:
        try:
            page = ft.context.page
            if page is not None and file_picker is not None and file_picker not in page.services:
                page.services.append(file_picker)
        except RuntimeError:
            logger.debug("[ModelConnectionForm] page not available for FilePicker setup")

    def _cleanup_file_picker() -> None:
        try:
            page = ft.context.page
            if page is not None and file_picker in page.services:
                page.services.remove(file_picker)
        except RuntimeError:
            # 组件卸载时 page 上下文可能已不可用，FilePicker 随之销毁，无需再做清理
            pass

    ft.use_effect(_setup_file_picker, dependencies=[], cleanup=_cleanup_file_picker)

    draft = state.draft
    is_editing = bool(state.selected_connection_id)
    show_connection = is_editing or editor_step <= 1
    show_billing = is_editing or editor_step >= 2
    show_review = (not is_editing) and editor_step >= 3

    submit_handler = safe_on_change(_on_save_click_factory(vm)) if enable_enter_submit else None

    # --- 通用字段构造器 ---
    def _text_field(
        label_key: str,
        value: str,
        update: Callable[[str], None],
        *,
        number: bool = False,
        password: bool = False,
        hint: str | None = None,
    ) -> ft.TextField:
        return ft.TextField(
            label=I18n.get(label_key),
            value=value,
            width=AppStyles.CONTROL_WIDTH_SM if number else AppStyles.CONTROL_WIDTH_MD,
            keyboard_type=ft.KeyboardType.NUMBER if number else None,
            password=password,
            can_reveal_password=password,
            hint_text=hint,
            on_change=safe_on_change(lambda e: update(get_control_value(e.control, ft.TextField))),
            on_submit=submit_handler,
        )

    def _path_row(
        label_key: str,
        value: str,
        update: Callable[[str], None],
        allowed_extensions: list[str] | None,
    ) -> ft.Row:
        field = ft.TextField(
            label=I18n.get(label_key),
            value=value,
            expand=True,
            on_change=safe_on_change(lambda e: update(get_control_value(e.control, ft.TextField))),
            on_submit=submit_handler,
        )
        button = ft.OutlinedButton(
            content=I18n.get(KEY_BTN_SELECT_FILE),
            icon=ft.Icons.FOLDER_OPEN,
            on_click=safe_on_click(_on_select_file_click_factory(file_picker, update, allowed_extensions)),
        )
        return ft.Row(
            [field, button],
            spacing=AppStyles.SPACING_SM,
            vertical_alignment=ft.CrossAxisAlignment.END,
        )

    # --- 连接字段组（名称始终可见；其余按步骤 / adapter_kind 条件渲染） ---
    name_field = anchored(EIDS.MODEL.NAME_INPUT, _text_field(KEY_NAME, draft.name, vm.update_name))

    adapter_dropdown = anchored(
        EIDS.MODEL.ADAPTER_DROPDOWN,
        ft.Dropdown(
            label=I18n.get(KEY_ADAPTER),
            value=draft.adapter_kind,
            options=[ft.dropdown.Option(kind) for kind in _ADAPTER_OPTIONS],
            width=AppStyles.CONTROL_WIDTH_MD,
            on_select=safe_on_change(lambda e: vm.update_adapter_kind(get_control_value(e.control, ft.Dropdown))),
        ),
    )
    enabled_switch = ft.Switch(
        label=I18n.get(KEY_ENABLED),
        value=draft.enabled,
        on_change=safe_on_change(lambda e: vm.update_enabled(get_control_value(e.control, ft.Switch))),
    )

    adapter_fields: list[ft.Control] = []
    if draft.adapter_kind == ADAPTER_LITELLM:
        credential_field = anchored(
            EIDS.MODEL.CREDENTIAL_INPUT,
            ft.TextField(
                label=I18n.get(KEY_CREDENTIAL),
                value="",  # 凭据不回显
                password=True,
                can_reveal_password=True,
                hint_text=I18n.get(KEY_CREDENTIAL_HINT),
                on_change=safe_on_change(lambda e: vm.update_credential(get_control_value(e.control, ft.TextField))),
                on_submit=submit_handler,
            ),
        )
        credential_row: list[ft.Control] = [credential_field]
        if is_editing and draft.credential_ref:
            credential_row.append(
                ft.TextButton(
                    content=I18n.get(KEY_BTN_CLEAR_CREDENTIAL),
                    on_click=safe_on_click(lambda e: vm.update_credential_cleared(True)),
                )
            )
        adapter_fields = [
            _text_field(KEY_PROVIDER, draft.provider, vm.update_provider),
            anchored(EIDS.MODEL.MODEL_INPUT, _text_field(KEY_MODEL, draft.model, vm.update_model)),
            # 地址原样透传，不擅自改 host / 端口 / /v1（§11.3）
            anchored(EIDS.MODEL.API_BASE_INPUT, _text_field(KEY_API_BASE, draft.api_base, vm.update_api_base)),
            ft.Row(credential_row, spacing=AppStyles.SPACING_SM),
        ]
    elif draft.adapter_kind == ADAPTER_GGUF_CHAT:
        adapter_fields = [
            _path_row(KEY_DIALOG_PATH, draft.dialog_model_path, vm.update_dialog_model_path, _GGUF_EXTENSIONS),
        ]
    elif draft.adapter_kind == ADAPTER_GGUF_SENTIMENT:
        adapter_fields = [
            _path_row(KEY_ENCODER_PATH, draft.encoder_model_path, vm.update_encoder_model_path, None),
            _path_row(KEY_HEAD_PATH, draft.head_path, vm.update_head_path, None),
            _path_row(KEY_TOKENIZER_PATH, draft.tokenizer_path, vm.update_tokenizer_path, None),
        ]

    connection_group: list[ft.Control] = [
        ft.Row(
            [adapter_dropdown, enabled_switch],
            spacing=AppStyles.SPACING_SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        *adapter_fields,
        ft.Row(
            [
                _text_field(KEY_TIMEOUT, draft.timeout_seconds, vm.update_timeout_seconds, number=True),
                _text_field(KEY_CONCURRENCY, draft.concurrency_limit, vm.update_concurrency_limit, number=True),
            ],
            spacing=AppStyles.SPACING_SM,
        ),
    ]

    # --- 统计方式分组（计量互斥选项 + 条件单价） ---
    billing_group: list[ft.Control] = [
        ft.Text(
            I18n.get(KEY_SECTION_BILLING),
            size=AppStyles.FONT_SIZE_TITLE,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        ft.RadioGroup(
            value=draft.billing_mode,
            on_change=safe_on_change(lambda e: vm.update_billing_mode(get_control_value(e.control, ft.RadioGroup))),
            content=ft.Column(
                [
                    ft.Radio(value=_BILLING_USAGE_ONLY, label=I18n.get(KEY_BILLING_USAGE_ONLY)),
                    ft.Radio(value=_BILLING_ESTIMATE_COST, label=I18n.get(KEY_BILLING_ESTIMATE_COST)),
                ],
                spacing=AppStyles.SPACING_XS,
                tight=True,
            ),
        ),
    ]
    if draft.billing_mode == _BILLING_ESTIMATE_COST:
        is_encoder_priced = draft.adapter_kind == ADAPTER_GGUF_SENTIMENT or draft.billing_unit == _PRICE_UNIT_ENCODER
        if is_encoder_priced:
            # 分类费用型只显 encoder 输入价
            billing_group.append(_text_field(KEY_ENCODER_PRICE, draft.encoder_price_per_m, vm.update_encoder_price))
        else:
            billing_group.extend(
                [
                    _text_field(KEY_INPUT_PRICE, draft.input_price_per_m, vm.update_input_price),
                    _text_field(KEY_OUTPUT_PRICE, draft.output_price_per_m, vm.update_output_price),
                ]
            )

    # --- 检查并保存分组（仅创建第 3 步） ---
    review_group: list[ft.Control] = []
    if show_review:
        review_group = [
            ft.Text(
                I18n.get(KEY_SECTION_REVIEW),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Text(
                f"{I18n.get(KEY_NAME)}: {draft.name}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            ),
            ft.Text(
                f"{I18n.get(KEY_ADAPTER)}: {draft.adapter_kind}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            ),
        ]

    # --- 校验展示（非空时渲染；不逐键报错，保留其余输入） ---
    validation_group: list[ft.Control] = []
    if state.validation_errors:
        validation_group = [
            ft.Row(
                [
                    ft.Icon(ft.Icons.ERROR, color=AppColors.ERROR, size=AppStyles.FONT_SIZE_TITLE),
                    ft.Text(
                        I18n.get(state.validation_errors[0]),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.ERROR,
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        ]
        validation_group.extend(
            ft.Text(I18n.get(err), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.ERROR)
            for err in state.validation_errors[1:]
        )

    # --- 内部保存按钮（向导传 False 隐藏，由宿主按钮推进） ---
    action_group: list[ft.Control] = []
    if show_internal_save:
        action_group = [
            ft.Button(
                content=I18n.get(KEY_BTN_SAVE),
                icon=ft.Icons.SAVE,
                style=AppStyles.primary_button(),
                on_click=safe_on_click(_on_save_click_factory(vm)),
            )
        ]

    controls: list[ft.Control] = [name_field]
    if show_connection:
        controls.extend(connection_group)
    if show_billing:
        controls.extend(billing_group)
    if show_review:
        controls.extend(review_group)
    controls.extend(validation_group)
    controls.extend(action_group)

    form_content = ft.Column(controls, spacing=AppStyles.SPACING_SM, tight=True)

    if compact:
        return ft.Container(
            content=form_content,
            width=AppStyles.CONTROL_WIDTH_LG + AppStyles.CONTROL_WIDTH_SM + AppStyles.CONTROL_WIDTH_XS,
            alignment=ft.Alignment.CENTER,
        )
    return ft.Container(content=form_content)


__all__ = ["ModelConnectionForm"]
