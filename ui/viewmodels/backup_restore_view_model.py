"""BackupRestoreViewModel — BackupRestorePanel 的 ViewModel (P3-11, CLAUDE.md §3.2 MVVM)。

提供数据库备份/恢复命令:
- "手动备份" (start_backup_with_default_path): 在 IO 池生成默认路径后委托 start_backup
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
- start_backup_with_default_path 是 async 命令: 默认路径生成含 ``mkdir`` / ``resolve``
  同步磁盘 IO, 经 ``ThreadPoolManager`` 的 IO 池执行 (R16), 完成后再 await start_backup
- start_backup 是 async 命令, 调用 MaintenanceService.dump
  (dump 已是 async, 内部通过 ThreadPoolManager 提交 subprocess, 不需额外包裹)
- start_backup 将 dump 放入独立 task 并以 ``asyncio.shield`` 等待: 调用方被取消时
  只终止等待, dump 继续执行, busy 在 dump 真正终结时由其自身落地终态后释放
- start_restore_wizard 的类型探测 (``is_file``) 与头校验 (``stat`` / ``open`` / ``read``)
  合并为单个同步 worker, 经 IO 线程池执行 (R16: 网络盘/离线设备的 open 延迟不可控);
  并发/重复选文件按请求代次只采纳最新一次请求的结果
- confirm_restore 是 async 命令但不调用 sidecar (仅设 state 显示指引)
- 异常处理: dump 异常设 error_message; CancelledError 继续透传 (R2, 不被吞没)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs

from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

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


def _probe_restore_archive(path: Path) -> RestoreArchiveInfo:
    """类型探测 + 头校验合一的同步 worker（经 IO 线程池执行，R16）.

    ``Path.is_file()`` 对不存在/无权限的路径返回 False 而不抛错，故与 ``open`` 失败
    统一按「不可作为备份文件」处理（``restore_failed``）。合并为单个 worker 是为了
    把类型探测与头读取的两次文件系统访问放在同一线程池调用内，避免各占一次往返。

    Raises:
        _InvalidBackupError: 空/截断文件或 magic 不匹配 (message_key 区分原因)
        OSError: 路径不是普通文件, 或 stat/open 失败 (权限/IO 等)
    """
    if not path.is_file():
        raise FileNotFoundError(f"not a regular file: {path}")
    return _read_archive_info(path)


def _generate_default_backup_path() -> Path:
    """生成带时间戳的绝对默认备份路径 (P1-7).

    返回 ``<app data>/backups/qtrading-backup-YYYYMMDD-HHMMSS.dump`` 绝对路径，
    避免 CWD 不可预测导致备份文件丢失。备份目录不存在时尝试创建；
    创建失败时 fall back 到 ``Path.cwd()`` 并记 warning（不阻塞用户操作）。

    含 ``mkdir`` / ``resolve`` 同步磁盘 IO，调用方必须经 ``ThreadPoolManager`` 的
    IO 池执行（R16），不得在 UI 事件处理器内直接调用。
    """
    timestamp = get_now().strftime("%Y%m%d-%H%M%S")
    backups_dir = Path(platformdirs.user_data_dir("qTrading")) / "backups"
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(
            "[BackupRestorePanel] cannot create backups dir %s: %s; fall back to CWD",
            DataSanitizer.sanitize_error(str(backups_dir)),
            DataSanitizer.sanitize_error(e),
        )
        return Path.cwd().resolve() / f"qtrading-backup-{timestamp}.dump"
    return backups_dir.resolve() / f"qtrading-backup-{timestamp}.dump"


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
    - start_backup_with_default_path() (async): IO 池生成默认路径后委托 start_backup
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
        # 强引用在途 dump task（asyncio 文档要求保存 shield 传入的 task 引用，
        # 避免任务因无引用被 GC 中断）；终态落地后清空。
        self._backup_task: asyncio.Task[None] | None = None
        # 恢复文件探测代次：并发/乱序探测时只采纳最新一次请求的结果
        # （避免用户重新选文件后，迟到的旧元信息/旧错误覆盖新状态）
        self._restore_generation = 0
        self._init_mixin_fields()

    def _get_maintenance_service(self) -> EmbeddedPgMaintenanceService:
        """懒加载 EmbeddedPgMaintenanceService 单例 (DI 优先)."""
        if self._maintenance_service is None:
            from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

            self._maintenance_service = EmbeddedPgMaintenanceService()
        return self._maintenance_service

    async def start_backup_with_default_path(self) -> None:
        """手动备份 (默认路径): IO 池生成默认路径后委托 ``start_backup``.

        R16: ``_generate_default_backup_path`` 含 ``mkdir`` / ``resolve`` 同步磁盘 IO,
        不得在 UI 事件处理器内执行; 此处经 ``ThreadPoolManager`` 的 IO 池执行,
        完成后再 ``await self.start_backup(output_path)``.
        R2: 路径准备阶段被取消时 ``CancelledError`` 继续透传, 不启动备份.
        """
        output_path = await ThreadPoolManager().run_async(TaskType.IO, _generate_default_backup_path)
        await self.start_backup(output_path)

    async def start_backup(self, output_path: Path) -> None:
        """手动备份: 调用 ``dump(output_path)`` 更新 state.

        - 重复点击守卫: ``is_backing_up`` 为 True 时跳过 (与 test_connection/save_config
          同模式; UI 层 disabled 与重渲染间存在双击窗口, 同秒默认时间戳路径相同,
          并发 dump 会写同一文件导致备份损坏)
        - 备份文件覆盖保护: ``output_path.exists()`` 时不执行 (避免误覆盖)
        - 调用前清空 error_message / backup_success_message / backup_path (旧路径残留)
        - dump 在独立 task 中执行并经 ``asyncio.shield`` 等待: 调用方被取消时只终止
          等待, dump 继续; busy 仅在 dump 真正终结时释放,
          避免在途 dump 与下一次备份并发写同一文件
        - 异常时设 error_message; CancelledError 继续透传 (R2)
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
        task = asyncio.create_task(self._run_dump(output_path))
        self._backup_task = task
        # 取消只终止调用方等待，dump 继续执行并在真正终结时自行落地终态
        # （busy 由 _run_dump 释放，dump 未终结前不放开并发）。
        # R2: CancelledError 继续透传，不被吞没。
        await asyncio.shield(task)

    async def _run_dump(self, output_path: Path) -> None:
        """执行 dump 并落地终态（独立 task：调用方被取消时仍继续，直到真正终结）.

        终态在此写入 state，保证 ``is_backing_up`` 只在 dump 真正终结后释放；
        dump 仍在运行时不放开并发（两次 dump 写同一文件会损坏备份）。
        """
        try:
            result = await self._get_maintenance_service().dump(output_path)
        except asyncio.CancelledError:
            # R2: 取消继续传播（仅 loop 关闭等场景会取消本 task）
            raise
        except Exception as exc:
            logger.error("[BackupRestoreVM] start_backup failed: %s", DataSanitizer.sanitize_error(exc), exc_info=exc)
            self._backup_task = None
            self._set_state(
                is_backing_up=False,
                progress_message=None,
                error_message=Message("backup_failed"),
            )
            return
        self._backup_task = None
        self._set_state(
            is_backing_up=False,
            backup_path=result.output_path,
            progress_message=None,
            backup_success_message=Message("backup_success", params={"path": result.output_path}),
        )

    async def start_restore_wizard(self, input_path: Path) -> None:
        """恢复向导 Step 1: 校验备份文件头后设置 restore_path + confirm_state="pending".

        - 类型探测 (``is_file``) 与头校验 (``stat`` / ``open`` / ``read``) 合并为单个
          同步 worker, 经 IO 线程池执行 (R16: 网络盘/离线设备的 open 延迟不可控,
          小读取量不构成事件循环内同步 IO 的豁免)
        - 路径不是普通文件或读取失败 → error_message + confirm_state="idle"
        - 校验 pg_dump custom-format magic header ``PGDMP``:
          空/截断文件与非法格式分别给出明确错误, 均不进入 pending
        - 校验通过时记录文件大小 + archive 版本, 供 pending 态展示元信息
        - 每次调用递增请求代次: 迟到返回的旧代次结果 (成功或失败) 一律丢弃,
          避免用户重新选文件后旧元信息覆盖新状态
        - 取消只终止等待 (worker 内的只读探测自然结束, 不写 state);
          CancelledError 继续透传 (R2)
        """
        self._restore_generation += 1
        generation = self._restore_generation
        error_message: Message | None = None
        archive_info: RestoreArchiveInfo | None = None
        try:
            archive_info = await ThreadPoolManager().run_async(TaskType.IO, _probe_restore_archive, input_path)
        except asyncio.CancelledError:
            # R2: 取消继续传播；不写 state（dispose 后 _set_state 另有 disposed 守卫）
            raise
        except _InvalidBackupError as exc:
            logger.warning(
                "[BackupRestoreVM] invalid backup file: %s (%s)",
                DataSanitizer.sanitize_error(str(input_path)),  # R9: 路径可能含用户名等敏感信息
                exc.message_key,
            )
            error_message = Message(exc.message_key)
        except OSError as exc:
            logger.warning(
                "[BackupRestoreVM] backup file unreadable: %s (%s)",
                DataSanitizer.sanitize_error(str(input_path)),  # R9: 路径可能含用户名等敏感信息
                DataSanitizer.sanitize_error(exc),
            )
            error_message = Message("restore_failed")
        if generation != self._restore_generation:
            logger.debug(
                "[BackupRestoreVM] discard stale restore probe result (generation %d -> %d)",
                generation,
                self._restore_generation,
            )
            return
        if error_message is not None:
            self._set_state(
                error_message=error_message,
                confirm_state="idle",
                restore_path=None,
                restore_archive_info=None,
            )
            return
        assert archive_info is not None  # 成功路径必已赋值（失败/取消路径已提前返回或上抛）
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
