"""DatabaseStatusViewModel — DatabaseStatusPanel 的 ViewModel (P3-10, CLAUDE.md §3.2 MVVM)。

显示 embedded PostgreSQL 运行状态:
- running/stopped/version/port/data_dir/log_dir
- "打开数据目录" / "打开日志目录" / "刷新状态" 命令

VM 不感知 locale: state 用 Message dataclass 产出 (key, params),
View 渲染时 I18n.get(msg.key, **msg.params)。

线程模型:
- refresh_status() 是 async 命令: 调用 EmbeddedPgMaintenanceService.doctor()
  (doctor 已是 async，不需额外 ThreadPoolManager 包裹)
- open_data_dir/open_log_dir 是同步命令: subprocess.Popen 非阻塞启动文件管理器
- 路径白名单: 只允许打开 state.data_dir / state.log_dir (来自 AppConfig + doctor)，
  不接受用户直接输入路径 (Security Required)
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from utils.config_handler import ConfigHandler
from utils.config_models import AppConfig
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

if TYPE_CHECKING:
    from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

logger = logging.getLogger(__name__)

# 平台 → 文件管理器命令 (跨平台路径打开)
_PLATFORM_FILE_MANAGER: dict[str, list[str]] = {
    "Windows": ["explorer"],
    "Darwin": ["open"],
    "Linux": ["xdg-open"],
}


@dataclass(frozen=True)
class DatabaseStatusState:
    """DatabaseStatusPanel 的不可变 state snapshot。

    与 DatabaseConfigPanelViewModel.DatabaseConfigState 字段不同 (R-A6 新 VM),
    仅含状态显示所需字段，不含 host/port/user/password 等表单字段。
    port 字段从 AppConfig.db_port 读取 (embedded 模式复用 db_port)。
    """

    is_running: bool = False
    pg_version: str | None = None
    port: int | None = None
    data_dir: str | None = None
    log_dir: str | None = None
    # 状态消息 (running/stopped)，VM 只产出 i18n key
    status_message: Message | None = None
    # 状态类型 (success/error/warning/info)，控制 icon + color
    status_type: str = "info"
    error_message: Message | None = None
    is_refreshing: bool = False


class DatabaseStatusViewModel(ObservableViewModelMixin[DatabaseStatusState]):
    """ViewModel for DatabaseStatusPanel (R-A6 新 VM).

    MVVM + 声明式渲染范式 (CLAUDE.md §3.2):
    - 不可变 state snapshot (DatabaseStatusState) via subscribe/_notify
    - VM 不感知 locale, state 用 Message 产出 (key, params)
    - 内部 VM 模式 (use_viewmodel(factory=...)): 由 DatabaseStatusPanel 实例化,
      生命周期由 hook 管理 (dispose_on_unmount=True)

    命令:
    - refresh_status() (async): 调用 doctor() 更新 state
    - open_data_dir() (sync): subprocess.Popen 打开 data_dir
    - open_log_dir() (sync): subprocess.Popen 打开 log_dir

    Args:
        maintenance_service: 可选 DI (测试注入)；为 None 时懒加载单例
    """

    def __init__(self, maintenance_service: EmbeddedPgMaintenanceService | None = None) -> None:
        self._state = DatabaseStatusState()
        self._subscribers: list = []
        self._maintenance_service = maintenance_service

    def _get_maintenance_service(self) -> EmbeddedPgMaintenanceService:
        """懒加载 EmbeddedPgMaintenanceService 单例 (DI 优先)。"""
        if self._maintenance_service is None:
            from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService

            self._maintenance_service = EmbeddedPgMaintenanceService()
        return self._maintenance_service

    async def refresh_status(self) -> None:
        """调用 doctor() 刷新数据库状态，更新 state。

        - doctor() 返回 data_dir / pg_version / postgres_alive
        - log_dir / port 从 AppConfig 解析
        - doctor 已是 async，不需 ThreadPoolManager 包裹
        - 异常时设 error state (R2: CancelledError 自动透传，except Exception 不捕获 BaseException)
        """
        self._set_state(is_refreshing=True)
        try:
            svc = self._get_maintenance_service()
            result = await svc.doctor()
            config = AppConfig.model_validate(ConfigHandler.load_config())
            log_dir = self._resolve_log_dir(config)
            port = config.db_port
            pg_version = str(result.pg_version) if result.pg_version is not None else None
            is_running = result.postgres_alive
            status_msg = Message(
                "db_status_running" if is_running else "db_status_stopped",
            )
            status_type = "success" if is_running else "warning"
            self._set_state(
                is_running=is_running,
                pg_version=pg_version,
                port=port,
                data_dir=result.data_dir,
                log_dir=log_dir,
                status_message=status_msg,
                status_type=status_type,
                is_refreshing=False,
                error_message=None,
            )
        except Exception as exc:
            logger.error(
                "[DatabaseStatusVM] refresh_status failed: %s",
                exc,
                exc_info=True,
            )
            self._set_state(
                is_refreshing=False,
                status_type="error",
                error_message=Message("db_status_refresh_failed"),
            )

    def open_data_dir(self) -> None:
        """打开数据目录 (subprocess.Popen 非阻塞)。

        路径白名单: 只打开 state.data_dir (来自 doctor()，不接受用户输入)。
        路径不存在时不调用 Popen (Security Required)。
        """
        path = self._state.data_dir
        if path is not None:
            self._open_path_in_file_manager(path)

    def open_log_dir(self) -> None:
        """打开日志目录 (subprocess.Popen 非阻塞)。

        路径白名单: 只打开 state.log_dir (来自 AppConfig，不接受用户输入)。
        路径不存在时不调用 Popen (Security Required)。
        """
        path = self._state.log_dir
        if path is not None:
            self._open_path_in_file_manager(path)

    def _resolve_log_dir(self, config: AppConfig) -> str:
        """从 AppConfig 解析 log_dir，空则用 platformdirs 默认 <app data>/postgres-logs。

        与 EmbeddedPgMaintenanceService._get_sidecar_path_and_data_dir 一致的默认搜索逻辑。
        """
        if config.embedded_pg_log_dir:
            return config.embedded_pg_log_dir
        import platformdirs

        app_data = Path(platformdirs.user_data_dir("qTrading"))
        return str(app_data / "postgres-logs")

    def _open_path_in_file_manager(self, path: str) -> None:
        """跨平台打开文件管理器 (subprocess.Popen 非阻塞)。

        Security:
        - 路径存在性校验 (Path.exists)，不存在时不调用 Popen
        - 路径白名单: 仅由 open_data_dir/open_log_dir 调用，路径来自 state (AppConfig/doctor)
        """
        if not Path(path).exists():
            logger.warning("[DatabaseStatusVM] path does not exist, skip opening: %s", path)
            return
        system = platform.system()
        cmd_args = _PLATFORM_FILE_MANAGER.get(system)
        if not cmd_args:
            logger.warning("[DatabaseStatusVM] unsupported platform for opening dir: %s", system)
            return
        try:
            subprocess.Popen(cmd_args + [path])
        except OSError as exc:
            logger.error("[DatabaseStatusVM] failed to open file manager for %s: %s", path, exc)
