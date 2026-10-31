"""BackupRestoreViewModel — BackupRestorePanel 的 ViewModel (P3-11, CLAUDE.md §3.2 MVVM)。

提供数据库备份/恢复命令:
- "手动备份" (start_backup): 调用 ``EmbeddedPgMaintenanceService.dump(output_path)``
- "恢复向导" (start_restore_wizard → confirm_restore): 引导离线恢复流程
  - Step 1 (start_restore_wizard): 校验 pg_dump custom-format 头 (``PGDMP``),
    通过后设置 restore_path + restore_archive_info + confirm_state="pending"
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
from utils.sanitizers import DataSanitizer

if TYPE_CHECKING:
    from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

logger = logging.getLogger(__name__)

# pg_dump custom-format 归档头: 5 字节 ASCII magic "PGDMP" +
# vmaj/vmin/vrev 三字节版本号 (至少 8 字节, 不含后续 TOC 偏移等字段)
_PGDMP_MAGIC = b"PGDMP"
_PGDMP_HEADER_MIN_BYTES = 8


class _InvalidBackupError(Exception):
    """备份文件头校验失败 (携带 i18n key, 供 VM 转译为 Message)."""

    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.message_key = message_key


@dataclass(frozen=True)
class RestoreArchiveInfo:
    """pg_dump custom-format 备份的只读元信息 (供恢复向导 pending 态展示)."""

    file_size: int
    archive_version: str


def _read_archive_info(path: Path) -> RestoreArchiveInfo:
    """读取 pg_dump custom-format 备份头, 返回文件大小 + 归档版本.

    仅读取前 8 字节 (magic + 版本号), 不解析 TOC, 避免一次读入大备份文件.

    Raises:
        _InvalidBackupError: 空/截断文件或 magic 不匹配 (message_key 区分原因)
        OSError: 文件读取失败 (权限/IO 等)
    """
    file_size = path.stat().st_size
    with path.open("rb") as fh:
        header = fh.read(_PGDMP_HEADER_MIN_BYTES)
    if file_size == 0 or len(header) < _PGDMP_HEADER_MIN_BYTES:
        raise _InvalidBackupError("restore_file_invalid")
    if header[: len(_PGDMP_MAGIC)] != _PGDMP_MAGIC:
        raise _InvalidBackupError("restore_invalid_format")
    archive_version = f"{header[5]}.{header[6]}"
    return RestoreArchiveInfo(file_size=file_size, archive_version=archive_version)


@dataclass(frozen=True)
class BackupRestoreState:
    """BackupRestorePanel 的不可变 state snapshot。

    confirm_state 取值:
    - "idle": 初始 / 已完成 / 已取消
    - "pending": 已选备份文件, 等待用户确认
    - "offline_guidance": 用户已确认, 显示离线恢复指引消息 (D36: 不直接调 sidecar restore)
    - "cancelled": 用户已取消

    注: is_restoring / restore_success_message / restore_step3_executing 已移除 (P1-7)，
    D36 决策下 UI 不直接执行 restore，无执行中状态与成功消息。
    """

    is_backing_up: bool = False
    backup_path: str | None = None
    restore_path: str | None = None
    restore_archive_info: RestoreArchiveInfo | None = None
    progress_message: Message | None = None
    error_message: Message | None = None
    confirm_state: str = "idle"
    backup_success_message: Message | None = None


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
        self._maintenance_service = maintenance_service
        self._init_mixin_fields()

    def _get_maintenance_service(self) -> EmbeddedPgMaintenanceService:
        """懒加载 EmbeddedPgMaintenanceService 单例 (DI 优先)."""
        if self._maintenance_service is None:
            from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

            self._maintenance_service = EmbeddedPgMaintenanceService()
        return self._maintenance_service

    async def start_backup(self, output_path: Path) -> None:
        """手动备份: 调用 ``dump(output_path)`` 更新 state.

        - 重复点击守卫: ``is_backing_up`` 为 True 时跳过 (与 test_connection/save_config
          同模式; UI 层 disabled 与重渲染间存在双击窗口, 同秒默认时间戳路径相同,
          并发 dump 会写同一文件导致备份损坏)
        - 备份文件覆盖保护: ``output_path.exists()`` 时不执行 (避免误覆盖)
        - 调用前清空 error_message / backup_success_message / backup_path (旧路径残留)
        - 异常时设 error_message (R2: CancelledError 自动透传)
        """
        if self._state.is_backing_up:
            logger.debug("[BackupRestoreVM] start_backup skipped, backup in progress")
            return
        if output_path.exists():  # noqa: ASYNC240  # 备份前一次性存在性探测，防误覆盖（非交互热路径）
            logger.warning(
                "[BackupRestoreVM] backup file already exists, skip: %s",
                DataSanitizer.sanitize_error(str(output_path)),  # R9: 路径可能含用户名等敏感信息
            )
            self._set_state(
                error_message=Message("backup_failed"),
                is_backing_up=False,
            )
            return
        self._set_state(
            is_backing_up=True,
            backup_path=None,
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
            logger.error("[BackupRestoreVM] start_backup failed: %s", DataSanitizer.sanitize_error(exc), exc_info=True)
            self._set_state(
                is_backing_up=False,
                progress_message=None,
                error_message=Message("backup_failed"),
            )

    async def start_restore_wizard(self, input_path: Path) -> None:
        """恢复向导 Step 1: 校验备份文件头后设置 restore_path + confirm_state="pending".

        - 路径必须是普通文件 (不存在/目录/读取失败时设 error_message, 不进入 pending)
        - 校验 pg_dump custom-format magic header ``PGDMP``:
          空/截断文件与非法格式分别给出明确错误, 均不进入 pending
        - 校验通过时记录文件大小 + archive 版本, 供 pending 态展示元信息
        - 清空之前的 error_message
        """
        if not input_path.is_file():  # noqa: ASYNC240  # restore 前一次性类型探测（头读取前，非交互热路径）
            logger.warning(
                "[BackupRestoreVM] restore path is not a file: %s",
                DataSanitizer.sanitize_error(str(input_path)),  # R9: 路径可能含用户名等敏感信息
            )
            self._set_state(
                error_message=Message("restore_failed"),
                confirm_state="idle",
                restore_path=None,
                restore_archive_info=None,
            )
            return
        try:
            archive_info = _read_archive_info(input_path)
        except _InvalidBackupError as exc:
            logger.warning(
                "[BackupRestoreVM] invalid backup file: %s (%s)",
                DataSanitizer.sanitize_error(str(input_path)),  # R9: 路径可能含用户名等敏感信息
                exc.message_key,
            )
            self._set_state(
                error_message=Message(exc.message_key),
                confirm_state="idle",
                restore_path=None,
                restore_archive_info=None,
            )
            return
        except OSError as exc:
            logger.warning(
                "[BackupRestoreVM] backup file unreadable: %s (%s)",
                DataSanitizer.sanitize_error(str(input_path)),  # R9: 路径可能含用户名等敏感信息
                DataSanitizer.sanitize_error(exc),
            )
            self._set_state(
                error_message=Message("restore_failed"),
                confirm_state="idle",
                restore_path=None,
                restore_archive_info=None,
            )
            return
        self._set_state(
            restore_path=str(input_path),
            restore_archive_info=archive_info,
            confirm_state="pending",
            error_message=None,
        )

    async def confirm_restore(self) -> None:
        """恢复向导 Step 2: confirm_state="offline_guidance" + 显示离线恢复指引.

        D36 决策: UI 不直接调用 ``svc.restore()``, 因为 §13.3 PGDATA 级操作必须持维护锁,
        qTrading 运行中调用 sidecar restore 会因锁冲突失败 exit 50.
        引导用户关闭 qTrading 后通过离线维护脚本 (qtrading-db-maintenance.bat/.sh) 执行 restore.

        - 仅当 confirm_state=="pending" 时执行 (防止重复触发)
        - restore_path 为 None 时直接设 error_message (防御性)
        - 注入 backup_path 参数到指引消息: 用户可在指引中看到所选备份文件路径 (P1-4)
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
            progress_message=Message(
                "restore_offline_guidance",
                params={"backup_path": self._state.restore_path or ""},
            ),
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
            restore_archive_info=None,
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
            restore_archive_info=None,
            progress_message=None,
        )
