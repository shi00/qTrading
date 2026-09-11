"""密钥/凭证域：token、db_password、provider credentials + keyring + AES 加密降级。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 secrets 相关方法，仅按域搬移、不改行为。本模块所有共享状态
（keyring/SecurityManager/ENV_FALLBACK_MAP 等）与跨方法访问一律经
``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

import copy
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from utils import config_handler as cfg
from utils.llm_providers import LLM_PROVIDERS


class SaveOutcome(StrEnum):
    """凭证保存结果语义（替代裸 bool，修复 D8-2 假报成功）。

    - ``SAVED``: 已成功持久化。
    - ``OVERRIDDEN_BY_ENV``: 对应环境变量已设置，优先级高于此处配置，本次输入不生效
      （get_* 优先读环境变量，写入的值永不生效）。返回成功会让 UI 假报"已保存"。
    - ``FAILED_NO_SECURE_STORE``: 本机无可用的 keyring / 加密环境（SecurityManager
      在无既有密钥文件时故意抛 ``SecurityError``）。应提示用户改用环境变量或修复密钥服务。
    - ``FAILED``: 其他持久化失败。
    """

    SAVED = "saved"
    OVERRIDDEN_BY_ENV = "overridden_by_env"
    FAILED_NO_SECURE_STORE = "failed_no_secure_store"
    FAILED = "failed"


@dataclass(frozen=True)
class CredentialSaveResult:
    """按字段报告的凭证保存结果（修复 D8-5 部分成功不可区分）。

    ``None`` 表示本次调用未修改该字段。
    """

    api_key: SaveOutcome | None = None
    base_url: SaveOutcome | None = None
    models: SaveOutcome | None = None

    @property
    def all_ok(self) -> bool:
        outcomes = [o for o in (self.api_key, self.base_url, self.models) if o is not None]
        return all(o is SaveOutcome.SAVED for o in outcomes) if outcomes else True


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


_KEYRING_PROBE_ITEM = "__keyring_available_probe__"
_keyring_available: bool | None = None


def is_keyring_available() -> bool:
    """F4（检视 06）：探测 keyring 后端是否可用（只读，结果缓存）。

    通过一次只读操作（``get_password``）检测 keyring 后端可用性：后端不可用
    （无 D-Bus / 未登录 / 权限拒绝）时该调用抛异常 → 返回 False；可正常返回
    （含未存储该项返回 None）则视为可用。结果缓存于模块级，避免重复探测
    带来的 OS / IPC 开销（应用启动期探测一次，后续 UI 状态直接读取缓存）。

    Returns:
        bool: True 表示 keyring 后端可用；否则 False。
    """
    global _keyring_available
    if _keyring_available is not None:
        return _keyring_available
    try:
        cfg.keyring.get_password(cfg.KEYRING_SERVICE_NAME, _KEYRING_PROBE_ITEM)
        _keyring_available = True
    except Exception as e:
        cfg.logger.debug("Keyring availability probe failed: %s", cfg.DataSanitizer.sanitize_error(e))
        _keyring_available = False
    return _keyring_available


def _reset_keyring_available_cache() -> None:
    """测试隔离：重置 keyring 可用性探测缓存，使下次调用重新探测。"""
    global _keyring_available
    _keyring_available = None


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


def _store_secret(
    item: str,
    value: str,
    env_var: str,
    *,
    clear_config: Callable[[], bool],
    encrypt_to_config: Callable[[str], bool],
) -> SaveOutcome:
    """统一的凭证存储降级链：环境变量检查 → keyring → AES → 明确失败。

    三个 save_* 函数原各自实现同一降级结构并已漂移
    （save_provider_credential 缺 SecurityError 分支，见 D8-3）。

    Args:
        item: keyring 中的存储项名（如 ``"ts_token"``）。
        value: 要保存的明文凭证。
        env_var: 对应环境变量名（如 ``"TS_TOKEN"``）。该环境变量存在时返回
            ``OVERRIDDEN_BY_ENV``，因为 get_* 会优先读环境变量，写入的值永不生效。
        clear_config: keyring 写入成功后清除配置中对应加密残留字段的可调用（返回 bool）。
        encrypt_to_config: AES 降级时把加密值写入配置的可调用（返回 bool）。

    Returns:
        保存结果（SaveOutcome），关键分支：
        - 环境变量已设置 → ``OVERRIDDEN_BY_ENV``
        - keyring + SecurityManager 均不可用 → ``FAILED_NO_SECURE_STORE``
    """
    if os.environ.get(env_var):
        return SaveOutcome.OVERRIDDEN_BY_ENV

    try:
        cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, item, value)
        return SaveOutcome.SAVED if clear_config() else SaveOutcome.FAILED
    # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
    except Exception as e:
        cfg.logger.error(
            "Failed to use keyring for %s: %s. Falling back to SecurityManager (lower security).",
            item,
            cfg.DataSanitizer.sanitize_error(e),
        )
        # H-3：keyring 降级到 AES 前清除陈旧的 keyring 条目，防止旧的明文/错误值在
        # keyring 恢复后"赢过"新写入的加密值。
        try:
            cfg.keyring.delete_password(cfg.KEYRING_SERVICE_NAME, item)
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as del_err:
            cfg.logger.debug(
                "Keyring %s deletion skipped: %s",
                item,
                cfg.DataSanitizer.sanitize_error(del_err),
                exc_info=True,
            )
        try:
            encrypted = cfg.SecurityManager.encrypt_data(value)
            return SaveOutcome.SAVED if encrypt_to_config(encrypted) else SaveOutcome.FAILED
        except cfg.SecurityError as se:
            cfg.logger.error(
                "Cannot securely store %s: %s. Please use environment variable %s instead.",
                item,
                cfg.DataSanitizer.sanitize_error(se),
                env_var,
            )
            return SaveOutcome.FAILED_NO_SECURE_STORE
        # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
        except Exception as enc_err:
            cfg.logger.error("Failed to encrypt %s: %s", item, cfg.DataSanitizer.sanitize_error(enc_err))
            return SaveOutcome.FAILED


def save_token(token: str) -> SaveOutcome:
    """保存 Tushare token。

    环境变量存在时返回 ``OVERRIDDEN_BY_ENV`` 而非成功 —— get_token 优先读
    环境变量，写入的值永远不会生效。返回成功会让 UI 显示"保存成功"而功能
    不工作，用户无从归因（D8-2）。
    """
    if not token:
        # 与 D8-2 语义一致：环境变量存在时不做任何 keyring 操作（环境变量优先，
        # 清除 keyring 无意义，并避免误删用户长期保存的 token / 临时设环境变量时被清空）。
        if os.environ.get(cfg.ENV_FALLBACK_MAP["ts_token"]):
            return SaveOutcome.OVERRIDDEN_BY_ENV
        try:
            cfg.keyring.delete_password(cfg.KEYRING_SERVICE_NAME, "ts_token")
        # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
        except Exception as e:
            cfg.logger.debug(
                "Keyring ts_token deletion skipped (not stored or keyring unavailable): %s",
                cfg.DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
        return SaveOutcome.SAVED if cfg.ConfigHandler.save_config({"ts_token": ""}) else SaveOutcome.FAILED

    return _store_secret(
        "ts_token",
        token,
        cfg.ENV_FALLBACK_MAP["ts_token"],
        clear_config=lambda: cfg.ConfigHandler.save_config({"ts_token": ""}),
        encrypt_to_config=lambda enc: cfg.ConfigHandler.save_config({"ts_token": enc}),
    )


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


def save_db_password(password: str) -> SaveOutcome:
    """保存数据库密码。

    环境变量存在时返回 ``OVERRIDDEN_BY_ENV``（D8-2）；其余走统一的
    keyring → AES → 明确失败降级链。
    """
    if not password:
        return SaveOutcome.FAILED
    return _store_secret(
        "db_password",
        password,
        cfg.ENV_FALLBACK_MAP["db_password"],
        clear_config=lambda: cfg.ConfigHandler.save_config({"db_password_encrypted": ""}),
        encrypt_to_config=lambda enc: cfg.ConfigHandler.save_config({"db_password_encrypted": enc}),
    )


def save_provider_credential(
    provider: str,
    api_key: str | None = None,
    base_url: str | None = None,
    models: list[str] | None = None,
) -> CredentialSaveResult:
    """保存指定 LLM 供应商的凭证（用于跨供应商 failover）。

    按字段（base_url / models / api_key）独立保存并各自报告结果，使 UI 能
    区分"全部失败 / 部分失败 / 未修改"，避免 api_key 无法安全存储时连带
    回滚 base_url / models 的修改且无任何可操作指引（D8-3 / D8-5）。

    Provider key 的环境变量语义与全局凭证不同：``get_provider_credential``
    仅在供应商专属 key 缺失时回退到全局，全局 ``AI_API_KEY`` 环境变量存在
    不应阻止供应商专属 key 的保存，故此处不复用 ``_store_secret`` 的环境
    变量覆盖检查，只对齐其 keyring → AES → 明确失败的降级结构。

    Args:
        provider: 供应商 ID (如 "qwen", "deepseek", "openai")
        api_key: API Key。None 表示不修改，空字符串表示清除，非空表示更新。
        base_url: API 基础 URL。None 表示不修改，空字符串表示清除，非空表示更新。
        models: 该供应商的自定义模型列表。None 表示不修改。

    Returns:
        CredentialSaveResult: 各字段独立结果（None 表示本次未修改该字段）。
    """
    config = cfg.ConfigHandler.load_config()

    provider_credentials = copy.deepcopy(config.get("llm_provider_credentials", {}))
    if not isinstance(provider_credentials, dict):
        provider_credentials = {}

    cred = provider_credentials.get(provider, {})

    result = CredentialSaveResult()

    if base_url is not None:
        if base_url:
            cred["base_url"] = base_url
        elif "base_url" in cred:
            del cred["base_url"]
        provider_credentials[provider] = cred
        ok = cfg.ConfigHandler.save_config({"llm_provider_credentials": provider_credentials})
        result = replace(result, base_url=SaveOutcome.SAVED if ok else SaveOutcome.FAILED)

    if models is not None:
        custom_models = copy.deepcopy(config.get("llm_custom_models", {}))
        updated_models = list(models)
        if len(updated_models) > 50:
            updated_models = updated_models[-50:]
        custom_models[provider] = updated_models
        ok = cfg.ConfigHandler.save_config({"llm_custom_models": custom_models})
        result = replace(result, models=SaveOutcome.SAVED if ok else SaveOutcome.FAILED)

    if api_key is not None:
        if api_key:
            try:
                cfg.keyring.set_password(cfg.KEYRING_SERVICE_NAME, f"ai_api_key_{provider}", api_key)
                result = replace(result, api_key=SaveOutcome.SAVED)
            # NOTE(lazy): keyring 操作失败降级到加密配置/忽略. ceiling: keyring 不可用(无 D-Bus/未登录/权限拒绝). upgrade: 引入 keyring 可用性预检或统一 fallback 包装.
            except Exception as e:
                cfg.logger.error(
                    "[ConfigHandler] Keyring save failed for %s: %s. Falling back to SecurityManager (lower security).",
                    provider,
                    cfg.DataSanitizer.sanitize_error(e),
                )
                try:
                    encrypted_key = cfg.SecurityManager.encrypt_data(api_key)
                    cred["api_key_encrypted"] = encrypted_key
                    provider_credentials[provider] = cred
                    ok = cfg.ConfigHandler.save_config({"llm_provider_credentials": provider_credentials})
                    result = replace(result, api_key=SaveOutcome.SAVED if ok else SaveOutcome.FAILED)
                except cfg.SecurityError as se:
                    # 与 save_token/save_db_password 保持一致：SecurityError 携带完整
                    # 解决指引（keyring 安装 / 环境变量），必须传递到 UI 而非降级为通用错误。
                    cfg.logger.error(
                        "[ConfigHandler] Cannot securely store api_key for %s: %s. Use environment variable AI_API_KEY instead.",
                        provider,
                        cfg.DataSanitizer.sanitize_error(se),
                    )
                    result = replace(result, api_key=SaveOutcome.FAILED_NO_SECURE_STORE)
                # NOTE(lazy): 加密/解密失败兜底(密钥变化/数据损坏). ceiling: SecurityManager 密钥未初始化或数据损坏. upgrade: 引入密钥迁移机制或显式提示用户重置.
                except Exception as enc_err:
                    cfg.logger.error(
                        "[ConfigHandler] Failed to encrypt api_key for %s: %s",
                        provider,
                        cfg.DataSanitizer.sanitize_error(enc_err),
                    )
                    result = replace(result, api_key=SaveOutcome.FAILED)
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
                ok = cfg.ConfigHandler.save_config({"llm_provider_credentials": provider_credentials})
                result = replace(result, api_key=SaveOutcome.SAVED if ok else SaveOutcome.FAILED)
            else:
                result = replace(result, api_key=SaveOutcome.SAVED)

    return result


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
