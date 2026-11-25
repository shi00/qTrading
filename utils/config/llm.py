"""LLM 域：llm 配置 / failover / local_ai / prompt / 策略预设。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 llm 相关方法，仅按域搬移、不改行为。本模块所有共享状态与
跨方法访问一律经 ``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

import copy
import os

from core.prompt_base import RESEARCH_SUMMARY_PROMPT_DEFAULT
from utils import config_handler as cfg
from utils.config import storage
from utils.config_models import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT, PresetDeleteOutcome
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

    env 覆盖语义（A-02）：环境变量 ``AI_API_KEY`` 存在时，keyring 写入/清除被
    有意跳过（运行时凭据永远优先读 env），此时提交的 api_key **不会持久化**，
    函数仍返回 True（provider/model/base_url 等非敏感字段照常落盘），并发出
    warning 提示该降级——调用方应依赖日志而非返回值感知密钥未写入 keyring。
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
                    # D02-F01：与 legacy 密钥清理串行（加密 + 提交配置在同一临界区）。
                    with cfg.SecurityManager.credential_write_lock():
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
        elif api_key and os.environ.get(cfg.ENV_FALLBACK_MAP["ai_api_key"]):
            # A-02: env 覆盖时跳过 keyring 写入是有意设计（运行时永远读 env），
            # 但须告警避免静默假报成功——用户提交的密钥实际未持久化。消息仅含
            # 环境变量名，不含密钥明文（R9）。
            cfg.logger.warning(
                "API key not persisted to keyring: %s environment variable override is in effect "
                "(the submitted key was skipped; env value takes precedence at runtime).",
                cfg.ENV_FALLBACK_MAP["ai_api_key"],
            )

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


def get_llm_config_for_provider(provider: str, *, allow_global_fallback: bool = False) -> dict:
    """获取指定供应商的 LLM 配置（用于跨供应商 failover）。

    Args:
        provider: 供应商 ID（如 "qwen", "deepseek", "openai"）。
        allow_global_fallback: 默认 False。全局 ``ai_api_key`` 语义上属于当前主供应商，
            跨供应商 failover 复用会把 A 的凭证发送到 B 的 endpoint（凭证跨域泄露，
            D8-1）。仅当调用方确知 provider 就是主供应商时才可传 True。

    Returns:
        {provider, model, api_key, base_url, models}
    """
    cred = cfg.ConfigHandler.get_provider_credential(provider, fallback_to_global=allow_global_fallback)

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


def save_ai_tuning_and_prompts(
    max_candidates: int,
    min_turnover: float,
    local_only: bool,
    ai_prompt: str,
    news_prompt: str,
) -> bool:
    """原子保存 AI 调优参数与提示词 (T3)。

    调优参数 (ai_max_candidates / strategy_min_turnover / ai_local_only_mode)
    与双 prompt (ai_system_prompt / ai_news_prompt) 合并为**单次**
    ``save_config`` 调用（单次调用自身原子：锁内读盘→合并→单次原子写），
    替换原「三次独立写」链路——原先任一步失败即提前返回，已写步骤不回滚、
    无部分提交标记，配置处于半更新状态（R21 邻域：部分成功被伪装成已保存）。

    prompt 校验/清洗语义与 :func:`save_ai_system_prompt` / :func:`set_ai_news_prompt`
    一致：非空时 validate + sanitize，校验失败返回 False 且 **整体未提交**
    （save_config 未被调用，含调优参数在内的所有键均不落盘）。

    Returns:
        True 全部保存成功; False 整体未提交 (prompt 校验失败 / 磁盘不可读或
        安全子域损坏 fail-closed / 原子写失败)。
    """
    payload: dict[str, object] = {
        "ai_max_candidates": max_candidates,
        "strategy_min_turnover": min_turnover,
        "ai_local_only_mode": local_only,
    }
    from utils.prompt_guard import sanitize_prompt, validate_prompt

    for key, prompt in (("ai_system_prompt", ai_prompt), ("ai_news_prompt", news_prompt)):
        if prompt:
            is_valid, _ = validate_prompt(prompt)
            if not is_valid:
                return False
            prompt = sanitize_prompt(prompt)
        payload[key] = prompt
    return cfg.ConfigHandler.save_config(payload)


# --- F35 / J26 研究角色 Prompt（默认正文只读，用户自定义为「追加」） ---


def _role_prompt_append_key(role_key: str) -> str:
    return f"ai_role_prompt_append_{role_key}"


def _role_prompt_version_key(role_key: str) -> str:
    return f"ai_role_prompt_version_{role_key}"


def get_ai_role_prompt_append(role_key: str) -> str:
    """读取角色追加内容；缺键/非字符串一律按「空追加」处理（R21：不伪造内容）。"""
    config = cfg.ConfigHandler.load_config()
    value = config.get(_role_prompt_append_key(role_key), "")
    return value if isinstance(value, str) else ""


def get_ai_role_prompt_version(role_key: str) -> str:
    """读取该角色最近一次保存绑定的版本；缺键/非字符串按「未保存」处理。"""
    config = cfg.ConfigHandler.load_config()
    value = config.get(_role_prompt_version_key(role_key), "")
    return value if isinstance(value, str) else ""


def save_ai_role_prompts(appends: dict[str, str], versions: dict[str, str]) -> bool:
    """原子保存各研究角色的追加内容与绑定版本（F35 / J26）。

    - 追加内容为「默认正文之后的追加」，不替换固定安全规则与输出契约；
    - 非空追加沿用 ``validate_prompt`` + ``sanitize_prompt`` 语义，任一校验失败
      返回 False 且整体未提交（不写部分角色）；
    - ``versions`` 为该角色本次保存的绑定版本（新请求绑定；旧请求保持原版本）。
    """
    from utils.prompt_guard import sanitize_prompt, validate_prompt

    payload: dict[str, object] = {}
    for role_key, append in appends.items():
        if not isinstance(role_key, str) or not isinstance(append, str):
            return False
        if append:
            is_valid, _ = validate_prompt(append)
            if not is_valid:
                return False
            append = sanitize_prompt(append)
        payload[_role_prompt_append_key(role_key)] = append
        payload[_role_prompt_version_key(role_key)] = versions.get(role_key, "")
    if not payload:
        return True
    return cfg.ConfigHandler.save_config(payload)


def get_strategy_presets(strategy_key: str) -> dict[str, dict]:
    """获取策略的已保存参数预设 (Task 4.1).

    Returns:
        dict: {preset_name: params_dict}, 无预设时返回空 dict.

    返回深拷贝：``load_config`` 只做顶层浅拷贝，嵌套 dict 与配置缓存共享引用，
    直接返回会让调用方（或内部写路径）原地修改污染缓存。
    """
    config = cfg.ConfigHandler.load_config()
    key = f"strategy_presets_{strategy_key}"
    presets = config.get(key, {})
    return copy.deepcopy(presets) if isinstance(presets, dict) else {}


def save_strategy_preset(strategy_key: str, name: str, params: dict) -> bool:
    """保存命名参数预设 (Task 4.1). 重名覆盖（明确最后写者）。

    D02-F06：在「文件锁 → RWLock」内基于磁盘现值合并单个名字，不把锁外旧缓存
    构造的整份预设 dict 传给 ``save_config``（后者会整体替换该顶层键的嵌套内容，
    丢失他进程已提交的预设）。不同名合并、同名覆盖；失败时磁盘与缓存均保持
    已提交值。

    Returns:
        bool: 保存成功返回 True.
    """
    key = f"strategy_presets_{strategy_key}"

    def _merge(section: dict) -> bool:
        section[name] = copy.deepcopy(params)
        return True

    return storage.update_config_key(key, _merge)


def delete_strategy_preset(strategy_key: str, name: str) -> PresetDeleteOutcome:
    """删除命名参数预设 (Task 4.1).

    D02-F06：存在性在文件锁内基于磁盘判定，不依据锁外缓存——他进程已删除的
    条目不会因本进程旧快照而被复活。

    Returns:
        ``DELETED`` 磁盘上预设存在且落盘成功；``NOT_FOUND`` 锁内判定目标不存在；
        ``WRITE_FAILED`` 目标存在但存储未能提交（读盘失败 / 锁超时 / 原子写失败等）。
        后两者磁盘与缓存均保持已提交值，调用方可据此区分「不存在」与「需要重试」。
    """
    key = f"strategy_presets_{strategy_key}"

    def _remove(section: dict) -> bool:
        if name not in section:
            return False
        del section[name]
        return True

    outcome = storage.update_config_key_outcome(key, _remove)
    if outcome is storage.ConfigMutationOutcome.NOT_APPLIED:
        return PresetDeleteOutcome.NOT_FOUND
    if outcome is storage.ConfigMutationOutcome.COMMITTED:
        return PresetDeleteOutcome.DELETED
    return PresetDeleteOutcome.WRITE_FAILED


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


# ---------------------------------------------------------------------------
# 研究配置统一入口（W-18）：角色 / 预算 / 摘要策略的单一配置来源
#
# 约定：
# - 模型不在本域重复存储——研究模型与「模型连接 / 场景配置（agent_research）」同一来源；
#   本域只表达研究侧默认角色与摘要策略。
# - 预算不在本域重复存储——月度软预算由 ``usage_policy.monthly_cny_limit`` 单一来源承载。
# - 摘要 Prompt 的默认资产来自 ``core.prompt_base.RESEARCH_SUMMARY_PROMPT_DEFAULT``
#   （与 W-10 压缩模块对齐）；存储值为空即「未自定义」，读取时回落到默认资产。
# - 缺失/非法值一律显式回落并在读取件上可区分（R21：不把未配置伪装成具体业务值）。
# ---------------------------------------------------------------------------

#: 研究角色白名单（与设置页/研究侧角色 Prompt 的 role_key 同集合）。
RESEARCH_ROLE_KEYS: tuple[str, ...] = ("main", "sub")
#: 研究默认角色（仅当配置缺失/非法时使用；属文档化默认，不是缺失值伪装）。
DEFAULT_RESEARCH_ROLE = "main"
#: 摘要策略默认：启用 + 保留尾部 6 条（与 W-10 ``CompactionConfig`` 默认对齐，
#: 由测试守护两处不漂移）。
DEFAULT_RESEARCH_SUMMARY_ENABLED = True
DEFAULT_RESEARCH_SUMMARY_KEEP_MESSAGES = 6
RESEARCH_SUMMARY_KEEP_MESSAGES_MIN = 1
RESEARCH_SUMMARY_KEEP_MESSAGES_MAX = 50

_RESEARCH_ROLE_KEY = "agent_research_role"
_RESEARCH_SUMMARY_ENABLED_KEY = "agent_research_summary_enabled"
_RESEARCH_SUMMARY_KEEP_MESSAGES_KEY = "agent_research_summary_keep_messages"
_RESEARCH_SUMMARY_PROMPT_KEY = "agent_research_summary_prompt"


def get_agent_research_role() -> str:
    """研究默认角色；缺键/非法值回落文档化默认（并告警，不静默伪装）。"""
    value = cfg.ConfigHandler.get_typed(_RESEARCH_ROLE_KEY, str, DEFAULT_RESEARCH_ROLE)
    if value not in RESEARCH_ROLE_KEYS:
        cfg.logger.warning("[ConfigHandler] Unknown research role %r, falling back to default", value)
        return DEFAULT_RESEARCH_ROLE
    return value


def get_agent_research_summary_enabled() -> bool:
    """摘要压缩是否启用（缺键按文档化默认 True）。"""
    return cfg.ConfigHandler.get_typed(_RESEARCH_SUMMARY_ENABLED_KEY, bool, DEFAULT_RESEARCH_SUMMARY_ENABLED)


def get_agent_research_summary_keep_messages() -> int:
    """摘要后原样保留的尾部消息数；越界按合法区间收敛。"""
    value = cfg.ConfigHandler.get_typed(
        _RESEARCH_SUMMARY_KEEP_MESSAGES_KEY, int, DEFAULT_RESEARCH_SUMMARY_KEEP_MESSAGES
    )
    return max(RESEARCH_SUMMARY_KEEP_MESSAGES_MIN, min(value, RESEARCH_SUMMARY_KEEP_MESSAGES_MAX))


def get_agent_research_summary_prompt() -> str:
    """摘要 Prompt 生效值：未自定义（空/空白）时返回默认资产。"""
    value = cfg.ConfigHandler.get_typed(_RESEARCH_SUMMARY_PROMPT_KEY, str, "")
    return value if value.strip() else RESEARCH_SUMMARY_PROMPT_DEFAULT


def is_agent_research_summary_prompt_default() -> bool:
    """当前生效摘要 Prompt 是否仍为默认资产（空存储即默认）。"""
    value = cfg.ConfigHandler.get_typed(_RESEARCH_SUMMARY_PROMPT_KEY, str, "")
    return not value.strip()


def save_agent_research_config(
    role_key: str,
    summary_enabled: bool,
    summary_keep_messages: int,
    summary_prompt: str,
) -> bool:
    """原子保存研究配置（角色 + 摘要策略），任一校验失败整体未提交。

    - ``role_key`` 必须属于 :data:`RESEARCH_ROLE_KEYS`；
    - ``summary_keep_messages`` 必须为 1..50 的整数（bool 拒绝）；
    - ``summary_prompt`` 空串表示「恢复默认」；非空沿用 ``validate_prompt`` +
      ``sanitize_prompt`` 语义，校验失败不写部分字段（R21 邻域）。

    Returns:
        True 全部落盘；False 未提交（校验失败 / 磁盘不可读 / 原子写失败）。
    """
    if role_key not in RESEARCH_ROLE_KEYS:
        return False
    if not isinstance(summary_enabled, bool):
        return False
    if (
        isinstance(summary_keep_messages, bool)
        or not isinstance(summary_keep_messages, int)
        or not (RESEARCH_SUMMARY_KEEP_MESSAGES_MIN <= summary_keep_messages <= RESEARCH_SUMMARY_KEEP_MESSAGES_MAX)
    ):
        return False
    if not isinstance(summary_prompt, str):
        return False

    prompt = summary_prompt
    if prompt.strip():
        from utils.prompt_guard import sanitize_prompt, validate_prompt

        is_valid, _ = validate_prompt(prompt)
        if not is_valid:
            return False
        prompt = sanitize_prompt(prompt)
    else:
        prompt = ""

    return cfg.ConfigHandler.save_config(
        {
            _RESEARCH_ROLE_KEY: role_key,
            _RESEARCH_SUMMARY_ENABLED_KEY: summary_enabled,
            _RESEARCH_SUMMARY_KEEP_MESSAGES_KEY: summary_keep_messages,
            _RESEARCH_SUMMARY_PROMPT_KEY: prompt,
        }
    )


def reset_agent_research_summary_prompt() -> bool:
    """恢复默认摘要 Prompt（清除自定义值，读取回落到默认资产）。"""
    return cfg.ConfigHandler.save_config({_RESEARCH_SUMMARY_PROMPT_KEY: ""})
