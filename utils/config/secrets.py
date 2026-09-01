"""密钥/凭证域：token、db_password、provider credentials + keyring + AES 加密降级。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 secrets 相关方法，仅按域搬移、不改行为。本模块所有共享状态
（keyring/SecurityManager/ENV_FALLBACK_MAP 等）与跨方法访问一律经
``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

import copy
import os

from utils import config_handler as cfg
from utils.llm_providers import LLM_PROVIDERS


def _try_decrypt(value):
    """Helper: Try to decrypt value. Returns empty string if failed."""
    if not value:
        return ""
    try:
        return cfg.SecurityManager.decrypt_data(value)
    except cfg.DecryptionError:
        cfg.logger.warning(
            "Failed to decrypt config value. It might be invalid or legacy plaintext.",
        )
        return ""
    # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
    except Exception as e:
        cfg.logger.error("Decryption error: %s", cfg.DataSanitizer.sanitize_error(e))
        return ""


def get_token():
    # 1. 环境变量优先（最高优先级）
    env_token = os.environ.get(cfg.ENV_FALLBACK_MAP["ts_token"])
    if env_token:
        cfg.DataSanitizer.register_secret(env_token)
        return env_token

    # 2. keyring
    kr_token = None
    try:
        kr_token = cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, "ts_token")
    # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
    except Exception as e:
        cfg.logger.debug("Keyring get_password for ts_token failed: %s", cfg.DataSanitizer.sanitize_error(e))
    if kr_token:
        cfg.DataSanitizer.register_secret(kr_token)
        return kr_token

    # 3. 加密配置文件（如果 SecurityManager 可用）
    config = cfg.ConfigHandler.load_config()
    token = config.get("ts_token", "")
    decrypted = cfg.ConfigHandler._try_decrypt(token)
    if decrypted:
        try:
            cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "ts_token", decrypted)
            cfg.ConfigHandler._persist_migration({"ts_token": ""}, "clear ts_token after keyring migration")
            cfg.logger.info("Migrated ts_token from config to keyring and cleared legacy value")
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as e:
            cfg.logger.debug("Keyring migration failed: %s", cfg.DataSanitizer.sanitize_error(e))
    cfg.DataSanitizer.register_secret(decrypted)
    return decrypted


def save_token(token):
    # 环境变量优先：若 TS_TOKEN 已存在，跳过 keyring 读写（get_token 会优先读环境变量）
    if os.environ.get(cfg.ENV_FALLBACK_MAP["ts_token"]):
        return True

    if not token:
        try:
            cfg.keyring.delete_password(cfg.KEYRING_SERVICE_NAME, "ts_token")
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as e:
            cfg.logger.debug(
                "Keyring ts_token deletion skipped (not stored or keyring unavailable): %s",
                cfg.DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
        return cfg.ConfigHandler.save_config({"ts_token": ""})

    try:
        cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "ts_token", token)
        return cfg.ConfigHandler.save_config({"ts_token": ""})
    # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
    except Exception as e:
        cfg.logger.warning(
            "Failed to use keyring for ts_token: %s. Falling back to SecurityManager.",
            cfg.DataSanitizer.sanitize_error(e),
        )
        try:
            encrypted = cfg.SecurityManager.encrypt_data(token)
            return cfg.ConfigHandler.save_config({"ts_token": encrypted})
        except cfg.SecurityError as se:
            cfg.logger.error(
                "Cannot securely store ts_token: %s. Please use environment variable TS_TOKEN instead.",
                cfg.DataSanitizer.sanitize_error(se),
            )
            return False
        # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
        except Exception as enc_err:
            cfg.logger.error("Failed to encrypt ts_token: %s", cfg.DataSanitizer.sanitize_error(enc_err))
            return False


def get_db_password():
    """Get database password from keyring or encrypted config."""
    # 1. 环境变量优先（最高优先级）
    env_password = os.environ.get(cfg.ENV_FALLBACK_MAP["db_password"])
    if env_password:
        cfg.DataSanitizer.register_secret(env_password)
        return env_password

    # 2. keyring
    try:
        password = cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, "db_password")
        if password:
            cfg.DataSanitizer.register_secret(password)
            return password
    # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
    except Exception as e:
        cfg.logger.debug(
            "Failed to get db_password from keyring: %s", cfg.DataSanitizer.sanitize_error(e), exc_info=True
        )

    # 3. 加密配置文件
    user_config = cfg.ConfigHandler.load_config()
    encrypted = user_config.get("db_password_encrypted", "")
    if encrypted:
        decrypted = cfg.ConfigHandler._try_decrypt(encrypted)
        cfg.DataSanitizer.register_secret(decrypted)
        return decrypted
    return ""


def save_db_password(password: str) -> bool:
    """Save database password to keyring."""
    if not password:
        return False
    # 环境变量优先：若 DB_PASSWORD 已存在，跳过 keyring 写入（get_db_password 会优先读环境变量）
    if os.environ.get(cfg.ENV_FALLBACK_MAP["db_password"]):
        return True
    try:
        cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, "db_password", password)
        return cfg.ConfigHandler.save_config({"db_password_encrypted": ""})
    # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
    except Exception as e:
        cfg.logger.warning(
            "Failed to save db_password to keyring: %s", cfg.DataSanitizer.sanitize_error(e), exc_info=True
        )
        try:
            cfg.keyring.delete_password(cfg.KEYRING_SERVICE_NAME, "db_password")
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as e:
            cfg.logger.debug(
                "Keyring db_password deletion skipped: %s",
                cfg.DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
        try:
            encrypted = cfg.SecurityManager.encrypt_data(password)
            return cfg.ConfigHandler.save_config({"db_password_encrypted": encrypted})
        except cfg.SecurityError as se:
            cfg.logger.error(
                "Cannot securely store db_password: %s. Please use environment variable DB_PASSWORD instead.",
                cfg.DataSanitizer.sanitize_error(se),
            )
            return False
        # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
        except Exception as e2:
            cfg.logger.error("Failed to encrypt db_password: %s", cfg.DataSanitizer.sanitize_error(e2))
            return False


def save_provider_credential(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    models: list[str] | None = None,
) -> bool:
    """保存指定 LLM 供应商的凭证（用于跨供应商 failover）。

    Args:
        provider: 供应商 ID (如 "qwen", "deepseek", "openai")
        api_key: API Key。None 表示不修改，空字符串表示清除，非空表示更新。
        base_url: API 基础 URL。None 表示不修改，空字符串表示清除，非空表示更新。
        models: 该供应商的自定义模型列表。None 表示不修改。

    Returns:
        bool: 保存是否成功。
    """
    config = cfg.ConfigHandler.load_config()

    provider_credentials = copy.deepcopy(config.get("llm_provider_credentials", {}))
    if not isinstance(provider_credentials, dict):
        provider_credentials = {}

    cred = provider_credentials.get(provider, {})

    config_update = {}

    if base_url is not None:
        if base_url:
            cred["base_url"] = base_url
        elif "base_url" in cred:
            del cred["base_url"]

    provider_credentials[provider] = cred
    config_update["llm_provider_credentials"] = provider_credentials

    if models is not None:
        custom_models = copy.deepcopy(config.get("llm_custom_models", {}))
        updated_models = list(models)
        if len(updated_models) > 50:
            updated_models = updated_models[-50:]
        custom_models[provider] = updated_models
        config_update["llm_custom_models"] = custom_models

    if api_key is not None:
        if api_key:
            try:
                cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, f"ai_api_key_{provider}", api_key)
            # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
            except Exception as e:
                cfg.logger.warning(
                    "[ConfigHandler] Keyring save failed for %s: %s. Falling back to encrypted storage.",
                    provider,
                    cfg.DataSanitizer.sanitize_error(e),
                )
                try:
                    encrypted_key = cfg.SecurityManager.encrypt_data(api_key)
                    cred["api_key_encrypted"] = encrypted_key
                    provider_credentials[provider] = cred
                    config_update["llm_provider_credentials"] = provider_credentials
                # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
                except Exception as enc_err:
                    cfg.logger.error(
                        "[ConfigHandler] Failed to encrypt api_key for %s: %s",
                        provider,
                        cfg.DataSanitizer.sanitize_error(enc_err),
                    )
                    return False
        else:
            try:
                cfg.keyring.delete_password(cfg.KEYRING_SERVICE_NAME, f"ai_api_key_{provider}")
            except cfg.keyring.errors.PasswordDeleteError:  # type: ignore[reportAttributeAccessIssue]  # keyring.errors is available at runtime
                pass
            # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
            except Exception as e:
                cfg.logger.debug(
                    "keyring operation failed: %s",
                    cfg.DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
            if "api_key_encrypted" in cred:
                del cred["api_key_encrypted"]
                provider_credentials[provider] = cred
                config_update["llm_provider_credentials"] = provider_credentials

    return cfg.ConfigHandler.save_config(config_update)


def get_provider_credential(provider: str, fallback_to_global: bool = True) -> dict:
    """获取指定 LLM 供应商的完整凭证。

    Returns:
        {"api_key": str | None, "base_url": str, "models": list[str]}
    """
    config = cfg.ConfigHandler.load_config()

    provider_credentials = config.get("llm_provider_credentials", {})
    cred = provider_credentials.get(provider, {})

    api_key = None

    try:
        api_key = cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, f"ai_api_key_{provider}")
        if api_key:
            cfg.DataSanitizer.register_secret(api_key)
    # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
    except Exception as e:
        cfg.logger.debug(
            "keyring operation failed: %s",
            cfg.DataSanitizer.sanitize_error(e),
            exc_info=True,
        )

    if not api_key and cred.get("api_key_encrypted"):
        try:
            api_key = cfg.SecurityManager.decrypt_data(cred["api_key_encrypted"])
            if api_key:
                cfg.DataSanitizer.register_secret(api_key)
        # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
        except Exception as e:
            cfg.logger.debug(
                "keyring operation failed: %s",
                cfg.DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    # Fallback to global api_key if provider-specific key not found
    if fallback_to_global and not api_key:
        try:
            api_key = cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, "ai_api_key")
            if api_key:
                cfg.DataSanitizer.register_secret(api_key)
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as e:
            cfg.logger.debug(
                "keyring operation failed: %s",
                cfg.DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

        if not api_key:
            global_encrypted = config.get("ai_api_key")
            if global_encrypted:
                try:
                    api_key = cfg.SecurityManager.decrypt_data(global_encrypted)
                    if api_key:
                        cfg.DataSanitizer.register_secret(api_key)
                # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
                except Exception as e:
                    cfg.logger.debug(
                        "keyring operation failed: %s",
                        cfg.DataSanitizer.sanitize_error(e),
                        exc_info=True,
                    )

    base_url = cred.get("base_url", "")
    if not base_url:
        base_url = LLM_PROVIDERS.get(provider, {}).get("base_url", "")

    custom_models = config.get("llm_custom_models", {})
    provider_models = custom_models.get(provider, cred.get("models", []))

    return {
        "api_key": api_key,
        "base_url": base_url,
        "models": provider_models,
    }


def purge_legacy_key_if_safe() -> bool:
    """F3（检视 06）：安全清理 legacy 明文密钥文件（``.secret.key`` 家族）。

    前置判定：仅当配置中已无任何仍需该密钥解密的 AES 加密字段时，才调用
    ``SecurityManager.purge_legacy_key_files()`` 删除文件；否则保留文件并返回
    ``False``，避免导致既有加密值不可解密（数据丢失）。

    "仍需该密钥"判定：配置文件（经 load_config）中下列任一敏感字段非空，
    即视为仍需密钥解密，不删除——
    - ``ts_token`` / ``ai_api_key`` / ``db_password_encrypted``（顶层加密字段）
    - ``llm_provider_credentials.*.api_key_encrypted``（各供应商加密字段）

    Returns:
        True 若已删除 legacy 密钥文件；False 若无需删除或存在仍需密钥的加密字段。
    """
    if not cfg.SecurityManager.has_legacy_key_files():
        return False

    config = cfg.ConfigHandler.load_config()
    if config.get("ts_token") or config.get("db_password_encrypted") or config.get("ai_api_key"):
        cfg.logger.info("[Secrets] Legacy key files kept: config still references AES-encrypted fields")
        return False

    provider_credentials = config.get("llm_provider_credentials", {}) or {}
    for cred in provider_credentials.values():
        if isinstance(cred, dict) and cred.get("api_key_encrypted"):
            cfg.logger.info("[Secrets] Legacy key files kept: provider credential still AES-encrypted")
            return False

    removed = cfg.SecurityManager.purge_legacy_key_files()
    if removed:
        cfg.logger.info("[Secrets] Removed legacy plaintext key files (.secret.key family) after credentials migrated")
    return removed


def validate_failover_credentials() -> list[str]:
    """校验 failover 配置的凭证完整性。

    Returns:
        list[str]: 缺少凭证的供应商列表
    """
    config = cfg.ConfigHandler.load_config()
    failover_models = config.get("llm_failover_models", [])
    missing = []
    seen = set()

    for model in failover_models:
        if "/" in model:
            provider = model.split("/")[0]
            if provider in seen:
                continue
            model_id = model.split("/", 1)[1]
            cred = cfg.ConfigHandler.get_provider_credential(provider)
            if not cred.get("api_key"):  # noqa: SIM114
                missing.append(provider)
                seen.add(provider)
            elif model_id and (not cred.get("models") or model_id not in cred["models"]):
                missing.append(provider)
                seen.add(provider)

    return missing
