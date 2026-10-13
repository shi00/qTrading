"""NewsSentimentConfigPanel — 新闻情绪打分配置面板 (N5-4).

外部 VM 模式声明式组件 (CLAUDE.md §3.2 MVVM)：消费 AIBrainSettingsViewModel
的 ``news_sentiment_*`` state 字段与 setter 命令，本组件不持有业务状态。

独立成组件的原因：``ai_brain_tab`` 的契约测试禁止 ``ft.use_ref``（test_no_use_ref_cache），
而 FilePicker 生命周期需要 ``ft.use_ref`` + ``ft.use_effect``——照
``local_model_config_panel.py`` 的既有范式：
- ``ft.use_ref`` 持久化 FilePicker 实例（跨重渲染复用，三按钮共用单实例，
  ``pick_files`` 的扩展名为调用参数，模态对话框天然串行）；
- ``ft.use_effect`` 注册到 ``page.services``，cleanup 时移除；
- 点击经 ``page.run_task`` 调度 async pick（R16：不阻塞事件循环）。

模型来源边界（N5-4）：模型文件（GGUF / 分类头 / 分词器）由用户自行获取，
来源与合规由用户承担；保存时的 GGUF 魔数 + ``head.npz`` 结构 + 分词器存在性
校验实现在 ``AIBrainSettingsViewModel.save_ai_settings``（View 不做业务校验）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import (
    get_control_value,
    safe_icon_str,
    safe_on_change,
    safe_on_click,
)
from ui.components.settings_widgets import SettingRow
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.ai_brain_settings_view_model import AIBrainSettingsViewModel
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

# 三类模型产物：state 属性 → (setter, 扩展名, i18n label key)。
# GGUF 编码器 / head.npz 分类头 / tokenizer.json 分词器（N5-4 文件选择器）。
_FILE_TARGETS: tuple[tuple[str, str, str, str], ...] = (
    ("news_sentiment_model_path", "set_news_sentiment_model_path", "gguf", "settings_news_sentiment_model_path"),
    ("news_sentiment_head_path", "set_news_sentiment_head_path", "npz", "settings_news_sentiment_head_path"),
    (
        "news_sentiment_tokenizer_path",
        "set_news_sentiment_tokenizer_path",
        "json",
        "settings_news_sentiment_tokenizer_path",
    ),
)


async def _pick_and_set(
    vm: AIBrainSettingsViewModel,
    file_picker: ft.FilePicker,
    setter: str,
    extension: str,
) -> None:
    """打开文件选择对话框并把所选路径写入 VM（照 local_model_config_panel._select_file）。"""
    try:
        result = await file_picker.pick_files(allowed_extensions=[extension])
        if result and len(result) > 0:
            getattr(vm, setter)(result[0].path or "")
    except Exception as e:
        # CancelledError 为 BaseException 不会被本捕获吞没（R2）；其余选择失败仅记日志，
        # 不阻断渲染（用户可继续手输路径）。
        logger.error(
            "[NewsSentimentConfigPanel] File pick failed: %s",
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )


def _on_pick_click_factory(
    vm: AIBrainSettingsViewModel,
    file_picker: ft.FilePicker | None,
    setter: str,
    extension: str,
) -> Callable[[ft.ControlEvent], None]:
    """创建单条路径「选择文件」按钮的 on_click 处理器（page.run_task 调度，R16）。"""

    def _on_pick_click(_e: ft.ControlEvent) -> None:
        if file_picker is None:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_pick_and_set, vm, file_picker, setter, extension)
        except RuntimeError:
            logger.debug("[NewsSentimentConfigPanel] page not available for file pick")

    return _on_pick_click


@ft.component
def NewsSentimentConfigPanel(
    vm: AIBrainSettingsViewModel,
    *,
    on_submit_save: Callable[[ft.ControlEvent], None] | None = None,
) -> ft.Container:
    """新闻情绪打分配置面板（开关 + 三路径输入 + 文件选择按钮 + hint）。

    Args:
        vm: 外部 ``AIBrainSettingsViewModel``（消费方持有生命周期，use_viewmodel
            外部模式仅订阅不 dispose）。
        on_submit_save: 路径输入 Enter 触发的保存回调（UX-09，由 AIBrainTab 注入
            其 ``_on_save_ai``；None 时不挂 on_submit）。
    """
    # --- Subscribe to VM state changes (外部 VM 模式，VM 生命周期由消费方管理) ---
    state, _ = use_viewmodel(vm=vm)

    # --- Subscribe to i18n / theme changes (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- FilePicker lifecycle (register on page.services + cleanup) ---
    file_picker = ft.use_ref(lambda: ft.FilePicker()).current

    def _setup_file_picker() -> None:
        try:
            page = ft.context.page
            if page is not None and file_picker is not None and file_picker not in page.services:
                page.services.append(file_picker)
        except RuntimeError:
            logger.debug("[NewsSentimentConfigPanel] page not available for FilePicker setup")

    def _cleanup_file_picker() -> None:
        try:
            page = ft.context.page
            if page is not None and file_picker in page.services:
                page.services.remove(file_picker)
        except RuntimeError:
            pass

    ft.use_effect(_setup_file_picker, dependencies=[], cleanup=_cleanup_file_picker)

    # --- Build controls (driven by state) ---
    def _on_news_sentiment_toggle(e: ft.ControlEvent) -> None:
        vm.set_news_sentiment_enabled(get_control_value(e.control, ft.Switch))

    news_sentiment_switch = ft.Switch(
        label="",
        value=state.news_sentiment_enabled,
        on_change=safe_on_change(_on_news_sentiment_toggle),
    )

    def _build_path_row(state_attr: str, setter: str, extension: str, label_key: str) -> ft.Row:
        field = ft.TextField(
            label=I18n.get(label_key),
            value=getattr(state, state_attr),
            expand=True,
            on_change=lambda e, s=setter: getattr(vm, s)(e.control.value),
            on_submit=safe_on_change(on_submit_save) if on_submit_save is not None else None,
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
        )
        btn_select = ft.OutlinedButton(
            content=I18n.get("settings_btn_select_file"),
            icon=ft.Icons.FOLDER_OPEN,
            on_click=safe_on_click(_on_pick_click_factory(vm, file_picker, setter, extension)),
        )
        return ft.Row([field, btn_select], spacing=8)

    path_rows = [
        _build_path_row(state_attr, setter, extension, label_key)
        for state_attr, setter, extension, label_key in _FILE_TARGETS
    ]

    return ft.Container(
        content=ft.Column(
            [
                SettingRow(
                    icon=safe_icon_str(ft.Icons.INSIGHTS),
                    title=I18n.get("settings_news_sentiment_title"),
                    subtitle=I18n.get("settings_news_sentiment_desc"),
                    control=news_sentiment_switch,
                    icon_color=AppColors.PRIMARY,
                    title_key="settings_news_sentiment_title",
                    subtitle_key="settings_news_sentiment_desc",
                ),
                *path_rows,
                ft.Text(
                    I18n.get("settings_news_sentiment_hint"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
            ],
        ),
    )
