"""FailoverConfigPanelViewModel — FailoverConfigPanel 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（FailoverConfigState frozen dataclass）
- subscribe/_notify 通知机制（View 通过 use_viewmodel hook 订阅）
- commands 作为实例方法（消费方可直接调用 reload_config/save_config）

VM 不感知 locale：status_message 用 Message dataclass 产出 (key, params)，
View 渲染时 I18n.get(msg.key, **msg.params)。动态错误消息用 _RAW_MSG_KEY
+ default=params 传递，I18n.get 对不存在的 key 返回 default。

线程模型：
- reload_config/open_add_dialog/open_edit_dialog/test_credential/confirm_credential/
  delete_item/move_item/validate_all 是 async
- ConfigHandler 同步 IO 通过 ThreadPoolManager.run_async(TaskType.IO, ...) offload（R16）
- on_test_connection 回调由消费方注入，通常是 async-native HTTP（R16 澄清）
- CancelledError 是 BaseException，不被 ``except Exception`` 捕获，自动传播（R2）
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.llm_providers import LLM_PROVIDERS
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailoverItem:
    """failover 列表项（不可变，用于 state snapshot）。"""

    provider: str
    model: str
    display_name: str
    has_credential: bool
    api_key_masked: str = ""

    def to_config_string(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class FailoverConfigState:
    """FailoverConfigPanel + ProviderCredentialDialog 的不可变 state snapshot。

    Panel 状态与 Dialog 状态合并到单个 VM（Dialog 由 Panel 消费，状态紧密耦合）。
    Dialog 字段以 ``dialog_`` 前缀标识。
    """

    # --- Panel state ---
    failover_items: tuple[FailoverItem, ...] = ()
    is_loading: bool = False
    status_message: Message | None = None
    status_type: str = "info"  # "success" / "error" / "warning" / "info"

    # --- Dialog state ---
    dialog_open: bool = False
    dialog_is_edit: bool = False
    dialog_edit_item: FailoverItem | None = None
    dialog_existing_providers: tuple[str, ...] = ()
    dialog_provider: str = ""
    dialog_model: str = ""
    dialog_custom_model: str = ""
    dialog_base_url: str = ""
    dialog_api_key: str = ""
    dialog_is_testing: bool = False
    dialog_is_saving: bool = False
    dialog_status_message: Message | None = None
    dialog_status_type: str = "info"


def _normalize_base_url(url: str) -> str:
    """规范化 base_url，去除用户可能粘贴的 API 端点后缀（纯函数）。

    仅移除尾部 API 端点路径（如 /chat/completions），保留 /compatible-mode/v1
    等基础路径。
    """
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = re.sub(r"/chat/completions$", "", url)
    url = re.sub(r"/completions$", "", url)
    url = re.sub(r"/embeddings$", "", url)
    return url


def _load_failover_items_sync() -> list[FailoverItem]:
    """同步加载 failover 配置（IO 部分），返回 FailoverItem 列表。

    作为 ThreadPoolManager.run_async 的 func 参数，在 IO 线程执行。
    """
    config = ConfigHandler.load_config()
    failover_models = config.get("llm_failover_models", [])

    items: list[FailoverItem] = []
    for entry in failover_models:
        if "/" not in entry:
            continue
        provider, model = entry.split("/", 1)
        pinfo = LLM_PROVIDERS.get(provider, {})
        cred = ConfigHandler.get_provider_credential(provider)
        has_key = bool(cred.get("api_key"))
        key_masked = ""
        if has_key and cred["api_key"]:
            key_masked = DataSanitizer.sanitize_token(cred["api_key"])

        items.append(
            FailoverItem(
                provider=provider,
                model=model,
                display_name=pinfo.get("name", provider),
                has_credential=has_key,
                api_key_masked=key_masked,
            )
        )
    return items


class FailoverConfigPanelViewModel(ObservableViewModelMixin[FailoverConfigState]):
    """ViewModel for FailoverConfigPanel + ProviderCredentialDialog.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (FailoverConfigState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - VM 不感知 locale，status_message 用 Message 产出 (key, params)

    消费方（AIBrainTab）直接实例化 VM 以调用 commands
    （reload_config/save_config），View 通过 use_viewmodel(vm=vm)
    hook 订阅 VM state 变化触发重渲染（外部 VM 模式）。
    """

    def __init__(
        self,
        on_test_connection: Callable[..., Awaitable[dict]],
        on_save: Callable[[], None] | None = None,
    ):
        self._on_test_connection = on_test_connection
        self._on_save = on_save
        self._state = FailoverConfigState()
        self._subscribers: list[Callable[[FailoverConfigState], None]] = []
        # 同步初始化 state（从 ConfigHandler 加载 failover 列表）
        self._state = replace(self._state, failover_items=tuple(_load_failover_items_sync()))

    # --- Status helpers ---

    def _show_panel_status(self, message: Message, status_type: str) -> None:
        self._set_state(status_message=message, status_type=status_type)

    def _show_dialog_status(self, message: Message, status_type: str) -> None:
        self._set_state(dialog_status_message=message, dialog_status_type=status_type)

    # --- Config loading ---

    @log_async_operation(operation_name="failover_reload_config", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def reload_config(self) -> None:
        """重新从 ConfigHandler 加载 failover 列表。

        R16：ConfigHandler.load_config/get_provider_credential 是同步 IO，
        通过 ThreadPoolManager offload。
        """
        try:
            items = await ThreadPoolManager().run_async(TaskType.IO, _load_failover_items_sync)
            self._set_state(failover_items=tuple(items))
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] reload_config failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_panel_status(Message("sys_snack_save_err"), "error")

    # --- Dialog open/close ---

    @log_async_operation(operation_name="failover_open_add_dialog", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def open_add_dialog(self) -> None:
        """打开添加供应商对话框。

        R16：ConfigHandler.load_config 是同步 IO，通过 ThreadPoolManager offload。
        """
        try:
            existing = [item.provider for item in self._state.failover_items]
            primary_provider = await ThreadPoolManager().run_async(
                TaskType.IO, lambda: ConfigHandler.load_config().get("llm_provider", "")
            )
            if primary_provider and primary_provider not in existing:
                existing.append(primary_provider)

            self._set_state(
                dialog_open=True,
                dialog_is_edit=False,
                dialog_edit_item=None,
                dialog_existing_providers=tuple(existing),
                dialog_provider="",
                dialog_model="",
                dialog_custom_model="",
                dialog_base_url="",
                dialog_api_key="",
                dialog_status_message=None,
                dialog_status_type="info",
            )
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] open_add_dialog failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_panel_status(Message("sys_snack_save_err"), "error")

    @log_async_operation(operation_name="failover_open_edit_dialog", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def open_edit_dialog(self, index: int) -> None:
        """打开编辑供应商对话框。

        R16：ConfigHandler.get_provider_credential 是同步 IO（keyring），
        通过 ThreadPoolManager offload。
        """
        try:
            items = list(self._state.failover_items)
            if index < 0 or index >= len(items):
                return
            item = items[index]
            cred = await ThreadPoolManager().run_async(
                TaskType.IO, ConfigHandler.get_provider_credential, item.provider
            )

            existing = [it.provider for it in items]
            primary_provider = await ThreadPoolManager().run_async(
                TaskType.IO, lambda: ConfigHandler.load_config().get("llm_provider", "")
            )
            if primary_provider and primary_provider not in existing:
                existing.append(primary_provider)

            pinfo = LLM_PROVIDERS.get(item.provider, {})
            base_url = cred.get("base_url", "") or pinfo.get("base_url", "")

            self._set_state(
                dialog_open=True,
                dialog_is_edit=True,
                dialog_edit_item=item,
                dialog_existing_providers=tuple(existing),
                dialog_provider=item.provider,
                dialog_model=item.model,
                dialog_custom_model="",
                dialog_base_url=base_url,
                dialog_api_key=cred.get("api_key", ""),
                dialog_status_message=None,
                dialog_status_type="info",
            )
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] open_edit_dialog failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_panel_status(Message("sys_snack_save_err"), "error")

    def close_dialog(self) -> None:
        """关闭对话框。"""
        self._set_state(
            dialog_open=False,
            dialog_edit_item=None,
            dialog_provider="",
            dialog_model="",
            dialog_custom_model="",
            dialog_base_url="",
            dialog_api_key="",
            dialog_status_message=None,
            dialog_status_type="info",
        )

    # --- Dialog field updates ---

    def update_dialog_provider(self, provider: str) -> None:
        """供应商变更：重置 model，设置默认 base_url。"""
        pinfo = LLM_PROVIDERS.get(provider, {})
        default_url = pinfo.get("base_url", "")
        self._set_state(
            dialog_provider=provider,
            dialog_model="",
            dialog_custom_model="",
            dialog_base_url=default_url,
        )

    def update_dialog_model(self, model: str) -> None:
        """选择模型时清空自定义模型输入。"""
        self._set_state(dialog_model=model, dialog_custom_model="")

    def update_dialog_custom_model(self, model: str) -> None:
        """自定义模型输入变更时清空下拉选择。"""
        self._set_state(dialog_custom_model=model, dialog_model="")

    def update_dialog_base_url(self, url: str) -> None:
        self._set_state(dialog_base_url=url)

    def update_dialog_api_key(self, key: str) -> None:
        self._set_state(dialog_api_key=key)

    # --- Dialog actions ---

    @log_async_operation(operation_name="failover_test_connection", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def test_credential(self) -> None:
        """测试当前 dialog 表单中的凭证。

        R16：on_test_connection 回调由消费方注入，通常是 async-native HTTP。
        """
        provider = self._state.dialog_provider
        model = self._state.dialog_custom_model or self._state.dialog_model
        base_url = self._state.dialog_base_url or ""
        api_key = self._state.dialog_api_key

        if not provider or not model or not api_key:
            return

        self._set_state(dialog_is_testing=True)
        try:
            result = await self._on_test_connection(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
            )
            if result.get("success"):
                self._show_dialog_status(Message("failover_test_success"), "success")
            else:
                self._show_dialog_status(
                    Message("failover_test_failed", {"detail": str(result.get("error", ""))}),
                    "error",
                )
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] test connection failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_dialog_status(
                Message("failover_test_failed", {"detail": DataSanitizer.sanitize_error(ex)}),
                "error",
            )
        finally:
            self._set_state(dialog_is_testing=False)

    @log_async_operation(operation_name="failover_save_credential", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def confirm_credential(self) -> None:
        """保存凭证并更新 failover 列表。

        R16：ConfigHandler 同步 IO 通过 ThreadPoolManager offload。
        """
        provider = self._state.dialog_provider
        model = self._state.dialog_custom_model or self._state.dialog_model
        base_url = self._state.dialog_base_url or ""
        api_key = self._state.dialog_api_key

        if not provider or not model:
            return

        # 规范化 base_url
        base_url = _normalize_base_url(base_url)

        # 新增模式下要求 API Key 非空
        if not self._state.dialog_is_edit and not api_key:
            self._show_dialog_status(Message("llm_test_need_key"), "warning")
            return

        self._set_state(dialog_is_saving=True)
        try:
            is_edit = self._state.dialog_is_edit
            edit_item = self._state.dialog_edit_item

            def _save_sync() -> tuple[dict | None, str]:
                """合并多次 IO 到单个闭包，减少 run_async 调用次数。"""
                # 编辑模式下清空 API Key 时查询原有凭证（用于警告提示）
                existing_cred = None
                if is_edit and not api_key:
                    existing_cred = ConfigHandler.get_provider_credential(provider)

                # 主供应商检查
                primary_provider = ConfigHandler.load_config().get("llm_provider", "")
                if provider == primary_provider:
                    return existing_cred, primary_provider

                # 保存凭证
                ConfigHandler.save_provider_credential(
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    models=[model],
                )

                # 加载并更新 failover 列表
                failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
                new_entry = f"{provider}/{model}"
                if is_edit and edit_item is not None:
                    old_entry = edit_item.to_config_string()
                    failover_models = [new_entry if m == old_entry else m for m in failover_models]
                else:
                    if new_entry not in failover_models:
                        failover_models.append(new_entry)

                ConfigHandler.save_config({"llm_failover_models": failover_models})
                return existing_cred, primary_provider

            existing_cred, primary_provider = await ThreadPoolManager().run_async(TaskType.IO, _save_sync)

            # 编辑模式下清空 API Key 时提示警告（允许用户有意清除）
            if is_edit and not api_key and existing_cred and existing_cred.get("api_key"):
                self._show_dialog_status(Message("failover_clear_key_warning"), "warning")

            if provider == primary_provider:
                self._show_dialog_status(Message("failover_primary_in_list"), "warning")
                return

            # 关闭对话框 + 重新加载列表
            self._set_state(
                dialog_open=False,
                dialog_edit_item=None,
                dialog_is_saving=False,
            )
            await self.reload_config()
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] confirm_credential failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_dialog_status(Message("sys_snack_save_err"), "error")
            self._set_state(dialog_is_saving=False)

    # --- Panel list operations ---

    @log_async_operation(operation_name="failover_delete_item", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def delete_item(self, index: int) -> None:
        """删除 failover 列表项。

        R16：ConfigHandler 同步 IO 通过 ThreadPoolManager offload。
        """
        try:
            items = list(self._state.failover_items)
            if index < 0 or index >= len(items):
                return
            entry = items[index].to_config_string()

            def _delete_sync() -> None:
                failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
                if entry in failover_models:
                    failover_models.remove(entry)
                    ConfigHandler.save_config({"llm_failover_models": failover_models})

            await ThreadPoolManager().run_async(TaskType.IO, _delete_sync)
            await self.reload_config()
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] delete_item failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_panel_status(Message("sys_snack_save_err"), "error")

    @log_async_operation(operation_name="failover_move_item", threshold_ms=PerfThreshold.DB_BULK_IO)
    async def move_item(self, index: int, direction: int) -> None:
        """移动 failover 列表项（上移/下移）。

        R16：ConfigHandler 同步 IO 通过 ThreadPoolManager offload。
        失败时回滚列表顺序，保持内存状态与配置一致。
        """
        items = list(self._state.failover_items)
        target = index + direction
        if index < 0 or index >= len(items) or target < 0 or target >= len(items):
            return

        # 先记录原始顺序，便于失败时回滚
        original_order = list(items)
        try:
            items[index], items[target] = items[target], items[index]
            ordered = [item.to_config_string() for item in items]

            await ThreadPoolManager().run_async(
                TaskType.IO, ConfigHandler.save_config, {"llm_failover_models": ordered}
            )
            self._set_state(failover_items=tuple(items))
        except Exception as ex:
            # 回滚列表顺序
            self._set_state(failover_items=tuple(original_order))
            logger.error(
                "[FailoverConfigVM] move_item failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_panel_status(Message("sys_snack_save_err"), "error")

    @log_async_operation(operation_name="failover_validate_all", threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def validate_all(self) -> None:
        """批量校验 failover 凭证完整性。

        R16：ConfigHandler.validate_failover_credentials 是同步 IO，
        通过 ThreadPoolManager offload。
        """
        try:
            missing = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.validate_failover_credentials)
            if missing:
                providers_str = ", ".join(missing)
                self._show_panel_status(
                    Message("failover_validation_missing", {"providers": providers_str}),
                    "warning",
                )
            else:
                self._show_panel_status(Message("failover_validation_complete"), "success")
        except Exception as ex:
            logger.error(
                "[FailoverConfigVM] validate_all failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_panel_status(Message("sys_snack_save_err"), "error")

    def save_config(self) -> None:
        """保存按钮回调（同步，触发 on_save 回调）。"""
        if self._on_save:
            self._on_save()
        self._show_panel_status(Message("settings_verify_success"), "success")
