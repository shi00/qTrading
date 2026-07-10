"""LLMConfigPanelViewModel — LLMConfigPanel 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（LLMConfigState frozen dataclass）
- subscribe/_notify 通知机制（View 通过 use_state + use_effect 订阅）
- commands 作为实例方法（消费方可直接调用 save_config/verify_connection/refresh_models）

VM 不感知 locale：status_message 用 Message dataclass 产出 (key, params)，
View 渲染时 I18n.get(msg.key, **msg.params)。动态错误消息用 _RAW_MSG_KEY
+ default=params 传递，I18n.get 对不存在的 key 返回 default。

线程模型：
- verify_connection/save_config/refresh_models/update_provider 是 async
- ConfigHandler 同步 IO 通过 ThreadPoolManager.run_async(TaskType.IO, ...) offload（R16）
- httpx.AsyncClient 是 async-native IO，按原生 await 模型执行（R16 澄清）
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from ui.viewmodels import Message
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, get_error_message
from utils.llm_providers import AZURE_DEFAULT_API_VERSION, LLM_PROVIDERS, is_recommended_model
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

_RAW_MSG_KEY = "_raw_msg_"

MODELS_API_COMPATIBLE = {
    "openai",
    "deepseek",
    "qwen",
    "zhipu",
    "moonshot",
    "mistral",
    "minimax",
    "custom",
}

_REFRESH_TIMEOUT = 10.0
_MAX_CUSTOM_MODELS = 50


@dataclass(frozen=True)
class LLMConfigState:
    """LLMConfigPanel 的不可变 state snapshot。

    model_options / custom_model_options 不存入 state：
    - model_options 由 View 从 LLM_PROVIDERS + 当前 locale 构建（tag 需 i18n）
    - custom_model_options 存入 state（来自 ConfigHandler，非 locale 相关）
    """

    # Config fields
    provider: str = "deepseek"
    model: str = ""
    custom_model: str = ""
    base_url: str = ""
    api_key: str = ""
    azure_resource_name: str = ""
    azure_deployment_name: str = ""
    azure_api_version: str = AZURE_DEFAULT_API_VERSION
    # Derived flags
    is_azure: bool = False
    base_url_read_only: bool = True
    show_custom_model_input: bool = False
    show_refresh_button: bool = True
    # Status fields
    is_verifying: bool = False
    is_refreshing: bool = False
    is_saving: bool = False
    api_key_modified: bool = False
    status_message: Message | None = None
    status_type: str = "info"  # "success" / "error" / "warning" / "info"
    # Custom model history (来自 ConfigHandler，非 locale 相关)
    custom_model_options: tuple[str, ...] = ()


class LLMConfigPanelViewModel:
    """ViewModel for LLMConfigPanel.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (LLMConfigState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - VM 不感知 locale，status_message 用 Message 产出 (key, params)

    消费方（AIBrainTab/OnboardingWizard）直接实例化 VM 以调用 commands
    （save_config/verify_connection/reload_config），View 通过 use_state +
    use_effect 订阅 VM state 变化触发重渲染。
    """

    def __init__(
        self,
        on_test_connection: Callable[..., Awaitable[dict]],
        on_save: Callable[[], None] | None = None,
        on_reload_service: Callable[[], Awaitable[None]] | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
    ):
        self._on_test_connection = on_test_connection
        self._on_save = on_save
        self._on_reload_service = on_reload_service
        self._on_loading_change = on_loading_change
        self._state = LLMConfigState()
        self._subscribers: list[Callable[[LLMConfigState], None]] = []
        # 同步初始化 state（从 ConfigHandler 加载配置）
        self._load_config_to_state()

    # --- State snapshot + subscribe/_notify ---

    @property
    def state(self) -> LLMConfigState:
        """View 只读 state snapshot，不可变。"""
        return self._state

    def subscribe(self, callback: Callable[[LLMConfigState], None]) -> Callable[[], None]:
        """订阅 state 变化，返回退订函数。"""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        """state 变化后调所有订阅者，传入新 snapshot。"""
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes) -> None:
        """Update state fields and notify subscribers."""
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self) -> None:
        """Cleanup resources."""
        self._subscribers.clear()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步）。"""
        llm_config = ConfigHandler.get_llm_config()

        provider = llm_config.get("provider", "deepseek")
        model = llm_config.get("model", "")
        base_url = llm_config.get("base_url", "")
        api_key = llm_config.get("api_key", "")

        is_azure = provider == "azure"
        show_custom = False
        show_refresh = True
        base_url_read_only = True
        custom_model = ""
        azure_resource = ""
        azure_deployment = ""
        azure_version = AZURE_DEFAULT_API_VERSION
        custom_model_options: tuple[str, ...] = ()

        if is_azure:
            azure_resource = llm_config.get("azure_resource_name", "")
            azure_deployment = llm_config.get("azure_deployment_name", "") or model
            azure_version = llm_config.get("api_version", AZURE_DEFAULT_API_VERSION)
            base_url = ""
            show_refresh = False
        else:
            if provider == "custom":
                custom_model_options = self._load_custom_model_history_from_config(provider, llm_config)
                show_custom = True
                custom_model = model
                base_url_read_only = False
            else:
                models = LLM_PROVIDERS.get(provider, {}).get("models", [])
                model_ids = [m.get("id") for m in models]
                if model and model in model_ids:
                    pass  # model stays as-is
                elif model:
                    show_custom = True
                    custom_model = model
                    custom_model_options = self._load_custom_model_history_from_config(provider, llm_config)
                elif models:
                    recommended = next(
                        (m.get("id") for m in models if is_recommended_model(m)),
                        None,
                    )
                    model = recommended or models[0].get("id", "")

            provider_config = LLM_PROVIDERS.get(provider, {})
            base_url = base_url or provider_config.get("base_url", "")
            show_refresh = provider in MODELS_API_COMPATIBLE

        self._state = replace(
            self._state,
            provider=provider,
            model=model,
            custom_model=custom_model,
            base_url=base_url,
            api_key=api_key,
            azure_resource_name=azure_resource,
            azure_deployment_name=azure_deployment,
            azure_api_version=azure_version,
            is_azure=is_azure,
            base_url_read_only=base_url_read_only,
            show_custom_model_input=show_custom,
            show_refresh_button=show_refresh,
            api_key_modified=False,
            custom_model_options=custom_model_options,
        )

    def reload_config(self) -> None:
        """重新从 ConfigHandler 加载配置到 state。"""
        self._load_config_to_state()
        self._notify()

    @staticmethod
    def _load_custom_model_history_from_config(provider_id: str, llm_config: dict) -> tuple[str, ...]:
        """从 llm_config 加载指定 provider 的自定义模型历史。"""
        custom_models = llm_config.get("custom_models", {})
        return tuple(custom_models.get(provider_id, []))

    # --- Update commands ---

    def update_model(self, value: str) -> None:
        self._set_state(model=value)

    def update_custom_model(self, value: str) -> None:
        self._set_state(custom_model=value)

    def update_base_url(self, value: str) -> None:
        self._set_state(base_url=value)

    def update_api_key(self, value: str) -> None:
        self._set_state(api_key=value, api_key_modified=True)

    def update_azure_resource(self, value: str) -> None:
        self._set_state(azure_resource_name=value)

    def update_azure_deployment(self, value: str) -> None:
        self._set_state(azure_deployment_name=value)

    def update_azure_version(self, value: str) -> None:
        self._set_state(azure_api_version=value)

    @log_async_operation(
        operation_name="llm_panel_update_provider",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def update_provider(self, provider_id: str) -> None:
        """供应商变更：加载已存储凭证 + 更新派生标志。

        R16：ConfigHandler.get_provider_credential / get_llm_config 是同步 IO，
        通过 ThreadPoolManager offload。
        """
        provider = LLM_PROVIDERS.get(provider_id, {})
        provider_name = self._get_provider_name(provider, provider_id)

        # 加载该供应商已存储的专属凭证（不回退全局 Key）
        stored_cred = await ThreadPoolManager().run_async(
            TaskType.IO,
            ConfigHandler.get_provider_credential,
            provider_id,
            fallback_to_global=False,
        )
        stored_key = stored_cred.get("api_key", "") or ""
        stored_base_url = stored_cred.get("base_url", "")

        is_azure = provider_id == "azure"
        show_custom = False
        show_refresh = True
        base_url_read_only = True
        model = ""
        custom_model = ""
        base_url = ""
        custom_model_options: tuple[str, ...] = ()

        if is_azure:
            base_url = ""
            show_refresh = False
        elif provider_id == "custom":
            show_custom = True
            base_url = stored_base_url
            base_url_read_only = False
            show_refresh = True
            llm_config = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.get_llm_config)
            custom_model_options = self._load_custom_model_history_from_config(provider_id, llm_config)
        else:
            base_url = stored_base_url or provider.get("base_url", "")
            base_url_read_only = True
            show_refresh = provider_id in MODELS_API_COMPATIBLE
            models = provider.get("models", [])
            if models:
                recommended = next(
                    (m.get("id") for m in models if is_recommended_model(m)),
                    None,
                )
                model = recommended or models[0].get("id", "")

        self._set_state(
            provider=provider_id,
            model=model,
            custom_model=custom_model,
            base_url=base_url,
            api_key=stored_key,
            api_key_modified=False,
            is_azure=is_azure,
            base_url_read_only=base_url_read_only,
            show_custom_model_input=show_custom,
            show_refresh_button=show_refresh,
            custom_model_options=custom_model_options,
        )
        self._show_info(
            Message(
                "llm_switch_provider_hint",
                {"provider": provider_name},
            )
        )

    @staticmethod
    def _get_provider_name(provider: dict, provider_id: str) -> str:
        """获取供应商显示名称（locale 无关，View 层做 i18n 渲染）。

        VM 不感知 locale，直接返回中文 name；View 的 provider_options
        从 LLM_PROVIDERS + I18n 构建，此 name 仅用于 switch hint Message 参数。
        """
        return provider.get("name", provider_id)

    # --- get_current_config ---

    def get_current_config(self) -> dict:
        """返回当前配置字典。"""
        provider = self._state.provider
        base_url = self._normalize_base_url(self._state.base_url) if not self._state.is_azure else ""
        api_key = (self._state.api_key or "").strip()

        result: dict[str, object] = {
            "provider": provider,
            "base_url": base_url,
            "api_key": api_key,
        }

        if self._state.is_azure:
            result["model"] = self._state.azure_deployment_name
            result["api_version"] = self._state.azure_api_version
            result["azure_resource_name"] = self._state.azure_resource_name
            result["azure_deployment_name"] = self._state.azure_deployment_name
        else:
            result["model"] = self._state.model or self._state.custom_model

        return result

    # --- validate ---

    def _validate_azure_fields(self) -> tuple[bool, Message | None]:
        """校验 Azure 专用字段。"""
        if not self._state.azure_resource_name:
            return False, Message("llm_azure_need_resource")
        if not self._state.azure_deployment_name:
            return False, Message("llm_azure_need_deployment")
        return True, None

    # --- Status helpers ---

    def _show_success(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="success")

    def _show_error(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="error")

    def _show_warning(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="warning")

    def _show_info(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="info")

    @staticmethod
    def _raw_message(text: str) -> Message:
        """将动态字符串包装为 Message。

        I18n.get(_RAW_MSG_KEY, default=text) 对不存在的 key 返回 default。
        """
        return Message(_RAW_MSG_KEY, {"default": text})

    def _set_loading_state(self, loading: bool) -> None:
        if self._on_loading_change:
            self._on_loading_change(loading)

    # --- async commands ---

    @log_async_operation(
        operation_name="llm_panel_verify_connection",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def verify_connection(self) -> bool:
        """验证 LLM 连接（onboarding 向导使用）。

        R16：on_test_connection 回调由消费方注入，通常是 async-native HTTP。
        """
        api_key = (self._state.api_key or "").strip()

        if not api_key:
            self._show_warning(Message("llm_test_need_key"))
            return False

        model = self._state.model or self._state.custom_model
        if self._state.is_azure:
            model = self._state.azure_deployment_name

        if not self._state.provider or not model:
            self._show_error(Message("wizard_err_provider_model_required"))
            return False

        if self._state.is_verifying:
            return False

        self._set_state(is_verifying=True)
        self._set_loading_state(True)
        self._show_info(Message("llm_testing"))

        try:
            kwargs: dict[str, object] = {}
            if self._state.is_azure:
                is_valid, error_msg = self._validate_azure_fields()
                if not is_valid:
                    assert error_msg is not None
                    self._show_warning(error_msg)
                    return False
                model = self._state.azure_deployment_name
                if self._state.azure_api_version:
                    kwargs["api_version"] = self._state.azure_api_version
                kwargs["azure_resource_name"] = self._state.azure_resource_name
                base_url = ""
            else:
                base_url = self._normalize_base_url(self._state.base_url or "")

            result = await self._on_test_connection(
                provider=self._state.provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                **kwargs,
            )

            if result.get("success"):
                self._show_success(Message("llm_test_success"))
                return True

            self._show_error(self._raw_message(result.get("message", "common_err_unknown")))
            return False

        except Exception as ex:
            logger.error(
                "[LLMConfigVM] Verify connection error: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            error_info = classify_error(ex, context="llm")
            self._show_error(self._raw_message(get_error_message(error_info)))
            return False
        finally:
            self._set_state(is_verifying=False)
            self._set_loading_state(False)

    @log_async_operation(
        operation_name="llm_panel_save_config",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def save_config(self) -> bool:
        """保存 LLM 配置（custom_models 历史 + failover 同步 + Azure 字段 + api_key_modified reset）。

        R16：ConfigHandler 同步 IO 通过 ThreadPoolManager offload。
        """
        if self._state.is_saving:
            self._show_warning(Message("llm_saving_in_progress"))
            return False

        self._set_state(is_saving=True)
        self._show_info(Message("llm_saving"))

        try:
            provider = self._state.provider
            is_azure = self._state.is_azure

            kwargs: dict[str, object] = {}

            if is_azure:
                is_valid, error_msg = self._validate_azure_fields()
                if not is_valid:
                    assert error_msg is not None
                    self._show_warning(error_msg)
                    return False
                model = self._state.azure_deployment_name
                base_url = ""
                if self._state.azure_api_version:
                    kwargs["api_version"] = self._state.azure_api_version
                kwargs["azure_resource_name"] = self._state.azure_resource_name
                kwargs["azure_deployment_name"] = self._state.azure_deployment_name
            else:
                model = self._state.model or self._state.custom_model or ""
                base_url = self._normalize_base_url(self._state.base_url or "")

            # 未修改 API Key 时传 None，避免不必要的重加密
            api_key_to_save = (self._state.api_key or "").strip() if self._state.api_key_modified else None

            # custom_models 历史
            if not is_azure:
                custom_models_update = await ThreadPoolManager().run_async(
                    TaskType.IO,
                    self._build_custom_models_update,
                    provider or "",
                    model,
                    is_azure=False,
                )
                if custom_models_update is not None:
                    kwargs["custom_models"] = custom_models_update

            await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_llm_config,
                provider=provider,
                model=model or "",
                base_url=base_url,
                api_key=api_key_to_save,
                **kwargs,
            )

            # api_key_modified reset
            self._set_state(api_key_modified=False)

            # failover 同步
            await ThreadPoolManager().run_async(
                TaskType.IO,
                self._remove_primary_from_failover,
                provider,
            )
            llm_config = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.get_llm_config)
            custom_models = kwargs.get("custom_models", llm_config.get("custom_models", {}))
            await ThreadPoolManager().run_async(
                TaskType.IO,
                self._sync_provider_credential_to_failover,
                provider,
                api_key_to_save,
                base_url,
                custom_models.get(provider) if isinstance(custom_models, dict) else None,
            )

            # 重载服务
            if self._on_reload_service:
                await self._on_reload_service()

            self._show_success(Message("settings_verify_success"))

            if self._on_save:
                self._on_save()

            return True

        except Exception as ex:
            logger.error(
                "[LLMConfigVM] Save config error: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_error(Message("settings_save_failed"))
            return False
        finally:
            self._set_state(is_saving=False)

    @log_async_operation(
        operation_name="llm_panel_refresh_models",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def refresh_models(self) -> None:
        """通过 HTTP /models 刷新模型列表。

        httpx.AsyncClient 是 async-native IO，按原生 await 模型执行（R16 澄清）。
        """
        api_key = self._state.api_key
        base_url = self._normalize_base_url(self._state.base_url or "")

        if not api_key:
            self._show_warning(Message("llm_refresh_need_key"))
            return

        if not base_url:
            self._show_warning(Message("llm_refresh_need_url"))
            return

        self._set_state(is_refreshing=True)
        self._set_loading_state(True)
        self._show_info(Message("llm_refreshing"))

        try:
            import httpx
            from utils.proxy_manager import ProxyManager

            models_url = f"{base_url.rstrip('/')}/models"

            proxy_cfg = ProxyManager.get_httpx_proxy_config()
            async with httpx.AsyncClient(**proxy_cfg) as client:
                response = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=_REFRESH_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()

            models = data.get("data", [])
            model_ids = sorted([m["id"] for m in models if m.get("id")])

            if not model_ids:
                self._show_warning(Message("llm_refresh_empty"))
                return

            # 更新 model 为第一个可用模型（如果当前 model 不在列表中）
            new_model = self._state.model if self._state.model in model_ids else model_ids[0]
            self._set_state(model=new_model)

            self._show_success(Message("llm_refresh_success", {"count": len(model_ids)}))

        except Exception as ex:
            logger.error(
                "[LLMConfigVM] Refresh models error: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            error_info = classify_error(ex, context="llm")
            self._show_error(self._raw_message(get_error_message(error_info)))
        finally:
            self._set_state(is_refreshing=False)
            self._set_loading_state(False)

    # --- static helpers (migrated from imperative LLMConfigPanel) ---

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """Normalize base URL by stripping known API endpoint suffixes.

        Only removes trailing API endpoint paths (e.g., /chat/completions)
        while preserving essential base paths like /compatible-mode/v1.
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

    @staticmethod
    def _build_custom_models_update(
        provider: str,
        model: str,
        is_azure: bool = False,
    ) -> dict[str, list[str]] | None:
        """构建 custom_models 更新字典（历史记录去重 + 上限裁剪）。"""
        if not model or is_azure:
            return None
        if provider != "custom" and model in [m.get("id") for m in LLM_PROVIDERS.get(provider, {}).get("models", [])]:
            return None
        llm_config = ConfigHandler.get_llm_config()
        custom_models = llm_config.get("custom_models", {})
        if provider not in custom_models:
            custom_models[provider] = []
        if model not in custom_models[provider]:
            custom_models[provider].append(model)
            custom_models[provider] = custom_models[provider][-_MAX_CUSTOM_MODELS:]
        return custom_models

    @staticmethod
    def _remove_primary_from_failover(provider: str) -> None:
        """从 failover_models 中移除主供应商的模型。"""
        failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
        primary_prefix = f"{provider}/"
        new_failover_models = [m for m in failover_models if not m.startswith(primary_prefix)]
        if len(new_failover_models) != len(failover_models):
            ConfigHandler.save_config({"llm_failover_models": new_failover_models})
            logger.info(
                "[LLMConfigVM] Automatically removed primary provider %s models from failover list",
                provider,
            )

    @staticmethod
    def _sync_provider_credential_to_failover(
        provider: str,
        api_key: str | None,
        base_url: str,
        models: list[str] | None = None,
    ) -> None:
        """如果当前 provider 在 failover_models 中，同步凭证到 llm_provider_credentials。

        api_key 为 None 表示未修改，读取现有凭证避免覆盖。
        """
        try:
            failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
            for model in failover_models:
                if model.startswith(f"{provider}/"):
                    effective_key = api_key
                    if effective_key is None:
                        existing_cred = ConfigHandler.get_provider_credential(provider)
                        effective_key = existing_cred.get("api_key", "")

                    ConfigHandler.save_provider_credential(
                        provider=provider,
                        api_key=effective_key,
                        base_url=base_url,
                        models=models,
                    )
                    logger.debug("[LLMConfigVM] Synced credential to failover provider: %s", provider)
                    break
        except Exception as e:
            logger.debug(
                "[LLMConfigVM] Failed to sync failover credential: %s",
                DataSanitizer.sanitize_error(e),
            )
