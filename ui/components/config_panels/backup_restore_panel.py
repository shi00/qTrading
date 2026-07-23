"""BackupRestorePanel — 声明式数据库备份/恢复面板组件 (P3-11).

显示备份/恢复操作 UI:
- "手动备份" 按钮 → vm.start_backup(output_path)
- "恢复向导" 按钮 → vm.start_restore_wizard(input_path) → confirm_restore / dismiss_offline_guidance
- 备份进度状态显示 (progress_message / error_message / success_message)
- 恢复前二次确认 (tri-state confirmation: pending → offline_guidance/cancelled)
- 离线恢复指引 (D36: 不直接调 sidecar restore, 引导用户用离线维护脚本)

CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
- 内部 VM 模式 (factory=BackupRestoreViewModel): hook 实例化 + dispose on unmount
- View 通过 use_viewmodel(factory=...) 订阅 vm.state 变化触发重渲染
- i18n 通过 ft.use_state(get_observable_state) 自动重渲染
- View 不持有业务状态 (state 全部从 VM 读取)
"""

from datetime import datetime
import logging
from pathlib import Path

import flet as ft
import platformdirs

from ui.components.flet_type_helpers import safe_on_click
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.backup_restore_view_model import BackupRestoreViewModel

logger = logging.getLogger(__name__)


def _generate_default_backup_path() -> Path:
    """生成带时间戳的绝对默认备份路径 (P1-7).

    返回 ``<app data>/backups/qtrading-backup-YYYYMMDD-HHMMSS.dump`` 绝对路径，
    避免 CWD 不可预测导致备份文件丢失。备份目录不存在时尝试创建；
    创建失败时 fall back 到 ``Path.cwd()`` 并记 warning（不阻塞用户操作）。
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups_dir = Path(platformdirs.user_data_dir("qTrading")) / "backups"
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(
            "[BackupRestorePanel] cannot create backups dir %s: %s; fall back to CWD",
            backups_dir,
            e,
        )
        return Path.cwd().resolve() / f"qtrading-backup-{timestamp}.dump"
    return backups_dir.resolve() / f"qtrading-backup-{timestamp}.dump"


def _render_message(msg: Message | None) -> str:
    """Render a Message to localized text via I18n.get."""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


def _on_backup_click_factory(vm: BackupRestoreViewModel) -> ft.ControlEventHandler | None:
    """Create on_click handler for backup button — submits vm.start_backup."""

    def _on_backup_click(e: ft.ControlEvent) -> None:  # noqa: ARG001
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.start_backup, _generate_default_backup_path())
        except RuntimeError:
            logger.debug("[BackupRestorePanel] page not available for start_backup")

    return safe_on_click(_on_backup_click)


def _on_restore_wizard_click_factory(vm: BackupRestoreViewModel) -> ft.ControlEventHandler | None:
    """Create on_click handler for restore wizard button — submits vm.start_restore_wizard.

    Restore input path: 优先使用 state.backup_path (最近一次备份), 否则用默认值.
    """

    def _on_restore_wizard_click(e: ft.ControlEvent) -> None:  # noqa: ARG001
        try:
            page = ft.context.page
            if page is not None:
                input_path = (
                    Path(vm.state.backup_path) if vm.state.backup_path is not None else _generate_default_backup_path()
                )
                page.run_task(vm.start_restore_wizard, input_path)
        except RuntimeError:
            logger.debug("[BackupRestorePanel] page not available for start_restore_wizard")

    return safe_on_click(_on_restore_wizard_click)


def _on_confirm_restore_click_factory(vm: BackupRestoreViewModel) -> ft.ControlEventHandler | None:
    """Create on_click handler for confirm restore button — submits vm.confirm_restore."""

    def _on_confirm_restore_click(e: ft.ControlEvent) -> None:  # noqa: ARG001
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.confirm_restore)
        except RuntimeError:
            logger.debug("[BackupRestorePanel] page not available for confirm_restore")

    return safe_on_click(_on_confirm_restore_click)


def _on_cancel_restore_click_factory(vm: BackupRestoreViewModel) -> ft.ControlEventHandler | None:
    """Create on_click handler for cancel restore button — calls vm.cancel_restore (sync)."""

    def _on_cancel_restore_click(e: ft.ControlEvent) -> None:  # noqa: ARG001
        vm.cancel_restore()

    return safe_on_click(_on_cancel_restore_click)


def _on_dismiss_offline_guidance_click_factory(vm: BackupRestoreViewModel) -> ft.ControlEventHandler | None:
    """Create on_click handler for dismiss offline guidance button — calls vm.dismiss_offline_guidance (sync)."""

    def _on_dismiss_offline_guidance_click(e: ft.ControlEvent) -> None:  # noqa: ARG001
        vm.dismiss_offline_guidance()

    return safe_on_click(_on_dismiss_offline_guidance_click)


@ft.component
def BackupRestorePanel() -> ft.Container:
    """数据库备份/恢复面板 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - 内部 VM 模式: hook 实例化 BackupRestoreViewModel + dispose on unmount
    - View 通过 use_viewmodel(factory=...) 订阅 vm.state 变化触发重渲染
    - i18n 通过 ft.use_state(get_observable_state) 自动重渲染
    - View 不持有业务状态, 全部从 VM state 读取

    Returns:
        ft.Container: 含备份/恢复操作 + 状态显示的面板
    """
    # --- 内部 VM 模式: hook 实例化 + dispose on unmount ---
    state, vm = use_viewmodel(factory=BackupRestoreViewModel)

    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- Title ---
    title_ctrl = ft.Text(
        I18n.get("backup_restore_title"),
        size=AppStyles.FONT_SIZE_HEADLINE,
        color=AppColors.TEXT_PRIMARY,
        weight=ft.FontWeight.BOLD,
    )

    # --- Backup section ---
    backup_button = ft.Button(
        content=I18n.get("backup_button"),
        icon=ft.Icons.SAVE,
        on_click=_on_backup_click_factory(vm),
        style=AppStyles.secondary_button(),
        disabled=state.is_backing_up,
    )

    backup_path_text = I18n.get("db_status_data_dir", path=state.backup_path) if state.backup_path is not None else ""
    backup_path_ctrl = ft.Text(
        backup_path_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.TEXT_SECONDARY,
        visible=backup_path_text != "",
    )

    backup_progress_text = _render_message(state.progress_message if state.is_backing_up else None)
    backup_progress_ctrl = ft.Text(
        backup_progress_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.INFO,
        visible=backup_progress_text != "",
    )

    backup_success_text = _render_message(state.backup_success_message)
    backup_success_ctrl = ft.Text(
        backup_success_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.SUCCESS,
        visible=backup_success_text != "",
    )

    # --- Restore wizard section ---
    # Step 1 (idle): 显示恢复按钮 + 文件选择提示
    # Step 2 (pending): 显示确认对话框 (title + message) + 确认/取消按钮
    # Step 3 (confirmed/executing): 显示执行中状态
    restore_section_controls: list[ft.Control] = []

    restore_button = ft.Button(
        content=I18n.get("restore_button"),
        icon=ft.Icons.RESTORE,
        on_click=_on_restore_wizard_click_factory(vm),
        style=AppStyles.secondary_button(),
        disabled=state.is_restoring,
        visible=state.confirm_state in ("idle", "cancelled"),
    )
    restore_section_controls.append(restore_button)

    if state.confirm_state in ("idle", "cancelled"):
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_step1_select"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )

    if state.confirm_state == "pending":
        # Step 2: 确认对话框 (title + message + 确认/取消按钮)
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_step2_confirm"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
                weight=ft.FontWeight.BOLD,
            )
        )
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_confirm_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                color=AppColors.TEXT_PRIMARY,
                weight=ft.FontWeight.BOLD,
            )
        )
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_confirm_message"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
            )
        )
        confirm_restore_button = ft.Button(
            content=I18n.get("confirm_restore_button"),
            icon=ft.Icons.CHECK,
            on_click=_on_confirm_restore_click_factory(vm),
            style=AppStyles.primary_button(),
        )
        cancel_restore_button = ft.Button(
            content=I18n.get("cancel_restore_button"),
            icon=ft.Icons.CANCEL,
            on_click=_on_cancel_restore_click_factory(vm),
            style=AppStyles.secondary_button(),
        )
        restore_section_controls.append(
            ft.Row(
                [confirm_restore_button, cancel_restore_button],
                spacing=10,
                wrap=True,
            )
        )

    if state.confirm_state == "confirmed" or state.is_restoring:
        # Step 3: 执行中 (防御性渲染, 当前 VM 流程不进入此状态)
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_step3_executing"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.INFO,
            )
        )

    if state.confirm_state == "offline_guidance":
        # Step 3 (D36): 离线恢复指引 — 用户已确认, 显示离线恢复步骤 + "我已了解"按钮
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_offline_guidance_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                color=AppColors.INFO,
                weight=ft.FontWeight.BOLD,
            )
        )
        restore_section_controls.append(
            ft.Text(
                I18n.get("restore_offline_guidance"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
        dismiss_button = ft.Button(
            content=I18n.get("restore_offline_guidance_dismiss"),
            icon=ft.Icons.CHECK,
            on_click=_on_dismiss_offline_guidance_click_factory(vm),
            style=AppStyles.primary_button(),
        )
        restore_section_controls.append(dismiss_button)

    restore_progress_text = _render_message(state.progress_message if state.is_restoring else None)
    restore_progress_ctrl = ft.Text(
        restore_progress_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.INFO,
        visible=restore_progress_text != "",
    )

    restore_success_text = _render_message(state.restore_success_message)
    restore_success_ctrl = ft.Text(
        restore_success_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.SUCCESS,
        visible=restore_success_text != "",
    )

    # --- Error / cancelled message display ---
    error_text = _render_message(state.error_message)
    error_ctrl = ft.Text(
        error_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.ERROR,
        visible=error_text != "",
    )

    cancelled_text = I18n.get("restore_cancelled") if state.confirm_state == "cancelled" else ""
    cancelled_ctrl = ft.Text(
        cancelled_text,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.TEXT_SECONDARY,
        visible=cancelled_text != "",
    )

    # --- Build UI layout ---
    return ft.Container(
        content=ft.Column(
            [
                title_ctrl,
                ft.Container(height=8),
                # --- Backup section ---
                backup_button,
                backup_path_ctrl,
                backup_progress_ctrl,
                backup_success_ctrl,
                ft.Container(height=12),
                # --- Restore wizard section ---
                *restore_section_controls,
                restore_progress_ctrl,
                restore_success_ctrl,
                # --- Common messages ---
                error_ctrl,
                cancelled_ctrl,
            ],
            spacing=5,
        ),
    )
