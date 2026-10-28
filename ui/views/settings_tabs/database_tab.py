"""DatabaseTab — 声明式组件 (Phase 3 P3-13).

P3-13 改造为 3 面板默认显示 + 高级模式开关:
- 默认渲染: EmbeddedStatusCard + DatabaseStatusPanel + BackupRestorePanel
- 高级模式开关 (ft.Switch, 持久化到 AppConfig.db_show_advanced)
- 高级模式开启时追加 ExternalPgForm (DatabaseConfigPanelViewModel 外部 VM 模式)
- 底部追加"离线维护工具"说明 Card

CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
- 内部 VM 模式面板 (EmbeddedStatusCard / DatabaseStatusPanel / BackupRestorePanel)
  自身通过 use_viewmodel(factory=...) 实例化 VM, DatabaseTab 直接调用即可
- 高级模式的 ExternalPgForm 通过 use_viewmodel(factory=...) 在 DatabaseTab 顶层
  实例化 DatabaseConfigPanelViewModel (hook 顺序稳定, 不受 show_advanced 影响)
- i18n/theme 通过 ft.use_state(*.get_observable_state) 订阅自动重渲染
- 高级模式状态通过 ft.use_state + use_effect 持久化到 AppConfig
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from ui.components.config_panels.backup_restore_panel import BackupRestorePanel
from ui.components.config_panels.database_status_panel import DatabaseStatusPanel
from ui.components.config_panels.embedded_status_card import EmbeddedStatusCard
from ui.components.config_panels.external_pg_form import ExternalPgForm
from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel
from utils.config.db import DatabaseMode, DatabaseModeResolution

logger = logging.getLogger(__name__)

# UIR-F15-001：模式来源 → i18n key 的显式字面量映射。
# 不用 f-string 拼接：AST 静态扫描（i18n 死键门禁）只能识别字面量引用，动态拼接会被
# 判定为死键；且映射集中后，locale 键被删改时可在同一处核对（未知来源回退裸来源串，
# 与改造前行为一致）。
_MODE_SOURCE_I18N_KEYS: dict[str, str] = {
    "env": "settings_db_mode_source_env",
    "config": "settings_db_mode_source_config",
    "fallback_standard_variant": "settings_db_mode_source_fallback_standard_variant",
}


def _mode_source_label(source: str) -> str:
    """把模式来源标识翻译为可读文案；未知来源回退为原始标识（不伪造文案）。"""
    key = _MODE_SOURCE_I18N_KEYS.get(source)
    return I18n.get(key) if key is not None else source


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _on_test_success(config: dict) -> None:
    """Log successful connection test (module-level pure function)."""
    logger.debug(
        "Database connection test successful: %s:%s/%s",
        config["host"],
        config["port"],
        config["database"],
    )


@ft.component
def DatabaseTab(show_snack_callback: Callable) -> ft.Container:
    """Database configuration tab for settings page (declarative, P3-13 3-panel).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - 默认渲染 3 个内部 VM 模式面板 (各自 hook 实例化 + dispose)
    - 高级模式开启时追加 ExternalPgForm (DatabaseConfigPanelViewModel 外部 VM 模式,
      通过 use_viewmodel(factory=...) 在顶层实例化以保持 hook 顺序稳定)
    - i18n/theme 通过 ft.use_state(*.get_observable_state) 订阅自动重渲染
    - 高级模式状态通过 ft.use_state + use_effect 持久化到 AppConfig.db_show_advanced
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """

    def _make_external_vm() -> DatabaseConfigPanelViewModel:
        def _on_save(config: dict) -> None:
            show_snack_callback(I18n.get("settings_db_saved"), "success")

        return DatabaseConfigPanelViewModel(
            on_save_callback=_on_save,
            on_test_success_callback=_on_test_success,
            load_password=True,
            # UIR-F15-001：设置页保存外置配置同时切换为外置模式（重启后生效）
            activate_mode_on_save=True,
        )

    # --- ExternalPgForm VM: 顶层实例化, hook 顺序稳定 (不受 show_advanced 影响) ---
    state, database_config_vm = use_viewmodel(_make_external_vm)

    # --- Subscribe to i18n + theme changes (auto-rerender on locale/theme switch) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 高级模式开关状态 (默认 False) ---
    show_advanced, set_show_advanced = ft.use_state(False)

    def _load_advanced_state() -> None:
        """挂载时从 AppConfig 读取高级模式开关初始值。"""
        try:
            saved = database_config_vm.load_show_advanced()
            set_show_advanced(bool(saved))
        except (
            Exception
        ):  # NOTE(lazy): 配置读取失败降级为默认 False. ceiling: 配置文件不可读. upgrade: 引入配置可读性预检.
            logger.debug("[DatabaseTab] Failed to load db_show_advanced, using default False")

    ft.use_effect(_load_advanced_state, dependencies=[])

    async def _do_save_show_advanced(value: bool) -> None:
        """异步持久化 db_show_advanced 到 AppConfig (R16: IO offload via VM)."""
        try:
            await database_config_vm.save_show_advanced(value)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except (
            Exception
        ):  # NOTE(lazy): 配置写入失败降级为内存态. ceiling: 配置文件不可写. upgrade: 引入配置可写性预检或重试.
            logger.debug("[DatabaseTab] Failed to persist db_show_advanced=%s", value)

    def _on_advanced_toggle(e: ft.ControlEvent) -> None:
        """高级模式开关切换: 更新 state + 调度异步持久化 (R16: sync wrapper + page.run_task)."""
        value = bool(get_control_value(e.control, ft.Switch))
        set_show_advanced(value)
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_show_advanced, value)

    def _on_switch_to_embedded(e: ft.ControlEvent) -> None:
        """切回内置 PostgreSQL 模式（仅保存下次启动偏好，不热切换引擎）。"""
        page = _get_page()
        if page is not None:
            page.run_task(database_config_vm.switch_to_embedded_mode)

    # --- 模式身份: 当前生效 / 待生效 / 来源 / 冲突 / 降级 ---
    resolution = state.mode_resolution or DatabaseModeResolution(
        mode=DatabaseMode.EMBEDDED,
        source="config",
        configured_mode=DatabaseMode.EMBEDDED,
    )
    is_embedded = resolution.mode is DatabaseMode.EMBEDDED

    def _mode_label(mode: DatabaseMode) -> str:
        key = "settings_db_mode_embedded" if mode is DatabaseMode.EMBEDDED else "settings_db_mode_external"
        return I18n.get(key)

    mode_children: list[ft.Control] = [
        ft.Text(
            I18n.get(
                "settings_db_mode_current",
                mode=_mode_label(resolution.mode),
                source=_mode_source_label(resolution.source),
            ),
            size=AppStyles.FONT_SIZE_BODY,
            color=AppColors.TEXT_PRIMARY,
        ),
    ]
    if resolution.env_mode is not None:
        mode_children.append(
            ft.Text(
                I18n.get("settings_db_mode_env_forced", mode=_mode_label(resolution.env_mode)),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
            )
        )
    if resolution.conflict:
        mode_children.append(
            ft.Text(
                I18n.get("settings_db_mode_conflict"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
            )
        )
    if resolution.fallback_reason == "standard_variant":
        mode_children.append(
            ft.Text(
                I18n.get("settings_db_mode_standard_variant"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    if resolution.restart_required:
        mode_children.append(
            ft.Text(
                I18n.get("settings_db_mode_pending", mode=_mode_label(resolution.configured_mode)),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
            )
        )
    # 仅在「环境未强制模式」且「非标准版自动降级」时提供切回内置入口：
    # 这两种情况下写入偏好后重启仍会被覆盖，展示按钮等于给出不可兑现的动作。
    if not is_embedded and resolution.env_mode is None and resolution.fallback_reason is None:
        mode_children.append(
            ft.Button(
                content=I18n.get("settings_db_mode_switch_embedded"),
                icon=ft.Icons.ARROW_BACK,
                on_click=safe_on_click(_on_switch_to_embedded),
                style=AppStyles.secondary_button(),
            )
        )

    mode_card = ft.Card(
        content=ft.Container(
            content=ft.Column(mode_children, spacing=4),
            padding=AppStyles.SPACING_LG,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
        ),
        elevation=1,
    )

    # --- Build UI ---
    title_text = ft.Text(
        I18n.get("settings_db_title"),
        size=AppStyles.FONT_SIZE_XL,
        weight=ft.FontWeight.W_500,
        color=AppColors.TEXT_PRIMARY,
    )

    advanced_switch = ft.Switch(
        label=I18n.get("settings_db_advanced_mode"),
        value=show_advanced,
        on_change=safe_on_change(_on_advanced_toggle),
        active_color=AppColors.PRIMARY,
    )

    # --- 组装面板: 内置模式显示 sidecar 面板; 外置模式显示连接目标 + 连接表单 ---
    external_target_card = ft.Card(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        I18n.get("settings_db_external_target_title"),
                        size=AppStyles.FONT_SIZE_TITLE,
                        weight=ft.FontWeight.W_500,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Container(height=6),
                    ft.Text(
                        I18n.get(
                            "settings_db_external_target",
                            host=getattr(state, "host", ""),
                            port=getattr(state, "port", ""),
                            database=getattr(state, "database", ""),
                        ),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
            ),
            padding=AppStyles.SPACING_LG,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
        ),
        elevation=1,
    )

    panels: list[ft.Control] = [mode_card, ft.Container(height=12)]
    if is_embedded:
        panels.extend(
            [
                EmbeddedStatusCard(),
                ft.Container(height=12),
                DatabaseStatusPanel(),
                ft.Container(height=12),
                BackupRestorePanel(),
            ]
        )
        if show_advanced:
            panels.extend(
                [
                    ft.Container(height=12),
                    ExternalPgForm(
                        vm=database_config_vm,
                        show_header=True,
                        compact=False,
                        show_save_button=True,
                    ),
                ]
            )
    else:
        panels.extend(
            [
                external_target_card,
                ft.Container(height=12),
                ExternalPgForm(
                    vm=database_config_vm,
                    show_header=True,
                    compact=False,
                    show_save_button=True,
                ),
            ]
        )

    # --- 离线维护工具说明 Card (DoD 8) ---
    offline_maintenance_card = ft.Card(
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        I18n.get("settings_db_offline_maintenance_title"),
                        size=AppStyles.FONT_SIZE_TITLE,
                        weight=ft.FontWeight.W_500,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    ft.Container(height=6),
                    ft.Text(
                        I18n.get("settings_db_offline_maintenance_desc"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
            ),
            padding=AppStyles.SPACING_LG,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
        ),
        elevation=1,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Container(height=20),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.STORAGE, size=AppStyles.ICON_SIZE_LG, color=AppColors.PRIMARY),
                        title_text,
                    ],
                ),
                ft.Container(height=12),
                *([advanced_switch, ft.Container(height=12)] if is_embedded else []),
                *panels,
                ft.Container(height=20),
                offline_maintenance_card,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
