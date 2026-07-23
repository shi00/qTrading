"""BackupRestoreViewModel — BackupRestorePanel 的 ViewModel (P3-11, CLAUDE.md §3.2 MVVM)。

提供数据库备份/恢复命令:
- "手动备份" (start_backup): 调用 ``EmbeddedPgMaintenanceService.dump(output_path)``
- "恢复向导" (start_restore_wizard → confirm_restore): 引导离线恢复流程
  - Step 1 (start_restore_wizard): 设置 restore_path + confirm_state="pending"
  - Step 2 (confirm_restore): confirm_state="offline_guidance" + 显示离线恢复指引消息
    (D36: UI 不直接执行 restore, 因为 §13.3 PGDATA 级操作必须持维护锁,
     qTrading 运行中调用 sidecar restore 会因锁冲突失败 exit 50.
     引导用户关闭 qTrading 后通过离线维护脚本执行 restore)
  - 取消 (cancel_restore): confirm_state="cancelled" + 清空 restore_path
  - 关闭指引 (dismiss_offline_guidance): confirm_state="idle" + 清空 restore_path

VM 不感知 locale: state 用 Message dataclass 产出 (key, params),
View 渲染时 I18n.get(msg.key, **msg.params)。

线程模型:
- start_backup 是 async 命令, 调用 MaintenanceService.dump
  (dump 已是 async, 内部通过 ThreadPoolManager 提交 subprocess, 不需额外包裹)
- confirm_restore 是 async 命令但不调用 sidecar (仅设 state 显示指引)
- 异常处理: ``except Exception`` 设 error_message
  (R2: CancelledError 是 BaseException 子类, 自动透传, 不被捕获)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

if TYPE_CHECKING:
    from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupRestoreState:
    """BackupRestorePanel 的不可变 state snapshot。

    confirm_state 取值:
    - "idle": 初始 / 已完成 / 已取消
    - "pending": 已选备份文件, 等待用户确认
    - "offline_guidance": 用户已确认, 显示离线恢复指引消息 (D36: 不直接调 sidecar restore)
    - "cancelled": 用户已取消
    """

    is_backing_up: bool = False
    is_restoring: bool = False
    backup_path: str | None = None
    restore_path: str | None = None
    progress_message: Message | None = None
    error_message: Message | None = None
    confirm_state: str = "idle"
    backup_success_message: Message | None = None
    restore_success_message: Message | None = None


class BackupRestoreViewModel(ObservableViewModelMixin[BackupRestoreState]):
    """ViewModel for BackupRestorePanel (R-A6 新 VM).

    MVVM + 声明式渲染范式 (CLAUDE.md §3.2):
    - 不可变 state snapshot (BackupRestoreState) via subscribe/_notify
    - VM 不感知 locale, state 用 Message 产出 (key, params)
    - 内部 VM 模式 (use_viewmodel(factory=...)): 由 BackupRestorePanel 实例化,
      生命周期由 hook 管理 (dispose_on_unmount=True)

    命令:
    - start_backup(output_path) (async): 调用 dump() 更新 state
    - start_restore_wizard(input_path) (async): Step 1, 设置 restore_path + confirm_state="pending"
    - confirm_restore() (async): Step 2, confirm_state="offline_guidance" + 显示离线恢复指引消息
      (D36: 不直接调用 sidecar restore, 因 §13.3 维护锁在 qTrading 运行时会冲突)
    - cancel_restore() (sync): confirm_state="cancelled" + 清空 restore_path
    - dismiss_offline_guidance() (sync): confirm_state="idle" + 清空 restore_path

    Args:
        maintenance_service: 可选 DI (测试注入); 为 None 时懒加载单例
    """

    def __init__(self, maintenance_service: EmbeddedPgMaintenanceService | None = None) -> None:
        self._state = BackupRestoreState()
        self._subscribers: list = []
        self._maintenance_service = maintenance_service

    def _get_maintenance_service(self) -> EmbeddedPgMaintenanceService:
        """懒加载 EmbeddedPgMaintenanceService 单例 (DI 优先)."""
        if self._maintenance_service is None:
            from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

            self._maintenance_service = EmbeddedPgMaintenanceService()
        return self._maintenance_service

    async def start_backup(self, output_path: Path) -> None:
        """手动备份: 调用 ``dump(output_path)`` 更新 state.

        - 备份文件覆盖保护: ``output_path.exists()`` 时不执行 (避免误覆盖)
        - 调用前清空 error_message / backup_success_message
        - 异常时设 error_message (R2: CancelledError 自动透传)
        """
        if output_path.exists():
            logger.warning("[BackupRestoreVM] backup file already exists, skip: %s", output_path)
            self._set_state(
                error_message=Message("backup_failed"),
                is_backing_up=False,
            )
            return
        self._set_state(
            is_backing_up=True,
            progress_message=Message("backup_in_progress"),
            error_message=None,
            backup_success_message=None,
        )
        try:
            svc = self._get_maintenance_service()
            result = await svc.dump(output_path)
            self._set_state(
                is_backing_up=False,
                backup_path=result.output_path,
                progress_message=None,
                backup_success_message=Message("backup_success", params={"path": result.output_path}),
            )
        except Exception as exc:
            logger.error("[BackupRestoreVM] start_backup failed: %s", exc, exc_info=True)
            self._set_state(
                is_backing_up=False,
                progress_message=None,
                error_message=Message("backup_failed"),
            )

    async def start_restore_wizard(self, input_path: Path) -> None:
        """恢复向导 Step 1: 设置 restore_path + confirm_state="pending".

        - restore 文件存在性校验: 不存在时直接设 error_message, 不进入 pending
        - 清空之前的 restore_success_message / error_message
        """
        if not input_path.exists():
            logger.warning("[BackupRestoreVM] restore file not found: %s", input_path)
            self._set_state(
                error_message=Message("restore_failed"),
                confirm_state="idle",
                restore_path=None,
            )
            return
        self._set_state(
            restore_path=str(input_path),
            confirm_state="pending",
            error_message=None,
            restore_success_message=None,
        )

    async def confirm_restore(self) -> None:
        """恢复向导 Step 2: confirm_state="offline_guidance" + 显示离线恢复指引.

        D36 决策: UI 不直接调用 ``svc.restore()``, 因为 §13.3 PGDATA 级操作必须持维护锁,
        qTrading 运行中调用 sidecar restore 会因锁冲突失败 exit 50.
        引导用户关闭 qTrading 后通过离线维护脚本 (qtrading-db-maintenance.bat/.sh) 执行 restore.

        - 仅当 confirm_state=="pending" 时执行 (防止重复触发)
        - restore_path 为 None 时直接设 error_message (防御性)
        - 保留 restore_path 供指引消息引用 (用户可在指引中看到所选备份文件)
        """
        if self._state.confirm_state != "pending":
            logger.debug(
                "[BackupRestoreVM] confirm_restore skipped, confirm_state=%s",
                self._state.confirm_state,
            )
            return
        if self._state.restore_path is None:
            self._set_state(
                error_message=Message("restore_failed"),
                confirm_state="idle",
            )
            return
        self._set_state(
            confirm_state="offline_guidance",
            progress_message=Message("restore_offline_guidance"),
            error_message=None,
        )

    def dismiss_offline_guidance(self) -> None:
        """关闭离线恢复指引: confirm_state="idle" + 清空 restore_path.

        仅当 confirm_state=="offline_guidance" 时生效.
        """
        if self._state.confirm_state != "offline_guidance":
            logger.debug(
                "[BackupRestoreVM] dismiss_offline_guidance skipped, confirm_state=%s",
                self._state.confirm_state,
            )
            return
        self._set_state(
            confirm_state="idle",
            restore_path=None,
            progress_message=None,
        )

    def cancel_restore(self) -> None:
        """取消恢复: confirm_state="cancelled" + 清空 restore_path.

        仅当 confirm_state=="pending" 时生效 (执行中不可取消).
        """
        if self._state.confirm_state != "pending":
            logger.debug(
                "[BackupRestoreVM] cancel_restore skipped, confirm_state=%s",
                self._state.confirm_state,
            )
            return
        self._set_state(
            confirm_state="cancelled",
            restore_path=None,
            progress_message=None,
        )
