"""LLM 域：llm 配置 / failover / local_ai / prompt / 策略预设。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 llm 相关方法，仅按域搬移、不改行为。本模块所有共享状态与
跨方法访问一律经 ``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

import copy
import os

from utils import config_handler as cfg
from utils.config_models import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT
from utils.llm_providers import AZURE_DEFAULT_API_VERSION

DEFAULTS = cfg.ConfigHandler.DEFAULT_CONFIG


def get_llm_provider() -> str:
    return cfg.ConfigHandler.get_typed("llm_provider", str, DEFAULTS["llm_provider"])


def save_llm_config(provider: str, model: str, base_url: str, api_key: str | None = None, **kwargs) -> bool:
    """保存 LLM 完整配置。

    Args:
        provider, model, base_url: LLM 供应商/模型/URL。
        api_key: None 保持现有密钥，空字符串清除，非空更新。
        **kwargs: 扩展字段 (azure 的 api_version 等)。
    """
    provider = provider.strip()
    model = model.strip()
    base_url = base_url.strip()
    if api_key is not None:
        api_key = api_key.strip()

    config_update: dict[str, object] = {
        "llm_provider": provider,
        "llm_model": model,
    }

    provider_extras = {}

    if provider == "azure":
        azure_extras = {}
        api_version = kwargs.get("api_version", AZURE_DEFAULT_API_VERSION)
        azure_extras["api_version"] = api_version

        resource_name = kwargs.get("azure_resource_name", "")
        deployment_name = kwargs.get("azure_deployment_name", "")

        if resource_name:
            azure_extras["resource_name"] = resource_name
            base_url = f"https://{resource_name}.openai.azure.com"

        if deployment_name:
            azure_extras["deployment_name"] = deployment_name

        if azure_extras:
            provider_extras["azure"] = azure_extras

    config_update["llm_base_url"] = base_url

    if "custom_models" in kwargs:
        provider_extras["custom_models"] = kwargs["custom_models"]
        config_update["llm_custom_models"] = kwargs["custom_models"]

    if provider_extras:
        config_update["llm_provider_extras"] = provider_extras
    else:
        config_update["llm_provider_extras"] = {}

    if not cfg.ConfigHandler.save_config(config_update):
        return False

    if api_key is not None:
        if api_key and not os.environ.get(cfg.ENV_FALLBACK_MAP["ai_api_key"]):
            try:
                cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "ai_api_key", api_key)
            # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
            except Exception as e:
                cfg.logger.warning(
                    "Keyring save failed: %s. Falling back to SecurityManager.", cfg.DataSanitizer.sanitize_error(e)
                )
                try:
                    encrypted_key = cfg.SecurityManager.encrypt_data(api_key)
                    if not cfg.ConfigHandler.save_config({"ai_api_key": encrypted_key}):
                        return False
                except cfg.SecurityError as se:
                    cfg.logger.error(
                        "Cannot securely store ai_api_key: %s. Please use environment variable AI_API_KEY instead.",
                        cfg.DataSanitizer.sanitize_error(se),
                    )
                    return False
                # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
                except Exception as enc_err:
                    cfg.logger.error("Failed to encrypt ai_api_key: %s", cfg.DataSanitizer.sanitize_error(enc_err))
                    return False
        elif not api_key and not os.environ.get(cfg.ENV_FALLBACK_MAP["ai_api_key"]):
            try:
                cfg.keyring.delete_password(cfg.KEYRING_SERVICE_NAME, "ai_api_key")
            # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
            except Exception as e:
                cfg.logger.debug(
                    "Keyring ai_api_key deletion skipped: %s",
                    cfg.DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
            if not cfg.ConfigHandler.save_config({"ai_api_key": ""}):
                return False

    return True


def get_llm_config() -> dict:
    """获取 LLM 完整配置。

    Returns:
        {provider, model, base_url, api_key, api_version, azure_resource_name,
         azure_deployment_name, custom_models, custom_model_contexts}
    """
    config = cfg.ConfigHandler.load_config()

    api_key = os.environ.get(cfg.ENV_FALLBACK_MAP["ai_api_key"])
    if api_key:
        cfg.DataSanitizer.register_secret(api_key)

    if not api_key:
        try:
            api_key = cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, "ai_api_key")
            if api_key:
                cfg.DataSanitizer.register_secret(api_key)
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as exc:
            cfg.logger.debug("Keyring get_password for ai_api_key failed: %s", cfg.DataSanitizer.sanitize_error(exc))

    if not api_key:
        encrypted = config.get("ai_api_key", "")
        api_key = cfg.ConfigHandler._try_decrypt(encrypted)
        if api_key:
            cfg.DataSanitizer.register_secret(api_key)
            try:
                cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "ai_api_key", api_key)
                cfg.ConfigHandler._persist_migration({"ai_api_key": ""}, "clear ai_api_key after keyring migration")
                cfg.logger.info("Migrated ai_api_key from config to keyring and cleared legacy value")
            # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
            except Exception as exc:
                cfg.logger.debug(
                    "[ConfigHandler] Keyring migration for ai_api_key skipped: %s",
                    cfg.DataSanitizer.sanitize_error(exc),
                )

    provider = config.get("llm_provider", DEFAULTS["llm_provider"])
    model = config.get("llm_model", DEFAULTS["llm_model"])
    base_url = config.get("llm_base_url", "")

    if not base_url:
        from utils.llm_providers import LLM_PROVIDERS

        provider_config = LLM_PROVIDERS.get(provider, {})
        base_url = provider_config.get("base_url", "")

    provider_extras = config.get("llm_provider_extras", {})

    api_version = AZURE_DEFAULT_API_VERSION
    azure_resource_name = ""
    azure_deployment_name = ""

    if "azure" in provider_extras:
        azure_config = provider_extras["azure"]
        api_version = azure_config.get("api_version", AZURE_DEFAULT_API_VERSION)
        azure_resource_name = azure_config.get("resource_name", "")
        azure_deployment_name = azure_config.get("deployment_name", "")
    else:
        api_version = config.get("llm_api_version", AZURE_DEFAULT_API_VERSION)
        azure_resource_name = config.get("llm_azure_resource_name", "")
        azure_deployment_name = config.get("llm_azure_deployment_name", "")

    custom_models: dict[str, list[str]] = {}
    raw_custom_models = config.get("llm_custom_models") or provider_extras.get("custom_models", {})
    for provider_id, value in raw_custom_models.items():
        if isinstance(value, list):
            custom_models[provider_id] = [str(m) for m in value]
        elif isinstance(value, dict) and "models" in value:
            models_list = value.get("models", [])
            if isinstance(models_list, list):
                custom_models[provider_id] = [str(m) for m in models_list]

    custom_model_contexts = copy.deepcopy(config.get("llm_custom_model_contexts", {}))

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "api_version": api_version,
        "azure_resource_name": azure_resource_name,
        "azure_deployment_name": azure_deployment_name,
        "custom_models": copy.deepcopy(custom_models),
        "custom_model_contexts": custom_model_contexts,
    }


def get_failover_config() -> dict:
    """P1-12: 获取多供应商 fallback 配置。

    Returns:
        {"primary": "provider/model", "fallbacks": list[str], "primary_config": dict}
    """
    llm_config = cfg.ConfigHandler.get_llm_config()
    provider = llm_config.get("provider", "")
    model = llm_config.get("model", "")
    primary = f"{provider}/{model}" if provider else model

    config = cfg.ConfigHandler.load_config()
    fallbacks = config.get("llm_failover_models", [])

    if not isinstance(fallbacks, list):
        fallbacks = []

    return {
        "primary": primary,
        "fallbacks": fallbacks,
        "primary_config": llm_config,
    }


def get_llm_config_for_provider(provider: str) -> dict:
    """获取指定供应商的 LLM 配置（用于跨供应商 failover）。

    Returns:
        {provider, model, api_key, base_url, models}
    """
    cred = cfg.ConfigHandler.get_provider_credential(provider)

    if not cred["models"]:
        cfg.logger.warning("[ConfigHandler] No models found for provider '%s', returning empty model", provider)

    return {
        "provider": provider,
        "model": cred["models"][0] if cred["models"] else "",
        "api_key": cred["api_key"],
        "base_url": cred["base_url"],
        "models": cred["models"],
    }


def get_local_ai_timeout() -> int | None:
    """Get local AI inference timeout in seconds.

    Returns:
        int | None: Timeout seconds, or None if not configured (wait indefinitely).
    """
    try:
        val = cfg.ConfigHandler.get_setting("local_model_timeout")
        return int(val) if val is not None else None  # type: ignore[arg-type]  # val is Any from get_setting
    except (ValueError, TypeError):
        return None


def set_local_ai_timeout(seconds: int) -> bool:
    """Set local AI inference timeout (1-3600s)"""
    val = max(1, min(seconds, 3600))
    return cfg.ConfigHandler.save_config({"local_model_timeout": val})


def get_local_ai_config() -> dict:
    """Get local AI configuration."""
    return {
        "local_model_path": cfg.ConfigHandler.get_typed("local_model_path", str, ""),
        "local_model_timeout": cfg.ConfigHandler.get_typed("local_model_timeout", int, DEFAULTS["local_model_timeout"]),
        "n_threads": cfg.ConfigHandler.get_typed("local_n_threads", int, DEFAULTS["local_n_threads"]),
        "n_batch": cfg.ConfigHandler.get_typed("local_n_batch", int, DEFAULTS["local_n_batch"]),
        "n_ctx": cfg.ConfigHandler.get_typed("local_n_ctx", int, DEFAULTS["local_n_ctx"]),
        "flash_attn": cfg.ConfigHandler.get_typed("local_flash_attn", bool, DEFAULTS["local_flash_attn"]),
        "n_gpu_layers": cfg.ConfigHandler.get_typed("local_n_gpu_layers", int, DEFAULTS["local_n_gpu_layers"]),
    }


def save_local_ai_config(model_path: str, timeout: int = 30, **kwargs) -> bool:
    """Save local AI configuration."""
    cfg_data = {"local_model_path": model_path, "local_model_timeout": timeout}

    if "n_threads" in kwargs:
        cfg_data["local_n_threads"] = kwargs["n_threads"]
    if "n_batch" in kwargs:
        cfg_data["local_n_batch"] = kwargs["n_batch"]
    if "n_ctx" in kwargs:
        cfg_data["local_n_ctx"] = kwargs["n_ctx"]
    if "flash_attn" in kwargs:
        cfg_data["local_flash_attn"] = kwargs["flash_attn"]
    if "n_gpu_layers" in kwargs:
        cfg_data["local_n_gpu_layers"] = kwargs["n_gpu_layers"]

    return cfg.ConfigHandler.save_config(cfg_data)


def get_ai_system_prompt():
    config = cfg.ConfigHandler.load_config()
    return config.get("ai_system_prompt", DEFAULT_AI_PROMPT)


def save_ai_system_prompt(prompt):
    from utils.prompt_guard import sanitize_prompt, validate_prompt

    if prompt:
        is_valid, _ = validate_prompt(prompt)
        if not is_valid:
            return False
        prompt = sanitize_prompt(prompt)
    return cfg.ConfigHandler.save_config({"ai_system_prompt": prompt})


def get_strategy_prompt(strategy_key):
    """Get user-customized prompt for a specific strategy."""
    config = cfg.ConfigHandler.load_config()
    key = f"ai_strategy_prompt_{strategy_key}"
    return config.get(key, None)


def set_strategy_prompt(strategy_key, prompt):
    """Save user-customized prompt for a specific strategy."""
    from utils.prompt_guard import sanitize_prompt, validate_prompt

    if prompt:
        is_valid, _ = validate_prompt(prompt)
        if not is_valid:
            return False
        prompt = sanitize_prompt(prompt)
    key = f"ai_strategy_prompt_{strategy_key}"
    return cfg.ConfigHandler.save_config({key: prompt})


def get_ai_news_prompt():
    """Get News Classification Prompt (returns Default if not set)."""
    config = cfg.ConfigHandler.load_config()
    val = config.get("ai_news_prompt", None)
    return val if val else DEFAULT_NEWS_PROMPT


def set_ai_news_prompt(prompt):
    """Save news classification prompt with validation and sanitization."""
    if prompt:
        from utils.prompt_guard import sanitize_prompt, validate_prompt

        is_valid, _ = validate_prompt(prompt)
        if not is_valid:
            return False
        prompt = sanitize_prompt(prompt)
    return cfg.ConfigHandler.save_config({"ai_news_prompt": prompt})


def get_strategy_presets(strategy_key: str) -> dict[str, dict]:
    """获取策略的已保存参数预设 (Task 4.1).

    Returns:
        dict: {preset_name: params_dict}, 无预设时返回空 dict.
    """
    config = cfg.ConfigHandler.load_config()
    key = f"strategy_presets_{strategy_key}"
    presets = config.get(key, {})
    return presets if isinstance(presets, dict) else {}


def save_strategy_preset(strategy_key: str, name: str, params: dict) -> bool:
    """保存命名参数预设 (Task 4.1). 重名覆盖.

    Returns:
        bool: 保存成功返回 True.
    """
    config = cfg.ConfigHandler.load_config()
    key = f"strategy_presets_{strategy_key}"
    presets = config.get(key, {})
    if not isinstance(presets, dict):
        presets = {}
    presets[name] = params
    return cfg.ConfigHandler.save_config({key: presets})


def delete_strategy_preset(strategy_key: str, name: str) -> bool:
    """删除命名参数预设 (Task 4.1).

    Returns:
        bool: 删除成功 (预设存在) 返回 True, 不存在返回 False.
    """
    config = cfg.ConfigHandler.load_config()
    key = f"strategy_presets_{strategy_key}"
    presets = config.get(key, {})
    if not isinstance(presets, dict) or name not in presets:
        return False
    del presets[name]
    return cfg.ConfigHandler.save_config({key: presets})


def get_ai_max_candidates():
    return cfg.ConfigHandler.get_typed("ai_max_candidates", int, DEFAULTS["ai_max_candidates"])


def set_ai_max_candidates(val):
    return cfg.ConfigHandler.set_typed("ai_max_candidates", int(val))


def get_ai_free_text_max_len():
    """UX-2.2: AI 自由文本最大长度 (默认 1000，范围 100-10000)。"""
    return cfg.ConfigHandler.get_typed("ai_free_text_max_len", int, DEFAULTS["ai_free_text_max_len"])


def get_ai_max_concurrent_analysis():
    val = cfg.ConfigHandler.get_typed("ai_max_concurrent_analysis", int, DEFAULTS["ai_max_concurrent_analysis"])
    return max(1, min(val, 10))


def set_ai_max_concurrent_analysis(val):
    safe_val = max(1, min(int(val), 10))
    return cfg.ConfigHandler.set_typed("ai_max_concurrent_analysis", safe_val)


def get_ai_news_max_concurrent():
    # NOTE: ai_news_max_concurrent 不在 AppConfig 字段中，保持内联默认 1（非 AppConfig 第二来源）。
    val = cfg.ConfigHandler.get_typed("ai_news_max_concurrent", int, 1)
    return max(1, min(val, 5))


def set_ai_news_max_concurrent(val):
    safe_val = max(1, min(int(val), 5))
    return cfg.ConfigHandler.set_typed("ai_news_max_concurrent", safe_val)


def get_strategy_min_turnover():
    return cfg.ConfigHandler.get_typed("strategy_min_turnover", float, DEFAULTS["strategy_min_turnover"])


def set_strategy_min_turnover(val):
    return cfg.ConfigHandler.set_typed("strategy_min_turnover", float(val))
