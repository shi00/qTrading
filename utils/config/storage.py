"""配置持久化基础设施：加载/保存/原子写/RWLock/缓存/深合并/迁移。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler``，仅按域搬移、不改行为。为保持现有 2396 行单测对
``utils.config_handler` 命名空间的 mock 有效，本模块所有共享状态与跨方法访问
一律经 ``cfg = utils.config_handler`` 间接引用。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from pydantic import ValidationError

from utils import config_handler as cfg
from utils.config_models import AppConfig, ConfigValidationResult, get_default_config
from utils.sanitizers import DataSanitizer


def _open_builtin():
    """返回内置 ``open`` 的可调用对象。

    review05-E11 迁移兼容：原实现在 ``utils/config_handler.py`` 函数体内直接调用未限定
    ``open()``，单测以 ``patch.object(cfg_mod, "open", create=True)`` 在 config_handler
    命名空间注入 mock。move to storage.py 后需经 ``cfg`` 命名空间回退读取该 mock；
    生产路径 ``cfg.open`` 不存在，回退内置 ``open``，行为不变。upgrade: 调用点归零后可删除。
    """
    return getattr(cfg, "open", open)


def _clear_cache() -> None:
    """Clear the in-memory config cache.

    Intended for test isolation only — prevents cross-test state leakage
    when tests modify config on disk.  Production code should never need
    this because ``save_config`` / ``ensure_defaults`` already keep the
    cache in sync.
    """
    with cfg.ConfigHandler._lock.gen_wlock():
        cfg.ConfigHandler._config_cache = None


def get_typed[T](key: str, expected_type: type[T], default: T) -> T:
    """类型安全的通用 getter。

    E12: 当配置值存在但类型转换失败时，不再静默回落默认值，而记 WARNING
    （含配置项名、脱敏后的非法值、实际使用的默认值）。
    """
    config = cfg.ConfigHandler.load_config()
    val = config.get(key, default)
    try:
        if expected_type is bool and isinstance(val, str):
            return expected_type(val.lower() == "true")  # type: ignore[return-value]
        return expected_type(val)  # type: ignore[call-arg]
    except (ValueError, TypeError):
        cfg.logger.warning(
            "[ConfigHandler] Invalid value for %s: %s. Falling back to default: %s",
            key,
            DataSanitizer.sanitize_token(str(val)),
            DataSanitizer.sanitize_token(str(default)),
        )
        return default


def set_typed(key: str, value: object, validator: Callable[..., bool] | None = None) -> bool:
    if validator and not validator(value):
        display_value = DataSanitizer.sanitize_token(str(value)) if key in cfg.SENSITIVE_KEYS else value
        cfg.logger.warning("[ConfigHandler] Validation failed for %s: %s", key, display_value)
        return False
    return cfg.ConfigHandler.save_config({key: value})


def _deep_merge_defaults(current: dict, defaults: dict) -> tuple[dict, bool]:
    """Recursively merge default values into current config.

    Returns:
        (merged_config, dirty) - merged config and whether any changes were made
    """
    result = current.copy()
    dirty = False

    for key, default_val in defaults.items():
        if key not in result:
            result[key] = default_val
            dirty = True
        elif isinstance(default_val, dict) and isinstance(result.get(key), dict):
            nested_result, nested_dirty = cfg.ConfigHandler._deep_merge_defaults(result[key], default_val)
            if nested_dirty:
                result[key] = nested_result
                dirty = True

    return result, dirty


def _migrate_custom_models_credentials(current_config: dict) -> bool:
    """迁移 custom_models 中的凭证到 llm_provider_credentials。

    旧格式: llm_custom_models: {provider: {api_key..., base_url..., models...}}
    新格式: llm_custom_models: {provider: [model_id...,]} + llm_provider_credentials。

    Returns:
        bool: 是否进行了迁移。
    """
    custom_models = current_config.get("llm_custom_models", {})
    if not custom_models:
        return False

    needs_migration = False
    provider_credentials = current_config.get("llm_provider_credentials", {})
    cleaned_custom_models: dict[str, list[str]] = {}

    for provider, value in custom_models.items():
        if isinstance(value, list):
            cleaned_custom_models[provider] = [str(m) for m in value]
        elif isinstance(value, dict):
            needs_migration = True

            if value.get("api_key"):
                try:
                    cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, f"ai_api_key_{provider}", str(value["api_key"]))
                # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
                except Exception as e:
                    cfg.logger.debug(
                        "[ConfigHandler] Config encrypt fallback triggered: %s",
                        DataSanitizer.sanitize_error(e),
                        exc_info=True,
                    )
                    encrypted = cfg.SecurityManager.encrypt_data(str(value["api_key"]))
                    cred = provider_credentials.get(provider, {})
                    cred["api_key_encrypted"] = encrypted
                    provider_credentials[provider] = cred

            if value.get("base_url"):
                cred = provider_credentials.get(provider, {})
                cred["base_url"] = str(value["base_url"])
                provider_credentials[provider] = cred

            if isinstance(value.get("models"), list):
                cleaned_custom_models[provider] = [str(m) for m in value["models"]]

            cfg.logger.info("[ConfigHandler] Migrated credentials from custom_models for provider: %s", provider)

    if needs_migration:
        current_config["llm_custom_models"] = cleaned_custom_models
        current_config["llm_provider_credentials"] = provider_credentials
        cfg.logger.info("[ConfigHandler] Credential migration from custom_models completed")

    for provider, cred in provider_credentials.items():
        if "models" in cred:
            if provider not in cleaned_custom_models and isinstance(cred["models"], list):
                cleaned_custom_models[provider] = [str(m) for m in cred["models"]]
                cfg.logger.info("[ConfigHandler] Migrated 'models' from credentials to custom_models for: %s", provider)

            del cred["models"]
            needs_migration = True
            cfg.logger.info("[ConfigHandler] Removed legacy 'models' from credentials for: %s", provider)

    if needs_migration and cleaned_custom_models:
        current_config["llm_custom_models"] = cleaned_custom_models

    return needs_migration


def _save_json_atomically(data, path):
    """Helper: Atomic write for JSON config."""
    tmp_file = path + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_file, path)
        return True
    # NOTE(lazy): 配置文件 IO 失败兜底. ceiling: 系统级磁盘故障/权限拒绝. upgrade: 引入文件可读性预检或重试.
    except Exception as e:
        cfg.logger.error("Atomic save failed for %s: %s", path, DataSanitizer.sanitize_error(e))
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        return False


def ensure_defaults():
    """Ensure default settings exist AND remove unused keys from user_settings.json.

    Uses write lock for the entire read-modify-write cycle to prevent TOCTOU races.
    Reads config directly inside the lock (not via load_config) to avoid
    wlock->rlock deadlock with RWLockFair.
    """
    try:
        with cfg.ConfigHandler._lock.gen_wlock():
            if cfg.ConfigHandler._config_cache is not None:
                current_config = cfg.ConfigHandler._config_cache.copy()
            elif os.path.exists(cfg.CONFIG_FILE):
                try:
                    with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
                        current_config = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    cfg.logger.warning(
                        "[ConfigHandler] Config file unreadable, rebuilding from defaults: %s",
                        DataSanitizer.sanitize_error(e) if hasattr(DataSanitizer, "sanitize_error") else repr(e),
                    )
                    current_config = {}
            else:
                current_config = {}

            dirty = False

            for key, default_val in cfg.ConfigHandler.DEFAULT_CONFIG.items():
                if key not in current_config:
                    current_config[key] = default_val
                    dirty = True
                    cfg.logger.info("Initialized default config: %s", key)
                elif isinstance(default_val, dict) and isinstance(current_config.get(key), dict):
                    nested_result, nested_dirty = cfg.ConfigHandler._deep_merge_defaults(
                        current_config[key], default_val
                    )
                    if nested_dirty:
                        current_config[key] = nested_result
                        dirty = True
                        cfg.logger.info("Updated nested config: %s", key)

            valid_keys = set(cfg.ConfigHandler.DEFAULT_CONFIG.keys())
            existing_keys = list(current_config.keys())

            for key in existing_keys:
                if key.startswith("ai_strategy_prompt_"):
                    continue
                if key not in valid_keys:
                    cfg.logger.info("Removing deprecated/unused config: %s", key)
                    current_config.pop(key)
                    dirty = True

            if cfg.ConfigHandler._migrate_custom_models_credentials(current_config):
                dirty = True

            if dirty:
                success = cfg.ConfigHandler._save_json_atomically(current_config, cfg.CONFIG_FILE)
                if success:
                    cfg.ConfigHandler._config_cache = current_config
                cfg.logger.info(
                    "Configuration (defaults & cleanup) synchronized. Cleared deprecated keys: %s",
                    set(existing_keys) - valid_keys,
                )

    # NOTE(lazy): 配置管理整体兜底避免单点失败阻断流程. ceiling: 配置管理内部逻辑不应抛异常. upgrade: 配置管理内部统一走 classify_error.
    except Exception as e:
        cfg.logger.error("Failed to ensure default config: %s", DataSanitizer.sanitize_error(e))


def load_config():
    """Load config with Read Lock and Validation."""
    with cfg.ConfigHandler._lock.gen_rlock():
        if cfg.ConfigHandler._config_cache is not None:
            return cfg.ConfigHandler._config_cache.copy()

        if os.path.exists(cfg.CONFIG_FILE):
            try:
                with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
                    raw_data = json.load(f)
                    validated = AppConfig.model_validate(raw_data)
                    cfg.ConfigHandler._config_cache = validated.model_dump()
                    return cfg.ConfigHandler._config_cache.copy()
            except ValidationError as e:
                cfg.logger.warning("[ConfigHandler] Config validation failed: %s", DataSanitizer.sanitize_error(e))
                cfg.ConfigHandler._config_cache = get_default_config()
                return cfg.ConfigHandler._config_cache.copy()
            # NOTE(lazy): 配置文件 IO 失败兜底. ceiling: 系统级磁盘故障/权限拒绝. upgrade: 引入文件可读性预检或重试.
            except Exception as e:
                cfg.logger.warning(
                    "[ConfigHandler] Failed to load config file, using defaults: %s",
                    DataSanitizer.sanitize_error(e),
                )
                cfg.ConfigHandler._config_cache = get_default_config()
                return cfg.ConfigHandler._config_cache.copy()
        return {}


def load_config_with_validation() -> ConfigValidationResult:
    """加载配置并返回验证详情 (供 UI 层使用)."""
    with cfg.ConfigHandler._lock.gen_rlock():
        if os.path.exists(cfg.CONFIG_FILE):
            try:
                with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
                    raw_data = json.load(f)
                    validated = AppConfig.model_validate(raw_data)
                    return ConfigValidationResult(
                        is_valid=True,
                        config=validated.model_dump(),
                        errors=[],
                        used_defaults=False,
                    )
            except ValidationError as e:
                errors = []
                for err in e.errors():
                    err_str = str(err)
                    if err.get("input") is not None and err.get("loc") and err["loc"][-1] in cfg.SENSITIVE_KEYS:
                        err_str = err_str.replace(str(err["input"]), "***")
                    errors.append(err_str)
                return ConfigValidationResult(
                    is_valid=False,
                    config=get_default_config(),
                    errors=errors,
                    used_defaults=True,
                )
            # NOTE(lazy): 配置验证失败降级返回错误详情不阻断 UI. ceiling: 配置验证逻辑异常. upgrade: 配置验证统一走 classify_error.
            except Exception as e:
                return ConfigValidationResult(
                    is_valid=False,
                    config={},
                    errors=[DataSanitizer.sanitize_error(e)],
                    used_defaults=False,
                )
        return ConfigValidationResult(
            is_valid=True,
            config=get_default_config(),
            errors=[],
            used_defaults=True,
        )


def save_config(config_data, replace=False):
    """Save config with Write Lock, Validation and Atomic Write.

    :param config_data: Dict to save
    :param replace: If True, replaces entire config with config_data. If False, merges.
    """
    try:
        with cfg.ConfigHandler._lock.gen_wlock():
            if replace:
                current_config = config_data.copy()
            else:
                current_config = {}
                if cfg.ConfigHandler._config_cache is not None:
                    current_config = cfg.ConfigHandler._config_cache.copy()
                elif os.path.exists(cfg.CONFIG_FILE):
                    try:
                        with _open_builtin()(cfg.CONFIG_FILE, encoding="utf-8") as f:
                            current_config = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        pass
                current_config.update(config_data)

            try:
                validated = AppConfig.model_validate(current_config)
                current_config = validated.model_dump()
            except ValidationError as e:
                cfg.logger.error("[ConfigHandler] Invalid config data: %s", DataSanitizer.sanitize_error(e))
                return False

            success = cfg.ConfigHandler._save_json_atomically(current_config, cfg.CONFIG_FILE)

            if success:
                cfg.ConfigHandler._config_cache = current_config
                return True
            return False
    # NOTE(lazy): 配置管理整体兜底避免单点失败阻断流程. ceiling: 配置管理内部逻辑不应抛异常. upgrade: 配置管理内部统一走 classify_error.
    except Exception as e:
        cfg.logger.error("Error saving config: %s", DataSanitizer.sanitize_error(e))
        return False


def _persist_migration(update: dict, migration_name: str) -> bool:
    """P3-Config-Return-Propagation-Gaps: 读时迁移路径持久化助手。

    Returns:
        True 表示 save_config 成功；False 表示失败（已记 warning，调用方可忽略）。
    """
    success = cfg.ConfigHandler.save_config(update)
    if not success:
        cfg.logger.warning(
            "[ConfigHandler] config migration failed: %s (will retry on next startup)",
            migration_name,
        )
    return success


def get_config(key, default=None):
    config = cfg.ConfigHandler.load_config()
    return config.get(key, default)


def get_setting(key, default=None):
    config = cfg.ConfigHandler.load_config()
    return config.get(key, default)
