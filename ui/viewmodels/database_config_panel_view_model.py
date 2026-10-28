"""DatabaseConfigPanelViewModel — DatabaseConfigPanel 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（DatabaseConfigState frozen dataclass）
- subscribe/_notify 通知机制（View 通过 use_state + use_effect 订阅）
- commands 作为实例方法（消费方可直接调用 save_config/test_connection）

VM 不感知 locale：status_message 用 Message dataclass 产出 (key, params)，
View 渲染时 I18n.get(msg.key, **msg.params)。动态错误消息用 _RAW_MSG_KEY
+ default=params 传递，I18n.get 对不存在的 key 返回 default。

线程模型：
- test_connection/save_config 是 async，在 Flet 事件循环中执行
- ConfigHandler.save_db_config 是同步 IO，通过 ThreadPoolManager offload
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from data.persistence.db_config_service import ConnectionStatus, DatabaseConfigService
from ui.viewmodels import Message
from ui.viewmodels.config_panel_view_model_base import ConfigPanelViewModelBase
from utils.config.db import DatabaseMode, DatabaseModeResolution
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, get_error_message_key
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabaseConfigState:
    """DatabaseConfigPanel 的不可变 state snapshot。

    port 保留原始字符串以支持 validate 检测无效输入（如 "abc"）。
    get_config / validate 时解析为 int。
    """

    # Config fields
    host: str = "localhost"
    port: str = "5432"
    user: str = "postgres"
    password: str = ""
    database: str = "astock"
    create_if_not_exists: bool = True
    # Status fields
    is_verifying: bool = False
    is_saving: bool = False
    status_message: Message | None = None
    status_type: str = "info"  # "success" / "error" / "warning" / "info"
    db_info: Message | None = None
    # UIR-F15-001：当前生效模式 / 待生效模式（设置页身份展示的唯一数据源）
    mode_resolution: DatabaseModeResolution | None = None


class DatabaseConfigPanelViewModel(ConfigPanelViewModelBase[DatabaseConfigState]):
    """ViewModel for DatabaseConfigPanel.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (DatabaseConfigState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - VM 不感知 locale，status_message 用 Message 产出 (key, params)

    消费方（DatabaseTab/OnboardingWizard）直接实例化 VM 以调用 commands
    （save_config/test_connection/reload_config），View 通过 use_state +
    use_effect 订阅 VM state 变化触发重渲染。
    """

    def __init__(
        self,
        on_save_callback: Callable | None = None,
        on_test_success_callback: Callable | None = None,
        on_change: Callable | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        load_password: bool = False,
        activate_mode_on_save: bool = False,
    ):
        self._on_save_callback = on_save_callback
        self._on_test_success_callback = on_test_success_callback
        self._on_change = on_change
        self._on_loading_change = on_loading_change
        self._load_password = load_password
        # UIR-F15-001：设置页保存外置配置即「切换为外置模式」，引导向导仅保存连接配置
        self._activate_mode_on_save = activate_mode_on_save
        self._state = DatabaseConfigState()
        self._init_mixin_fields()
        # 同步初始化 state（从 ConfigHandler 加载配置）
        self._load_config_to_state()
        # UIR-F15-001：冻结本进程启动时生效的模式，避免保存偏好后把「重启后生效」
        # 误显示为「已生效」。
        initial_resolution = self._state.mode_resolution
        self._active_mode: DatabaseMode | None = (
            initial_resolution.mode if isinstance(initial_resolution, DatabaseModeResolution) else None
        )
        self._active_source: str | None = (
            initial_resolution.source if isinstance(initial_resolution, DatabaseModeResolution) else None
        )

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步）。"""
        db_config = ConfigHandler.get_db_config()
        password = ConfigHandler.get_db_password() if self._load_password else ""
        self._state = replace(
            self._state,
            host=db_config.get("host", "localhost"),
            port=str(db_config.get("port", 5432)),
            user=db_config.get("user", "postgres"),
            password=password or "",
            database=db_config.get("database", "astock"),
            mode_resolution=self._resolve_mode(),
        )

    @staticmethod
    def _resolve_mode() -> DatabaseModeResolution | None:
        try:
            return ConfigHandler.resolve_database_mode()
        except Exception as e:
            logger.warning("[DatabaseConfigVM] resolve_database_mode failed: %s", DataSanitizer.sanitize_error(e))
            return None

    # --- Mode / preference queries (View 通过 VM 访问, 避免直接 import ConfigHandler) ---

    @property
    def is_embedded_mode(self) -> bool:
        """是否为 embedded 模式 (P3-13: View 通过 VM 访问, 避免直接 import ConfigHandler)."""
        return ConfigHandler.is_embedded_mode()

    def load_show_advanced(self) -> bool:
        """读取 db_show_advanced 配置 (P3-13: View 通过 VM 访问)."""
        return bool(ConfigHandler.load_config().get("db_show_advanced", False))

    async def save_show_advanced(self, value: bool) -> None:
        """持久化 db_show_advanced 配置 (P3-13: View 通过 VM 访问; R16: IO offload).

        Raises:
            asyncio.CancelledError: 取消时显式传播 (R2).
        """
        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.save_config, {"db_show_advanced": value})

    # --- Mode switching commands (UIR-F15-001) ---

    def _mode_switch_block_reason(self, target: DatabaseMode) -> Message | None:
        """返回禁止写入模式偏好原因；None 表示可写入。"""
        resolution = self._state.mode_resolution
        if resolution is not None and resolution.env_mode is not None and resolution.env_mode is not target:
            return Message("settings_db_mode_env_forced", {"mode": resolution.env_mode.value})
        if self._has_active_database_operation():
            return Message("settings_db_mode_busy")
        return None

    @staticmethod
    def _has_active_database_operation() -> bool:
        """同步/迁移/备份进行中禁止切换模式（lazy import 避免 UI 层加载时依赖 services）。"""
        from services.embedded_pg_maintenance_service import EmbeddedPgMaintenanceService
        from services.task_manager import TaskManager, TaskStatus

        if EmbeddedPgMaintenanceService.has_active_operation():
            return True
        return any(task.status in (TaskStatus.QUEUED, TaskStatus.RUNNING) for task in TaskManager().get_all_tasks())

    def _refresh_mode_resolution(self) -> None:
        resolution = self._resolve_mode()
        if resolution is None:
            return
        if self._active_mode is not None:
            # 保持「当前生效模式 = 进程启动模式」，只刷新 configured_mode（待生效偏好）
            resolution = replace(resolution, mode=self._active_mode, source=self._active_source or "config")
        self._set_state(mode_resolution=resolution)

    async def _persist_database_mode(self, target: DatabaseMode) -> bool:
        """持久化下次启动模式偏好（严格成功判定，失败不得报成功）。"""
        blocked = self._mode_switch_block_reason(target)
        if blocked is not None:
            self._show_warning(blocked)
            return False
        saved = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.save_database_mode, target)
        if saved is not True:
            logger.error(
                "[DatabaseConfigVM] save_database_mode(%s) returned %r; mode not persisted", target.value, saved
            )
            self._show_error(Message("settings_db_mode_save_failed"))
            return False
        self._refresh_mode_resolution()
        return True

    async def switch_to_embedded_mode(self) -> bool:
        """切回内置 PostgreSQL 模式（重启后生效）。"""
        if self._state.is_saving:
            self._show_warning(Message("db_saving_in_progress"))
            return False
        if not await self._persist_database_mode(DatabaseMode.EMBEDDED):
            return False
        resolution = self._state.mode_resolution
        if resolution is not None and resolution.restart_required:
            self._show_success(Message("settings_db_mode_saved_restart"))
        else:
            self._show_success(Message("db_msg_saved"))
        return True

    # --- Update commands ---

    def update_host(self, value: str) -> None:
        self._set_state(host=value)
        self._notify_on_change()

    def update_port(self, value: str) -> None:
        self._set_state(port=value)
        self._notify_on_change()

    def update_user(self, value: str) -> None:
        self._set_state(user=value)
        self._notify_on_change()

    def update_password(self, value: str) -> None:
        self._set_state(password=value)
        self._notify_on_change()

    def update_database(self, value: str) -> None:
        self._set_state(database=value)
        self._notify_on_change()

    def update_create_if_not_exists(self, value: bool) -> None:
        self._set_state(create_if_not_exists=value)
        self._notify_on_change()

    # --- get_config / set_config ---

    def get_config(self) -> dict:
        """返回配置字典，port 解析为 int（无效时 fallback 5432）。"""
        try:
            port = int((self._state.port or "").strip() or 5432)
        except (ValueError, TypeError):
            port = 5432
        return {
            "host": self._state.host.strip(),
            "port": port,
            "user": self._state.user.strip(),
            "password": self._state.password,
            "database": self._state.database.strip(),
            "create_if_not_exists": self._state.create_if_not_exists,
        }

    def set_config(self, config: dict) -> None:
        """批量更新配置字段。"""
        self._set_state(
            host=config.get("host", "localhost"),
            port=str(config.get("port", 5432)),
            user=config.get("user", "postgres"),
            password=config.get("password", ""),
            database=config.get("database", "astock"),
            create_if_not_exists=config.get("create_if_not_exists", False),
        )

    # --- validate ---

    def validate(self) -> tuple[bool, Message | None]:
        """校验配置，返回 (是否有效, 错误 Message | None)。"""
        if not self._state.host.strip():
            return False, Message("wizard_err_host_required", {"default": "Host is required"})

        try:
            port = int((self._state.port or "").strip() or 5432)
            if not (1 <= port <= 65535):
                return False, Message("wizard_err_port_range", {"default": "Port must be between 1 and 65535"})
        except ValueError:
            return False, Message("wizard_err_port_number", {"default": "Port must be a number"})

        if not self._state.user.strip():
            return False, Message("wizard_err_user_required", {"default": "Username is required"})

        if not self._state.database.strip():
            return False, Message("wizard_err_db_required", {"default": "Database name is required"})

        return True, None

    # --- async commands ---

    @log_async_operation(operation_name="db_panel_test_connection", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def test_connection(self) -> bool:
        """测试数据库连接。"""
        is_valid, error_msg = self.validate()
        if not is_valid:
            assert error_msg is not None
            self._show_error(error_msg)
            return False

        if self._state.is_verifying:
            self._show_warning(Message("db_testing_in_progress"))
            return False

        self._set_state(is_verifying=True)
        self._show_warning(Message("db_testing"))
        if self._on_loading_change:
            self._on_loading_change(True)

        try:
            config = self.get_config()

            result = await DatabaseConfigService.test_connection(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            if result.status == ConnectionStatus.SUCCESS:
                self._show_success(self._raw_message(result.message))

                info = await DatabaseConfigService.get_database_info(
                    host=config["host"],
                    port=config["port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                )
                if info:
                    db_info = Message(
                        "db_info_format",
                        {
                            "version": info.version,
                            "size": info.size,
                            "tables": info.table_count,
                        },
                    )
                    self._set_state(db_info=db_info)

                if self._on_test_success_callback:
                    self._on_test_success_callback(config)

                return True

            elif result.status == ConnectionStatus.DATABASE_NOT_FOUND:
                if self._state.create_if_not_exists:
                    self._show_warning(
                        Message(
                            "db_will_create",
                            {"default": "Database not found. Will create on save."},
                        )
                    )
                    if self._on_test_success_callback:
                        self._on_test_success_callback(config)
                    return True
                else:
                    self._show_error(self._raw_message(result.message))
                    return False
            else:
                self._show_error(self._raw_message(result.message))
                return False

        except asyncio.CancelledError:  # R2: CancelledError 必须传播, 不被 except Exception 吞没
            raise
        except ValueError as e:
            logger.warning("[DatabaseConfigVM] ValueError: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[DatabaseConfigVM] ValueError traceback", exc_info=True)
            error_info = classify_error(e, context="db")
            self._show_error(get_error_message_key(error_info))
            return False
        except Exception as e:
            logger.error("[DatabaseConfigVM] Test connection failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[DatabaseConfigVM] Test connection failed traceback", exc_info=True)
            error_info = classify_error(e, context="db")
            self._show_error(get_error_message_key(error_info))
            return False
        finally:
            self._set_state(is_verifying=False)
            if self._on_loading_change:
                self._on_loading_change(False)

    @log_async_operation(operation_name="db_panel_save_config", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def save_config(self) -> bool:
        """保存数据库配置（4 步：test → create_if_not_exists → ensure_tables → save）。"""
        is_valid, error_msg = self.validate()
        if not is_valid:
            assert error_msg is not None
            self._show_error(error_msg)
            return False

        if self._state.is_saving:
            self._show_warning(Message("db_saving_in_progress"))
            return False

        self._show_warning(Message("db_saving"))
        self._set_state(is_saving=True)

        try:
            config = self.get_config()

            result = await DatabaseConfigService.test_connection(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            if result.status == ConnectionStatus.DATABASE_NOT_FOUND and config["create_if_not_exists"]:
                success, msg = await DatabaseConfigService.create_database(
                    host=config["host"],
                    port=config["port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                )
                if not success:
                    self._show_error(self._raw_message(msg))
                    return False
            elif result.status != ConnectionStatus.SUCCESS:
                self._show_error(self._raw_message(result.message))
                return False

            self._show_warning(Message("db_creating_tables"))

            success, msg = await DatabaseConfigService.ensure_tables_exist(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            if not success:
                self._show_error(self._raw_message(msg))
                return False

            saved = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_db_config,
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            # UIR-F15-002: 必须严格 is True 才报成功。save_db_config 在配置文件写入失败或
            # 密码安全存储失败时返回 False（含「普通字段已写、密码未写」的部分写入），
            # 吞掉返回值会造成假成功：重启后配置丢失，或目标/凭据不配套。
            if saved is not True:
                logger.error(
                    "[DatabaseConfigVM] save_db_config returned %r; configuration not persisted",
                    saved,
                )
                self._show_error(Message("db_save_failed"))
                return False

            # UIR-F15-001：设置页保存外置配置即切换为外置模式（重启后生效）；
            # 模式偏好写入失败必须报错，不得在配置已写后谎称已切换。
            if self._activate_mode_on_save:
                if not await self._persist_database_mode(DatabaseMode.EXTERNAL):
                    return False

            resolution = self._state.mode_resolution
            if self._activate_mode_on_save and resolution is not None and resolution.restart_required:
                self._show_success(Message("settings_db_mode_saved_restart"))
            else:
                self._show_success(Message("db_msg_saved"))

            if self._on_save_callback:
                self._on_save_callback(config)

            return True

        except asyncio.CancelledError:  # R2: CancelledError 必须传播, 不被 except Exception 吞没
            raise
        except Exception as e:
            logger.error("[DatabaseConfigVM] Save config failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[DatabaseConfigVM] Save config failed traceback", exc_info=True)
            error_info = classify_error(e, context="db")
            self._show_error(get_error_message_key(error_info))
            return False
        finally:
            self._set_state(is_saving=False)
