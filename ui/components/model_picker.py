"""model_picker — 共享「搜索+分组+回记」模型选择组件 (AI-01 §3.1c D).

取代原普通 ``Dropdown`` 的模型选择：litellm 官方目录很大（openai 231 项），
普通下拉装不下。本组件提供：

- 顶部 ``TextField``：占位「搜索模型或供应商」，``on_change -> vm.update_model_query``；
  已选态（无关键字且已选）回显「供应商名 / 模型 id」，focus 进入选择态。
- 下方结果面板（``ft.Column``，随 ``query`` 与打开态渲染）：
  - **空 query（浏览模式）**：「最近使用」置顶分组 + 「供应商分组折叠」（每组首行 =
    provider icon+name+模型数，可点展开该 provider 的模型行）。
  - **有 query（搜索模式）**：``search_models`` 拍平列表，每行
    ``[icon] 供应商名 · 模型id (context)``；点击一行为**一键设定 provider + model**。

MVVM 声明式（CLAUDE.md §3.2 / §3.3，与 LLMConfigPanel/FailoverConfigPanel 同级共享组件）：
- @ft.component 函数组件，无 class 子类、不持有业务状态。
- ``use_viewmodel(vm=vm)`` 订阅 VM state（外部 VM 模式，VM 生命周期由消费方管理）。
- 渲染层仅做纯展示，无 logger/print 副作用（R16）。
- context=0（litellm 未声明）隐藏 ``(context)`` 后缀（R21，不伪装为 0）。

消费方契约：将选中结果通过 ``on_select(provider_id, model_id)`` 回调写回其自身状态
（failover 另需同步 dialog_provider，见 §3.1c-review #5）。
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_on_change,
    safe_on_click,
    safe_on_focus,
    safe_controls,
)
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.model_picker_view_model import ModelPickerViewModel, ModelRow, ProviderGroup

logger = logging.getLogger(__name__)

# NOTE(lazy): 结果面板固定高度 + 内部滚动，限制面板高度（对齐 watchlist_add_dialog）。
# ceiling: litellm 目录单供应商半随供应商展开，模型最多流畅呈现百余条.
# upgrade: 需要分页/虚拟滚动时重估。
_RESULT_AREA_HEIGHT = 220
_ICON_IMG_WIDTH = 18
_ICON_IMG_HEIGHT = 18


def _build_context_suffix(context: int) -> str:
    """context 后缀。context<=0（litellm 未声明/无法计量）隐藏，不显示 0 误导（R21）。"""
    return f" ({context:,})" if context > 0 else ""


def _icon_image(icon_path: str) -> ft.Control:
    """供应商 icon 图片控件（路径不存在时 file 不存在会显示占位，不崩 UI）。"""
    return ft.Image(
        src=icon_path,
        width=_ICON_IMG_WIDTH,
        height=_ICON_IMG_HEIGHT,
        fit=ft.BoxFit.CONTAIN,
    )


def _build_row_content(row: ModelRow, *, highlight_model: bool = False) -> list[ft.Control]:
    """单条模型/供应商行内容（共享于搜索行与浏览行）。"""
    primary_text = row.model_id if highlight_model else row.provider_name
    secondary_text = row.provider_name if highlight_model else row.model_id
    return [
        _icon_image(row.icon),
        ft.Column(
            [
                ft.Text(
                    primary_text,
                    size=AppStyles.FONT_SIZE_BODY,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(
                    f"{secondary_text}{_build_context_suffix(row.context)}",
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            spacing=0,
            expand=True,
        ),
    ]


def _build_select_row(
    row: ModelRow,
    on_select: Callable[[ModelRow], None],
    *,
    highlight_model: bool = False,
) -> ft.Container:
    """可点击的一行：点击即选中（search 行高亮模型 id，浏览 provider 行高亮供应商名）。"""
    return ft.Container(
        content=ft.Row(
            _build_row_content(row, highlight_model=highlight_model),
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        border_radius=6,
        bgcolor=AppColors.PRIMARY_DARK,
        on_click=safe_on_click(lambda _e: on_select(row)),
    )


def _build_group_header(
    group: ProviderGroup,
    expanded: bool,
    on_toggle: Callable[[str], None],
) -> ft.Container:
    """供应商分组折叠头：icon + name + 模型数 + 展开箭头，点击折叠/展开。"""
    return ft.Container(
        content=ft.Row(
            [
                _icon_image(group.icon),
                ft.Text(
                    group.provider_name,
                    size=AppStyles.FONT_SIZE_BODY,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                    expand=True,
                ),
                ft.Text(
                    str(group.model_count),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Icon(
                    ft.Icons.EXPAND_MORE if expanded else ft.Icons.EXPAND_LESS,
                    color=AppColors.TEXT_SECONDARY,
                    size=AppStyles.FONT_SIZE_BODY,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        border_radius=6,
        on_click=safe_on_click(lambda _e: on_toggle(group.provider_id)),
    )


def _build_browse_panel(
    state,
    on_select: Callable[[ModelRow], None],
    on_toggle: Callable[[str], None],
) -> list[ft.Control]:
    """浏览模式（空 query）：
    - 模型模式：最近使用置顶 + 供应商分组折叠；
    - provider-only 模式：仅供应商行列表（选中即设定供应商）。
    """
    if state.provider_only:
        if not state.provider_options:
            return [
                ft.Text(
                    I18n.get("model_picker_browse_empty"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_HINT,
                    italic=True,
                )
            ]
        return [_build_select_row(row, on_select, highlight_model=False) for row in state.provider_options]

    controls: list[ft.Control] = []

    if state.recent:
        controls.append(
            ft.Text(
                I18n.get("model_picker_section_recent"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            )
        )
        controls.extend(_build_select_row(row, on_select, highlight_model=True) for row in state.recent)

    for group in state.groups:
        expanded = group.provider_id in state.expanded
        controls.append(_build_group_header(group, expanded, on_toggle))
        if expanded:
            controls.extend(_build_select_row(row, on_select, highlight_model=True) for row in group.models)

    if not controls:
        controls.append(
            ft.Text(
                I18n.get("model_picker_browse_empty"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        )
    return controls


def _build_search_panel(state, on_select: Callable[[ModelRow], None]) -> list[ft.Control]:
    """搜索模式（有 query）：search_models 拍平列表。"""
    if not state.search_results:
        return [
            ft.Text(
                I18n.get("model_picker_no_result"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        ]
    return [_build_select_row(row, on_select, highlight_model=True) for row in state.search_results]


@ft.component
def ModelPicker(
    vm: ModelPickerViewModel,
    *,
    on_select: Callable[[str, str], None] | None = None,
    text_field_width: float | None = None,
    provider_scope: str | None = None,
    provider_only: bool = False,
) -> ft.Control:
    """共享模型选择组件（搜索 + 分组浏览 + 最近回记 + 级联过滤 + provider-only）。

    Args:
        vm: 由消费方实例化的 ModelPickerViewModel（外部 VM 模式）。
        on_select: 选中一行时回调 ``(provider_id, model_id)``，消费方写回自身状态。
            provider-only 模式下 model_id 恒为空串，消费方按「纯供应商选择」处理。
        text_field_width: 顶部 TextField 宽度（None 用 Panel 默认宽度）。
        provider_scope: 级联过滤作用域（非 None = 浏览模式仅展示该供应商模型分组，
            经 use_effect 同步到 ``vm.set_provider_scope``；搜索仍跨供应商，
            命中其他供应商模型时经 on_select 通知消费方同步供应商下拉——双向联动）。
        provider_only: provider-only 模式（与模型选择共用组件，仅行粒度不同：
            浏览为供应商列表、搜索按供应商名匹配，选中回 ``(provider_id, "")``）。
    """
    # --- Subscribe to VM state changes (外部 VM 模式) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- 级联过滤：外部 scope 变化 → VM 同步（只读 prop→VM 单向流） ---
    def _sync_scope() -> None:
        vm.set_provider_scope(provider_scope or "")

    ft.use_effect(_sync_scope, dependencies=[provider_scope])

    # --- provider-only：模式变化 → VM 同步（与模型选择共用同一组件） ---
    def _sync_provider_only() -> None:
        vm.set_provider_only(provider_only)

    ft.use_effect(_sync_provider_only, dependencies=[provider_only])

    # --- 挂载时预载 litellm 目录（R16：惰性 import 十秒级，必须 offload） ---
    def _load_catalog() -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.ensure_catalog_loaded)
        except RuntimeError:
            logger.debug("[ModelPicker] page not available for ensure_catalog_loaded")

    ft.use_effect(_load_catalog, dependencies=[])

    # 从 provider_id 解析显示名：已选态回显「供应商名 / 模型 id」
    from utils.llm_providers import LLM_PROVIDERS

    selected_provider_name = str(LLM_PROVIDERS.get(state.selected_provider, {}).get("name", state.selected_provider))

    def _on_search_change(e: ft.ControlEvent) -> None:
        """输入变化 → vm.update_model_query（目录已缓存，过滤为纯内存，主线程可接受）。"""
        vm.update_model_query(get_control_value(e.control, ft.TextField))

    def _on_focus(_e: ft.ControlEvent) -> None:
        """聚焦进入选择态：清空已选回显关键字以展开选择面板。"""
        if not state.query and state.selected_model:
            vm.update_model_query("")

    def _handle_select(row: ModelRow) -> None:
        vm.select_model(row)
        if on_select is not None:
            on_select(row.provider_id, row.model_id)

    # --- Selected display text (only when query empty & a selection exists) ---
    # provider-only 模式无模型粒度：以供应商名回显（否则供应商选择器恒空白）。
    if not state.query and (state.selected_model or (state.provider_only and state.selected_provider)):
        display = (
            f"{selected_provider_name} / {state.selected_model}" if state.selected_model else selected_provider_name
        )
    else:
        display = state.query

    search_field = ft.TextField(
        value=display,
        hint_text=I18n.get("model_picker_search_hint"),
        dense=True,
        expand=True,
        width=text_field_width,
        on_change=safe_on_change(_on_search_change),
        on_focus=safe_on_focus(_on_focus),
        prefix_icon=ft.Icons.SEARCH if ft.Icons else None,
    )

    # --- Result panel ---
    if state.searching:
        result_controls: list[ft.Control] = [
            ft.Row(
                [ft.ProgressRing(width=16, height=16), ft.Text(I18n.get("model_picker_searching"))],
                spacing=8,
            )
        ]
        result_visible = True
    elif state.query.strip():
        result_controls = _build_search_panel(state, _handle_select)
        result_visible = True
    else:
        result_controls = _build_browse_panel(state, _handle_select, vm.toggle_group)
        # provider-only 浏览以供应商行列表判定可见（无 recent/groups）
        result_visible = (
            bool(state.recent) or bool(state.groups) or (state.provider_only and bool(state.provider_options))
        )

    result_panel = ft.Container(
        content=ft.Column(safe_controls(result_controls), scroll=ft.ScrollMode.AUTO, spacing=2),
        height=_RESULT_AREA_HEIGHT,
        border=ft.Border.all(1, AppColors.DIVIDER),
        border_radius=8,
        padding=ft.Padding.all(4),
        visible=result_visible,
    )

    return ft.Column(
        [search_field, result_panel],
        spacing=6,
        tight=True,
    )


__all__ = ["ModelPicker"]
