"""LocalModelConfigPanelViewModel — LocalModelConfigPanel 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（LocalModelConfigState frozen dataclass）
- subscribe/_notify 通知机制（View 通过 use_state + use_effect 订阅）
- commands 作为实例方法（消费方可直接调用 verify_model/save_config/reload_config）

VM 不感知 locale：status_message 用 Message dataclass 产出 (key, params)，
View 渲染时 I18n.get(msg.key, **msg.params)。

线程模型：
- verify_model/save_config 是 async，在 Flet 事件循环中执行
- ConfigHandler.save_local_ai_config 是同步 IO，通过 ThreadPoolManager offload
- ConfigHandler.get_local_ai_config 是同步 IO，在 VM 初始化时同步加载（轻量）
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

_RAW_MSG_KEY = "_raw_msg_"


@dataclass(frozen=True)
class LocalModelConfigState:
    """LocalModelConfigPanel 的不可变 state snapshot。

    timeout 保留原始字符串以支持 validate 检测无效输入（如 "abc"）。
    get_current_config / save_config 时解析为 int。
    n_gpu_layers = -1 表示 auto（由 gpu_auto flag 控制）。
    """

    # Config fields
    model_path: str = ""
    timeout: str = "300"
    n_threads: int = 4
    n_gpu_layers: int = -1  # -1 表示 auto
    n_batch: int = 512
    n_ctx: int = 4096
    flash_attn: bool = True
    # Status fields
    is_verifying: bool = False
    is_saving: bool = False
    status_message: Message | None = None
    status_type: str = "info"  # "success" / "error" / "warning" / "info"


class LocalModelConfigPanelViewModel(ObservableViewModelMixin[LocalModelConfigState]):
    """ViewModel for LocalModelConfigPanel.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (LocalModelConfigState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - VM 不感知 locale，status_message 用 Message 产出 (key, params)

    消费方（AIBrainTab/OnboardingWizard）直接实例化 VM 以调用 commands
    （verify_model/save_config/reload_config/get_current_config），View 通过
    use_state + use_effect 订阅 VM state 变化触发重渲染。
    """

    def __init__(
        self,
        on_verify_model: Callable[[str, dict], Awaitable[bool]] | None = None,
        on_verify_success: Callable | None = None,
        on_save: Callable | None = None,
        on_change: Callable | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_internal_loading: bool = True,
    ):
        self._on_verify_model = on_verify_model
        self._on_verify_success = on_verify_success
        self._on_save = on_save
        self._on_change = on_change
        self._on_loading_change = on_loading_change
        self._show_internal_loading = show_internal_loading
        self._state = LocalModelConfigState()
        self._subscribers: list[Callable[[LocalModelConfigState], None]] = []
        # 同步初始化 state（从 ConfigHandler 加载配置）
        self._load_config_to_state()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步）。"""
        local_cfg = ConfigHandler.get_local_ai_config()
        timeout_val = ConfigHandler.get_local_ai_timeout()
        self._state = replace(
            self._state,
            model_path=local_cfg.get("local_model_path", ""),
            timeout=str(timeout_val) if timeout_val is not None else "300",
            n_threads=local_cfg.get("n_threads", 4),
            n_gpu_layers=local_cfg.get("n_gpu_layers", -1),
            n_batch=local_cfg.get("n_batch", 512),
            n_ctx=local_cfg.get("n_ctx", 4096),
            flash_attn=local_cfg.get("flash_attn", True),
        )

    def reload_config(self) -> None:
        """重新从 ConfigHandler 加载配置到 state。"""
        self._load_config_to_state()
        self._notify()

    # --- Update commands ---

    def update_model_path(self, value: str) -> None:
        self._set_state(model_path=value)
        self._notify_on_change()

    def update_timeout(self, value: str) -> None:
        self._set_state(timeout=value)
        self._notify_on_change()

    def update_threads(self, value: int | float) -> None:
        self._set_state(n_threads=int(value))
        self._notify_on_change()

    def update_gpu_auto(self, auto: bool) -> None:
        """auto=True 时 n_gpu_layers=-1，否则 0。"""
        self._set_state(n_gpu_layers=-1 if auto else 0)
        self._notify_on_change()

    def update_gpu_layers(self, value: int | float) -> None:
        self._set_state(n_gpu_layers=int(value))
        self._notify_on_change()

    def update_batch(self, value: str) -> None:
        try:
            self._set_state(n_batch=int(value))
        except (ValueError, TypeError):
            pass
        self._notify_on_change()

    def update_ctx(self, value: str) -> None:
        try:
            self._set_state(n_ctx=int(value))
        except (ValueError, TypeError):
            pass
        self._notify_on_change()

    def update_flash_attn(self, value: bool) -> None:
        self._set_state(flash_attn=value)
        self._notify_on_change()

    def _notify_on_change(self) -> None:
        if self._on_change:
            self._on_change()

    # --- get_config / set_config ---

    def get_current_config(self) -> dict:
        """返回当前配置字典（消费方用于保存/验证）。"""
        timeout_str = (self._state.timeout or "").strip()
        try:
            timeout = int(timeout_str) if timeout_str else 300
        except ValueError:
            timeout = 300
        return {
            "model_path": (self._state.model_path or "").strip(),
            "timeout": timeout,
            "n_threads": self._state.n_threads,
            "n_gpu_layers": self._state.n_gpu_layers,
            "n_batch": self._state.n_batch,
            "n_ctx": self._state.n_ctx,
            "flash_attn": self._state.flash_attn,
        }

    def set_config(self, config: dict) -> None:
        """批量更新配置字段。"""
        gpu_layers = config.get("n_gpu_layers", -1)
        self._set_state(
            model_path=config.get("model_path", ""),
            timeout=str(config.get("timeout", 300)),
            n_threads=config.get("n_threads", 4),
            n_gpu_layers=gpu_layers,
            n_batch=config.get("n_batch", 512),
            n_ctx=config.get("n_ctx", 4096),
            flash_attn=config.get("flash_attn", True),
        )

    # --- validate ---

    def _validate_for_verify(self) -> tuple[bool, Message | None]:
        """验证模型路径和 timeout（用于 verify_model 前置校验）。"""
        model_path = (self._state.model_path or "").strip()
        if not model_path:
            return False, Message("wizard_err_model_required")

        if not os.path.exists(model_path):
            return False, Message("wizard_err_model_not_found")

        if not model_path.lower().endswith(".gguf"):
            return False, Message("wizard_err_model_format")

        timeout_str = (self._state.timeout or "").strip()
        try:
            timeout = int(timeout_str) if timeout_str else 300
            if not (0 < timeout <= 3600):
                raise ValueError("Range")
        except ValueError:
            return False, Message(
                "ai_snack_invalid_range",
                {
                    "field": "timeout",
                    "min": 1,
                    "max": 3600,
                },
            )

        return True, None

    # --- Status helpers ---

    def _show_success(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="success")

    def _show_error(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="error")

    def _show_warning(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="warning")

    @staticmethod
    def _raw_message(text: str) -> Message:
        """将动态字符串包装为 Message。I18n.get(_RAW_MSG_KEY, default=text) 返回 text。"""
        return Message(_RAW_MSG_KEY, {"default": text})

    # --- async commands ---

    async def verify_model(self) -> bool:
        """验证本地模型（含前置校验 + on_verify_model 回调 + 状态更新）。"""
        if self._state.is_verifying:
            logger.warning("[LocalModelConfigVM] Verification already in progress")
            return False

        is_valid, error_msg = self._validate_for_verify()
        if not is_valid:
            assert error_msg is not None
            self._show_error(error_msg)
            return False

        if self._on_verify_model is None:
            self._show_error(Message("wizard_err_model_load_failed"))
            return False

        self._set_state(is_verifying=True)
        self._show_warning(Message("wizard_model_loading"))
        if self._on_loading_change:
            self._on_loading_change(True)

        try:
            # Ensure Flet renders the loading mask even if the
            # model is cached and returns instantly
            await asyncio.sleep(0.5)

            config = self.get_current_config()
            success = await self._on_verify_model(self._state.model_path.strip(), config)

            if not success:
                self._show_error(Message("wizard_err_model_load_failed"))
                return False

            self._show_success(Message("wizard_model_configured"))
            if self._on_verify_success:
                self._on_verify_success()
            return True

        except asyncio.CancelledError:  # R2: CancelledError 必须传播, 不被 except Exception 吞没
            raise
        except Exception as e:
            logger.error("[LocalModelConfigVM] Model verification failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[LocalModelConfigVM] Model verification failed traceback", exc_info=True)
            self._show_error(Message("wizard_err_model_load_failed"))
            return False
        finally:
            self._set_state(is_verifying=False)
            if self._on_loading_change:
                self._on_loading_change(False)

    async def save_config(self) -> bool:
        """保存本地模型配置到 ConfigHandler（通过 ThreadPoolManager offload）。"""
        if self._state.is_saving:
            return False

        self._set_state(is_saving=True)
        if self._on_loading_change and self._show_internal_loading:
            self._on_loading_change(True)

        try:
            config = self.get_current_config()
            timeout = max(1, min(config["timeout"], 3600))

            def _save_sync() -> bool:
                return ConfigHandler.save_local_ai_config(
                    model_path=config["model_path"],
                    timeout=timeout,
                    n_threads=config["n_threads"],
                    n_batch=config["n_batch"],
                    n_ctx=config["n_ctx"],
                    flash_attn=config["flash_attn"],
                    n_gpu_layers=config["n_gpu_layers"],
                )

            success = await ThreadPoolManager().run_async(TaskType.IO, _save_sync)

            if not success:
                self._show_error(Message("sys_snack_save_err"))
                return False

            # 提交验证模式（如果活跃）—— 仅 Onboarding 流程走此路径
            from services.local_model_manager import LocalModelManager

            LocalModelManager.commit_verification_if_active()

            self._show_success(Message("wizard_model_configured"))
            if self._on_save:
                self._on_save()
            return True

        except asyncio.CancelledError:  # R2: CancelledError 必须传播, 不被 except Exception 吞没
            raise
        except Exception as e:
            logger.error("[LocalModelConfigVM] Save failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[LocalModelConfigVM] Save failed traceback", exc_info=True)
            self._show_error(Message("sys_snack_save_err"))
            return False
        finally:
            self._set_state(is_saving=False)
            if self._on_loading_change and self._show_internal_loading:
                self._on_loading_change(False)
