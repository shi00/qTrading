import contextlib
import copy
import json
import logging
import os
import re
from typing import TypeVar
from collections.abc import Callable

import keyring
from pydantic import ValidationError
from readerwriterlock import rwlock

import config
from utils.config_models import (
    DEFAULT_AI_PROMPT,
    DEFAULT_NEWS_PROMPT,
    AppConfig,
    ConfigValidationResult,
    get_default_config,
)
from utils.llm_providers import AZURE_DEFAULT_API_VERSION
from utils.sanitizers import DataSanitizer
from utils.security_utils import DecryptionError, SecurityError, SecurityManager

logger = logging.getLogger(__name__)

CONFIG_FILE = os.environ.get("ASTOCK_CONFIG_FILE") or os.path.join(config.APP_ROOT, "user_settings.json")
KEYRING_SERVICE_NAME = "AStockScreener"

ENV_FALLBACK_MAP = {
    "ts_token": "TS_TOKEN",
    "db_password": "DB_PASSWORD",
    "ai_api_key": "AI_API_KEY",
}

SENSITIVE_KEYS = frozenset({"ts_token", "db_password", "db_password_encrypted", "ai_api_key"})


class ConfigHandler:
    _config_cache = None
    _last_load_time = 0
    _lock = rwlock.RWLockFair()

    DEFAULT_CONFIG = get_default_config()

    T = TypeVar("T")

    @staticmethod
    def get_typed(key: str, expected_type: type[T], default: T) -> T:
        """类型安全的通用 getter"""
        cfg = ConfigHandler.load_config()
        val = cfg.get(key, default)
        try:
            if expected_type is bool and isinstance(val, str):
                return expected_type(val.lower() == "true")  # type: ignore[return-value]
            return expected_type(val)  # type: ignore[call-arg]
        except (ValueError, TypeError):
            return default

    @staticmethod
    def set_typed(key: str, value: object, validator: Callable[..., bool] | None = None) -> bool:
        if validator and not validator(value):
            display_value = DataSanitizer.sanitize_token(str(value)) if key in SENSITIVE_KEYS else value
            logger.warning("[ConfigHandler] Validation failed for %s: %s", key, display_value)
            return False
        return ConfigHandler.save_config({key: value})

    @staticmethod
    def _deep_merge_defaults(current: dict, defaults: dict) -> tuple[dict, bool]:
        """
        Recursively merge default values into current config.

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
                nested_result, nested_dirty = ConfigHandler._deep_merge_defaults(result[key], default_val)
                if nested_dirty:
                    result[key] = nested_result
                    dirty = True

        return result, dirty

    @staticmethod
    def _migrate_custom_models_credentials(current_config: dict) -> bool:
        """
        迁移 custom_models 中的凭证到 llm_provider_credentials

        旧格式: llm_custom_models: {provider: {api_key: ..., base_url: ..., models: [...]}}
        新格式: llm_custom_models: {provider: [model_id1, model_id2]}
                llm_provider_credentials: {provider: {api_key_encrypted: ..., base_url: ...}}

        Returns:
            bool: 是否进行了迁移
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

                if "api_key" in value and value["api_key"]:
                    try:
                        keyring.set_password(KEYRING_SERVICE_NAME, f"ai_api_key_{provider}", str(value["api_key"]))
                    except Exception:
                        encrypted = SecurityManager.encrypt_data(str(value["api_key"]))
                        cred = provider_credentials.get(provider, {})
                        cred["api_key_encrypted"] = encrypted
                        provider_credentials[provider] = cred

                if "base_url" in value and value["base_url"]:
                    cred = provider_credentials.get(provider, {})
                    cred["base_url"] = str(value["base_url"])
                    provider_credentials[provider] = cred

                if "models" in value and isinstance(value["models"], list):
                    cleaned_custom_models[provider] = [str(m) for m in value["models"]]

                logger.info(f"[ConfigHandler] Migrated credentials from custom_models for provider: {provider}")

        if needs_migration:
            current_config["llm_custom_models"] = cleaned_custom_models
            current_config["llm_provider_credentials"] = provider_credentials
            logger.info("[ConfigHandler] Credential migration from custom_models completed")

        for provider, cred in provider_credentials.items():
            if "models" in cred:
                if provider not in cleaned_custom_models and isinstance(cred["models"], list):
                    cleaned_custom_models[provider] = [str(m) for m in cred["models"]]
                    logger.info(f"[ConfigHandler] Migrated 'models' from credentials to custom_models for: {provider}")

                del cred["models"]
                needs_migration = True
                logger.info(f"[ConfigHandler] Removed legacy 'models' from credentials for: {provider}")

        if needs_migration and cleaned_custom_models:
            current_config["llm_custom_models"] = cleaned_custom_models

        return needs_migration

    @staticmethod
    def ensure_defaults():
        """Ensure default settings exist AND remove unused keys from user_settings.json

        Uses write lock for the entire read-modify-write cycle to prevent TOCTOU races.
        Reads config directly inside the lock (not via load_config) to avoid
        wlock->rlock deadlock with RWLockFair.
        """
        try:
            with ConfigHandler._lock.gen_wlock():
                if ConfigHandler._config_cache is not None:
                    current_config = ConfigHandler._config_cache.copy()
                elif os.path.exists(CONFIG_FILE):
                    try:
                        with open(CONFIG_FILE, encoding="utf-8") as f:
                            current_config = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        current_config = {}
                else:
                    current_config = {}

                dirty = False

                for key, default_val in ConfigHandler.DEFAULT_CONFIG.items():
                    if key not in current_config:
                        current_config[key] = default_val
                        dirty = True
                        logger.info(f"Initialized default config: {key}")
                    elif isinstance(default_val, dict) and isinstance(current_config.get(key), dict):
                        nested_result, nested_dirty = ConfigHandler._deep_merge_defaults(
                            current_config[key], default_val
                        )
                        if nested_dirty:
                            current_config[key] = nested_result
                            dirty = True
                            logger.info(f"Updated nested config: {key}")

                valid_keys = set(ConfigHandler.DEFAULT_CONFIG.keys())
                existing_keys = list(current_config.keys())

                for key in existing_keys:
                    if key.startswith("ai_strategy_prompt_"):
                        continue
                    if key not in valid_keys:
                        logger.info(f"Removing deprecated/unused config: {key}")
                        current_config.pop(key)
                        dirty = True

                if ConfigHandler._migrate_custom_models_credentials(current_config):
                    dirty = True

                if dirty:
                    success = ConfigHandler._save_json_atomically(current_config, CONFIG_FILE)
                    if success:
                        ConfigHandler._config_cache = current_config
                    logger.info(
                        f"Configuration (defaults & cleanup) synchronized. Cleared deprecated keys: {set(existing_keys) - valid_keys}",
                    )

        except Exception as e:
            logger.error(f"Failed to ensure default config: {e}")

    @staticmethod
    def _save_json_atomically(data, path):
        """Helper: Atomic write for JSON config"""
        tmp_file = path + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, path)
            return True
        except Exception as e:
            logger.error(f"Atomic save failed for {path}: {e}")
            if os.path.exists(tmp_file):
                with contextlib.suppress(OSError):
                    os.remove(tmp_file)
            return False

    @staticmethod
    def _try_decrypt(value):
        """
        Helper: Try to decrypt value. Returns empty string if failed.
        """
        if not value:
            return ""
        try:
            return SecurityManager.decrypt_data(value)
        except DecryptionError:
            logger.warning(
                "Failed to decrypt config value. It might be invalid or legacy plaintext.",
            )
            return ""
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return ""

    @staticmethod
    def load_config():
        """Load config with Read Lock and Validation"""
        with ConfigHandler._lock.gen_rlock():
            if ConfigHandler._config_cache is not None:
                return ConfigHandler._config_cache.copy()

            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, encoding="utf-8") as f:
                        raw_data = json.load(f)
                        validated = AppConfig.model_validate(raw_data)
                        ConfigHandler._config_cache = validated.model_dump()
                        return ConfigHandler._config_cache.copy()
                except ValidationError as e:
                    logger.warning("[ConfigHandler] Config validation failed: %s", DataSanitizer.sanitize_error(e))
                    ConfigHandler._config_cache = get_default_config()
                    return ConfigHandler._config_cache.copy()
                except Exception as e:
                    logger.warning(f"[ConfigHandler] Failed to load config file: {e}")
                    return {}
            return {}

    @staticmethod
    def load_config_with_validation() -> ConfigValidationResult:
        """加载配置并返回验证详情 (供 UI 层使用)"""
        with ConfigHandler._lock.gen_rlock():
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, encoding="utf-8") as f:
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
                        if err.get("input") is not None and err.get("loc") and err["loc"][-1] in SENSITIVE_KEYS:
                            err_str = err_str.replace(str(err["input"]), "***")
                        errors.append(err_str)
                    return ConfigValidationResult(
                        is_valid=False,
                        config=get_default_config(),
                        errors=errors,
                        used_defaults=True,
                    )
                except Exception as e:
                    return ConfigValidationResult(
                        is_valid=False,
                        config={},
                        errors=[str(e)],
                        used_defaults=False,
                    )
            return ConfigValidationResult(
                is_valid=True,
                config=get_default_config(),
                errors=[],
                used_defaults=True,
            )

    @staticmethod
    def save_config(config_data, replace=False):
        """
        Save config with Write Lock, Validation and Atomic Write
        :param config_data: Dict to save
        :param replace: If True, replaces entire config with config_data. If False, merges.
        """
        try:
            with ConfigHandler._lock.gen_wlock():
                if replace:
                    current_config = config_data.copy()
                else:
                    current_config = {}
                    if ConfigHandler._config_cache is not None:
                        current_config = ConfigHandler._config_cache.copy()
                    elif os.path.exists(CONFIG_FILE):
                        try:
                            with open(CONFIG_FILE, encoding="utf-8") as f:
                                current_config = json.load(f)
                        except (json.JSONDecodeError, OSError):
                            pass
                    current_config.update(config_data)

                try:
                    validated = AppConfig.model_validate(current_config)
                    current_config = validated.model_dump()
                except ValidationError as e:
                    logger.error("[ConfigHandler] Invalid config data: %s", DataSanitizer.sanitize_error(e))
                    return False

                success = ConfigHandler._save_json_atomically(
                    current_config,
                    CONFIG_FILE,
                )

                if success:
                    ConfigHandler._config_cache = current_config
                    return True
                return False
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False

    @staticmethod
    def get_token():
        # 1. 环境变量优先（最高优先级）
        env_token = os.environ.get(ENV_FALLBACK_MAP["ts_token"])
        if env_token:
            return env_token

        # 2. keyring
        kr_token = None
        try:
            kr_token = keyring.get_password(KEYRING_SERVICE_NAME, "ts_token")
        except Exception as e:
            logger.debug(f"Keyring get_password for ts_token failed: {e}")
        if kr_token:
            return kr_token

        # 3. 加密配置文件（如果 SecurityManager 可用）
        config = ConfigHandler.load_config()
        token = config.get("ts_token", "")
        decrypted = ConfigHandler._try_decrypt(token)
        if decrypted:
            try:
                keyring.set_password(KEYRING_SERVICE_NAME, "ts_token", decrypted)
                ConfigHandler.save_config({"ts_token": ""})
                logger.info("Migrated ts_token from config to keyring and cleared legacy value")
            except Exception as e:
                logger.debug(f"Keyring migration failed: {e}")
        return decrypted

    @staticmethod
    def save_token(token):
        if not token:
            try:
                keyring.delete_password(KEYRING_SERVICE_NAME, "ts_token")
            except Exception:
                logger.debug("Keyring ts_token deletion skipped (not stored or keyring unavailable)")
            return ConfigHandler.save_config({"ts_token": ""})

        try:
            keyring.set_password(KEYRING_SERVICE_NAME, "ts_token", token)
            # Clear legacy setting from JSON
            return ConfigHandler.save_config({"ts_token": ""})
        except Exception as e:
            logger.warning(
                f"Failed to use keyring for ts_token: {e}. Falling back to SecurityManager.",
            )
            try:
                encrypted = SecurityManager.encrypt_data(token)
                return ConfigHandler.save_config({"ts_token": encrypted})
            except SecurityError as se:
                logger.error(
                    f"Cannot securely store ts_token: {se}\nPlease use environment variable TS_TOKEN instead.",
                )
                return False
            except Exception as enc_err:
                logger.error(f"Failed to encrypt ts_token: {enc_err}")
                return False

    @staticmethod
    def is_onboarding_complete():
        return ConfigHandler.get_typed("onboarding_complete", bool, False)

    @staticmethod
    def set_onboarding_complete(complete=True):
        return ConfigHandler.save_config({"onboarding_complete": complete})

    @staticmethod
    def is_auto_update_enabled():
        return ConfigHandler.get_typed("auto_update_enabled", bool, False)

    @staticmethod
    def get_log_level():
        return ConfigHandler.get_typed("log_level", str, "INFO").upper()

    @staticmethod
    def set_log_level(level):
        return ConfigHandler.set_typed("log_level", level.upper())

    @staticmethod
    def get_log_format():
        return ConfigHandler.get_typed("log_format", str, "text").lower()

    @staticmethod
    def set_log_format(log_format):
        return ConfigHandler.set_typed("log_format", log_format.lower())

    @classmethod
    def get_init_history_years(cls) -> int:
        return ConfigHandler.get_typed("init_history_years", int, 3)

    @classmethod
    def set_init_history_years(cls, years: int):
        years = max(1, min(5, int(years)))
        return ConfigHandler.set_typed("init_history_years", years)

    @staticmethod
    def get_auto_update_time():
        return ConfigHandler.get_typed("auto_update_time", str, "16:30")

    @classmethod
    def is_doubao_schedule_enabled(cls) -> bool:
        return ConfigHandler.get_typed("doubao_schedule_enabled", bool, False)

    @classmethod
    def set_doubao_schedule_enabled(cls, enabled: bool):
        return ConfigHandler.set_typed("doubao_schedule_enabled", bool(enabled))

    @classmethod
    def get_doubao_schedule_time(cls) -> str:
        return ConfigHandler.get_typed("doubao_schedule_time", str, "10:00")

    @classmethod
    def set_doubao_schedule_time(cls, time_str: str):
        return ConfigHandler.set_typed("doubao_schedule_time", str(time_str))

    @staticmethod
    def get_log_max_mb():
        return ConfigHandler.get_typed("log_max_mb", int, 5)

    @staticmethod
    def get_log_backup_count():
        return ConfigHandler.get_typed("log_backup_count", int, 5)

    @staticmethod
    def get_db_connection_pool_size():
        return ConfigHandler.get_typed("db_connection_pool_size", int, 10)

    @staticmethod
    def get_db_pool_pre_ping():
        return ConfigHandler.get_typed("db_pool_pre_ping", bool, True)

    @staticmethod
    def get_db_pool_recycle():
        return ConfigHandler.get_typed("db_pool_recycle", int, 1800)

    @staticmethod
    def get_db_pool_timeout():
        return ConfigHandler.get_typed("db_pool_timeout", int, 30)

    @staticmethod
    def get_db_max_overflow():
        return ConfigHandler.get_typed("db_max_overflow", int, 5)

    @staticmethod
    def get_db_url():
        """Get PostgreSQL connection URL by rebuilding from components.

        Reconstructs the URL from stored host/port/user/database + password
        via DatabaseConfigService.build_url(), which properly URL-encodes
        credentials. Falls back to config.DB_URL (from DATABASE_URL env var)
        only when no db_host is configured (pre-onboarding).
        """
        host = ConfigHandler.get_typed("db_host", str, "")
        if host:
            from data.persistence.db_config_service import DatabaseConfigService

            password = ConfigHandler.get_db_password()
            return DatabaseConfigService.build_url(
                host=host,
                port=ConfigHandler.get_typed("db_port", int, 5432),
                user=ConfigHandler.get_typed("db_user", str, "postgres"),
                password=password,
                database=ConfigHandler.get_typed("db_name", str, "astock"),
                async_driver=True,
            )
        return config.DB_URL

    @staticmethod
    def get_db_password():
        """Get database password from keyring or encrypted config."""
        # 1. 环境变量优先（最高优先级）
        env_password = os.environ.get(ENV_FALLBACK_MAP["db_password"])
        if env_password:
            return env_password

        # 2. keyring
        try:
            password = keyring.get_password(KEYRING_SERVICE_NAME, "db_password")
            if password:
                return password
        except Exception as e:
            logger.debug(f"Failed to get db_password from keyring: {e}")

        # 3. 加密配置文件
        user_config = ConfigHandler.load_config()
        encrypted = user_config.get("db_password_encrypted", "")
        if encrypted:
            return ConfigHandler._try_decrypt(encrypted)
        return ""

    @staticmethod
    def save_db_password(password: str) -> bool:
        """Save database password to keyring."""
        if not password:
            return False
        try:
            keyring.set_password(KEYRING_SERVICE_NAME, "db_password", password)
            ConfigHandler.save_config({"db_password_encrypted": ""})
            return True
        except Exception as e:
            logger.warning(f"Failed to save db_password to keyring: {e}")
            try:
                keyring.delete_password(KEYRING_SERVICE_NAME, "db_password")
            except Exception:
                logger.debug("Keyring db_password deletion skipped")
            try:
                encrypted = SecurityManager.encrypt_data(password)
                ConfigHandler.save_config({"db_password_encrypted": encrypted})
                return True
            except SecurityError as se:
                logger.error(
                    f"Cannot securely store db_password: {se}\nPlease use environment variable DB_PASSWORD instead.",
                )
                return False
            except Exception as e2:
                logger.error(f"Failed to encrypt db_password: {e2}")
                return False

    @staticmethod
    def save_db_config(
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> bool:
        """Save database configuration and sync config.DB_URL for downstream consumers."""
        from data.persistence.db_config_service import DatabaseConfigService

        db_url = DatabaseConfigService.build_url(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            async_driver=True,
        )

        # db_url in user_settings.json is kept for informational/debug purposes only;
        # password is masked. Actual URL is always rebuilt by get_db_url() from components.
        ConfigHandler.save_config(
            {
                "db_host": host,
                "db_port": port,
                "db_user": user,
                "db_name": database,
                "db_url": re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", db_url),
            }
        )

        if password:
            ConfigHandler.save_db_password(password)

        # Sync config.DB_URL / DB_URL_SYNC so that code reading these module-level
        # variables directly (e.g. DatabaseManager before this fix, Alembic env.py
        # fallback) gets the correct value after onboarding.
        config.DB_URL = db_url
        config.DB_URL_SYNC = db_url.replace("+asyncpg", "")

        return True

    @staticmethod
    def get_db_config() -> dict:
        """Get database configuration components."""
        password = ConfigHandler.get_db_password()
        return {
            "host": ConfigHandler.get_typed("db_host", str, "127.0.0.1"),
            "port": ConfigHandler.get_typed("db_port", int, 5432),
            "user": ConfigHandler.get_typed("db_user", str, "postgres"),
            "password": password,
            "database": ConfigHandler.get_typed("db_name", str, "astock"),
        }

    @staticmethod
    def set_db_connection_pool_size(size):
        return ConfigHandler.set_typed("db_connection_pool_size", int(size))

    @staticmethod
    def set_db_max_overflow(overflow):
        return ConfigHandler.set_typed("db_max_overflow", int(overflow))

    @staticmethod
    def set_db_pool_timeout(timeout):
        return ConfigHandler.set_typed("db_pool_timeout", int(timeout))

    @staticmethod
    def get_sync_concurrency():
        """Alias for get_sync_max_concurrent_heavy() for backward compatibility."""
        return ConfigHandler.get_sync_max_concurrent_heavy()

    @staticmethod
    def set_sync_concurrency(concurrency):
        """Alias for set_sync_max_concurrent_heavy() for backward compatibility."""
        return ConfigHandler.set_sync_max_concurrent_heavy(int(concurrency))

    @staticmethod
    def get_max_batch_rows():
        return ConfigHandler.get_typed("max_batch_rows", int, 20000)

    @staticmethod
    def set_max_batch_rows(rows):
        return ConfigHandler.set_typed("max_batch_rows", int(rows))

    @staticmethod
    def get_no_proxy_domains():
        """Get domains that should BYPASS proxy (NO_PROXY)."""
        config = ConfigHandler.load_config()
        val = config.get("no_proxy_domains", [])
        if isinstance(val, list):
            return list(val)
        return []

    # Alias for backward compatibility if needed, but we refactored callers
    get_proxy_domains = get_no_proxy_domains

    @staticmethod
    def set_no_proxy_domains(domains):
        if not isinstance(domains, list) or not all(isinstance(x, str) for x in domains):
            logger.error("Invalid no-proxy domains format: must be list of strings")
            return False
        return ConfigHandler.save_config({"no_proxy_domains": domains})

    @staticmethod
    def get_config(key, default=None):
        config = ConfigHandler.load_config()
        return config.get(key, default)

    @staticmethod
    def get_setting(key, default=None):
        config = ConfigHandler.load_config()
        return config.get(key, default)

    @staticmethod
    def get_llm_provider() -> str:
        return ConfigHandler.get_typed("llm_provider", str, "deepseek")

    @staticmethod
    def save_llm_config(
        provider: str,
        model: str,
        base_url: str,
        api_key: str = None,  # type: ignore[assignment]
        **kwargs,
    ) -> bool:
        """
        保存 LLM 完整配置

        Args:
            provider: 供应商 ID
            model: 模型 ID
            base_url: API 基础 URL
            api_key: API Key (将加密存储)，为 None 时保持现有密钥不变，为空字符串时清除密钥
            **kwargs: 扩展字段 (如 Azure 的 api_version, azure_resource_name, azure_deployment_name)
        """
        from utils.llm_providers import AZURE_DEFAULT_API_VERSION

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

        if not ConfigHandler.save_config(config_update):
            return False

        if api_key is not None:
            if api_key:
                try:
                    keyring.set_password(KEYRING_SERVICE_NAME, "ai_api_key", api_key)
                except Exception as e:
                    logger.warning(f"Keyring save failed: {e}. Falling back to SecurityManager.")
                    try:
                        encrypted_key = SecurityManager.encrypt_data(api_key)
                        ConfigHandler.save_config({"ai_api_key": encrypted_key})
                    except SecurityError as se:
                        logger.error(
                            f"Cannot securely store ai_api_key: {se}\n"
                            "Please use environment variable AI_API_KEY instead.",
                        )
                        return False
                    except Exception as enc_err:
                        logger.error(f"Failed to encrypt ai_api_key: {enc_err}")
                        return False
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE_NAME, "ai_api_key")
                except Exception:
                    logger.debug("Keyring ai_api_key deletion skipped")
                ConfigHandler.save_config({"ai_api_key": ""})

        return True

    @staticmethod
    def get_llm_config() -> dict:
        """
        获取 LLM 完整配置

        Returns:
            {
                "provider": str,
                "model": str,
                "base_url": str,
                "api_key": str,
                "api_version": str,  # Azure 专用
                "azure_resource_name": str,  # Azure 专用
                "azure_deployment_name": str,  # Azure 专用
                "custom_models": dict,
            }
        """
        config = ConfigHandler.load_config()

        # 1. 环境变量优先（最高优先级）
        api_key = os.environ.get(ENV_FALLBACK_MAP["ai_api_key"])

        # 2. keyring
        if not api_key:
            try:
                api_key = keyring.get_password(KEYRING_SERVICE_NAME, "ai_api_key")
            except Exception as exc:
                logger.debug(f"Keyring get_password for ai_api_key failed: {exc}")

        # 3. 加密配置文件
        if not api_key:
            encrypted = config.get("ai_api_key", "")
            api_key = ConfigHandler._try_decrypt(encrypted)
            if api_key:
                try:
                    keyring.set_password(KEYRING_SERVICE_NAME, "ai_api_key", api_key)
                    ConfigHandler.save_config({"ai_api_key": ""})
                    logger.info("Migrated ai_api_key from config to keyring and cleared legacy value")
                except Exception as exc:
                    logger.debug(f"[ConfigHandler] Keyring migration for ai_api_key skipped: {exc}")

        provider = config.get("llm_provider", "deepseek")
        model = config.get("llm_model", "deepseek-v4-flash")
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

        return {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "api_version": api_version,
            "azure_resource_name": azure_resource_name,
            "azure_deployment_name": azure_deployment_name,
            "custom_models": copy.deepcopy(custom_models),
        }

    @staticmethod
    def get_failover_config() -> dict:
        """
        P1-12: 获取多供应商 fallback 配置

        Returns:
            {
                "primary": str,  # 主供应商模型 "provider/model"
                "fallbacks": list[str],  # 备用供应商模型列表
            }
        """
        llm_config = ConfigHandler.get_llm_config()
        provider = llm_config.get("provider", "")
        model = llm_config.get("model", "")
        primary = f"{provider}/{model}" if provider else model

        config = ConfigHandler.load_config()
        fallbacks = config.get("llm_failover_models", [])

        if not isinstance(fallbacks, list):
            fallbacks = []

        return {
            "primary": primary,
            "fallbacks": fallbacks,
            "primary_config": llm_config,
        }

    @staticmethod
    def save_provider_credential(
        provider: str,
        api_key: str | None = None,
        base_url: str | None = None,
        models: list[str] | None = None,
    ) -> bool:
        """
        保存指定 LLM 供应商的凭证（用于跨供应商 failover）

        Args:
            provider: 供应商 ID (如 "qwen", "deepseek", "openai")
            api_key: API Key。None 表示不修改，空字符串表示清除，非空表示更新。
            base_url: API 基础 URL。None 表示不修改，空字符串表示清除，非空表示更新。
            models: 该供应商的自定义模型列表。None 表示不修改。

        Returns:
            bool: 保存是否成功
        """
        config = ConfigHandler.load_config()

        provider_credentials = config.get("llm_provider_credentials", {})
        if not isinstance(provider_credentials, dict):
            provider_credentials = {}

        cred = provider_credentials.get(provider, {})

        config_update = {}

        # base_url: None=不修改, ""=清除, 其他=更新
        if base_url is not None:
            if base_url:
                cred["base_url"] = base_url
            elif "base_url" in cred:
                del cred["base_url"]

        provider_credentials[provider] = cred
        config_update["llm_provider_credentials"] = provider_credentials

        if models is not None:
            custom_models = config.get("llm_custom_models", {})
            existing_models = custom_models.get(provider, [])
            updated_models = list(existing_models)
            for m in models:
                if m not in updated_models:
                    updated_models.append(m)
            if len(updated_models) > 50:
                updated_models = updated_models[-50:]
            custom_models[provider] = updated_models
            config_update["llm_custom_models"] = custom_models

        # api_key: None=不修改, ""=清除, 其他=更新
        if api_key is not None:
            if api_key:
                try:
                    keyring.set_password(KEYRING_SERVICE_NAME, f"ai_api_key_{provider}", api_key)
                except Exception as e:
                    logger.warning(
                        f"[ConfigHandler] Keyring save failed for {provider}: {e}. Falling back to encrypted storage."
                    )
                    try:
                        encrypted_key = SecurityManager.encrypt_data(api_key)
                        cred["api_key_encrypted"] = encrypted_key
                        provider_credentials[provider] = cred
                        config_update["llm_provider_credentials"] = provider_credentials
                    except Exception as enc_err:
                        logger.error(f"[ConfigHandler] Failed to encrypt api_key for {provider}: {enc_err}")
                        return False
            else:
                # 用户主动清空密钥，清除 keyring 和加密存储
                try:
                    keyring.delete_password(KEYRING_SERVICE_NAME, f"ai_api_key_{provider}")
                except keyring.errors.PasswordDeleteError:  # type: ignore[reportAttributeAccessIssue]  # keyring.errors is available at runtime
                    pass
                except Exception:
                    pass
                if "api_key_encrypted" in cred:
                    del cred["api_key_encrypted"]
                    provider_credentials[provider] = cred
                    config_update["llm_provider_credentials"] = provider_credentials

        ConfigHandler.save_config(config_update)

        return True

    @staticmethod
    def get_provider_credential(provider: str) -> dict:
        """
        获取指定 LLM 供应商的完整凭证

        Args:
            provider: 供应商 ID

        Returns:
            {
                "api_key": str | None,
                "base_url": str,
                "models": list[str],
            }
        """
        config = ConfigHandler.load_config()

        provider_credentials = config.get("llm_provider_credentials", {})
        cred = provider_credentials.get(provider, {})

        api_key = None

        try:
            api_key = keyring.get_password(KEYRING_SERVICE_NAME, f"ai_api_key_{provider}")
        except Exception:
            pass

        if not api_key and cred.get("api_key_encrypted"):
            try:
                api_key = SecurityManager.decrypt_data(cred["api_key_encrypted"])
            except Exception:
                pass

        # Fallback to global api_key if provider-specific key not found
        if not api_key:
            # Try keyring global key first
            try:
                api_key = keyring.get_password(KEYRING_SERVICE_NAME, "ai_api_key")
            except Exception:
                pass

            # Then try encrypted global key in config
            if not api_key:
                global_encrypted = config.get("ai_api_key")
                if global_encrypted:
                    try:
                        api_key = SecurityManager.decrypt_data(global_encrypted)
                    except Exception:
                        pass

        base_url = cred.get("base_url", "")
        if not base_url:
            from utils.llm_providers import LLM_PROVIDERS

            base_url = LLM_PROVIDERS.get(provider, {}).get("base_url", "")

        custom_models = config.get("llm_custom_models", {})
        provider_models = custom_models.get(provider, cred.get("models", []))
        return {
            "api_key": api_key,
            "base_url": base_url,
            "models": provider_models,
        }

    @staticmethod
    def get_llm_config_for_provider(provider: str) -> dict:
        """
        获取指定供应商的 LLM 配置（用于跨供应商 failover）

        整合 get_provider_credential 结果，返回与 get_llm_config 兼容的格式

        Args:
            provider: 供应商 ID

        Returns:
            {
                "provider": str,
                "model": str,
                "api_key": str | None,
                "base_url": str,
                "models": list[str],
            }
        """
        cred = ConfigHandler.get_provider_credential(provider)

        if not cred["models"]:
            logger.warning("[ConfigHandler] No models found for provider '%s', returning empty model", provider)

        return {
            "provider": provider,
            "model": cred["models"][0] if cred["models"] else "",
            "api_key": cred["api_key"],
            "base_url": cred["base_url"],
            "models": cred["models"],
        }

    @staticmethod
    def validate_failover_credentials() -> list[str]:
        """
        校验 failover 配置的凭证完整性

        Returns:
            list[str]: 缺少凭证的供应商列表
        """
        config = ConfigHandler.load_config()
        failover_models = config.get("llm_failover_models", [])
        missing = []
        seen = set()

        for model in failover_models:
            if "/" in model:
                provider = model.split("/")[0]
                if provider in seen:
                    continue
                model_id = model.split("/", 1)[1]
                cred = ConfigHandler.get_provider_credential(provider)
                if not cred.get("api_key"):  # noqa: SIM114
                    missing.append(provider)
                    seen.add(provider)
                elif model_id and (not cred.get("models") or model_id not in cred["models"]):
                    missing.append(provider)
                    seen.add(provider)

        return missing

    @staticmethod
    def get_local_ai_timeout() -> int:
        """
        Get local AI inference timeout in seconds.
        Value must come from user_settings.json.
        Returns:
            int: Timeout seconds, or None if not configured (wait indefinitely)
        """
        try:
            val = ConfigHandler.get_setting("local_model_timeout")
            return int(val) if val is not None else None  # type: ignore[arg-type]
        except (ValueError, TypeError):
            # If config is corrupted/invalid, treat as not set (no default provided)
            return None  # type: ignore[return-value]

    @staticmethod
    def set_local_ai_timeout(seconds: int):
        """Set local AI inference timeout (1-3600s)"""
        # Enforce bounds to be consistent with UI
        val = max(1, min(seconds, 3600))
        ConfigHandler.save_config({"local_model_timeout": val})

    @staticmethod
    def get_local_ai_config() -> dict:
        """Get local AI configuration"""
        return {
            "local_model_path": ConfigHandler.get_typed("local_model_path", str, ""),
            "local_model_timeout": ConfigHandler.get_typed("local_model_timeout", int, 90),
            "n_threads": ConfigHandler.get_typed("local_n_threads", int, 4),
            "n_batch": ConfigHandler.get_typed("local_n_batch", int, 512),
            "n_ctx": ConfigHandler.get_typed("local_n_ctx", int, 2048),
            "flash_attn": ConfigHandler.get_typed("local_flash_attn", bool, True),
            "n_gpu_layers": ConfigHandler.get_typed("local_n_gpu_layers", int, 0),
        }

    @staticmethod
    def save_local_ai_config(model_path: str, timeout: int = 30, **kwargs):
        """Save local AI configuration"""
        # Build config dict
        cfg = {"local_model_path": model_path, "local_model_timeout": timeout}

        # Save optional params
        if "n_threads" in kwargs:
            cfg["local_n_threads"] = kwargs["n_threads"]
        if "n_batch" in kwargs:
            cfg["local_n_batch"] = kwargs["n_batch"]
        if "n_ctx" in kwargs:
            cfg["local_n_ctx"] = kwargs["n_ctx"]
        if "flash_attn" in kwargs:
            cfg["local_flash_attn"] = kwargs["flash_attn"]
        if "n_gpu_layers" in kwargs:
            cfg["local_n_gpu_layers"] = kwargs["n_gpu_layers"]

        ConfigHandler.save_config(cfg)

    @staticmethod
    def get_ai_system_prompt():
        config = ConfigHandler.load_config()
        return config.get("ai_system_prompt", DEFAULT_AI_PROMPT)

    @staticmethod
    def save_ai_system_prompt(prompt):
        from utils.prompt_guard import validate_prompt, sanitize_prompt

        if prompt:
            is_valid, _ = validate_prompt(prompt)
            if not is_valid:
                return False
            prompt = sanitize_prompt(prompt)
        return ConfigHandler.save_config({"ai_system_prompt": prompt})

    @staticmethod
    def get_strategy_prompt(strategy_key):
        """Get user-customized prompt for a specific strategy."""
        config = ConfigHandler.load_config()
        key = f"ai_strategy_prompt_{strategy_key}"
        return config.get(key, None)

    @staticmethod
    def set_strategy_prompt(strategy_key, prompt):
        """Save user-customized prompt for a specific strategy."""
        from utils.prompt_guard import validate_prompt, sanitize_prompt

        if prompt:
            is_valid, _ = validate_prompt(prompt)
            if not is_valid:
                return False
            prompt = sanitize_prompt(prompt)
        key = f"ai_strategy_prompt_{strategy_key}"
        return ConfigHandler.save_config({key: prompt})

    @staticmethod
    def get_ai_news_prompt():
        """Get News Classification Prompt (returns Default if not set)"""
        config = ConfigHandler.load_config()
        val = config.get("ai_news_prompt", None)
        return val if val else DEFAULT_NEWS_PROMPT

    @staticmethod
    def set_ai_news_prompt(prompt):
        """Save news classification prompt with validation and sanitization."""
        if prompt:
            from utils.prompt_guard import sanitize_prompt, validate_prompt

            is_valid, _ = validate_prompt(prompt)
            if not is_valid:
                return False
            prompt = sanitize_prompt(prompt)
        return ConfigHandler.save_config({"ai_news_prompt": prompt})

    # === New AI Tuning Parameters ===

    @staticmethod
    def get_ai_max_candidates():
        return ConfigHandler.get_typed("ai_max_candidates", int, 30)

    @staticmethod
    def set_ai_max_candidates(val):
        return ConfigHandler.set_typed("ai_max_candidates", int(val))

    @staticmethod
    def get_sync_max_concurrent_heavy():
        val = ConfigHandler.get_typed("sync_max_concurrent_heavy", int, 3)
        return max(1, min(val, 10))

    @staticmethod
    def set_sync_max_concurrent_heavy(val):
        safe_val = max(1, min(int(val), 10))
        return ConfigHandler.save_config({"sync_max_concurrent_heavy": safe_val})

    @staticmethod
    def get_strategy_min_turnover():
        return ConfigHandler.get_typed("strategy_min_turnover", float, 2.0)

    @staticmethod
    def set_strategy_min_turnover(val):
        return ConfigHandler.set_typed("strategy_min_turnover", float(val))

    @staticmethod
    def get_ai_max_concurrent_analysis():
        val = ConfigHandler.get_typed("ai_max_concurrent_analysis", int, 5)
        return max(1, min(val, 10))

    @staticmethod
    def set_ai_max_concurrent_analysis(val):
        safe_val = max(1, min(int(val), 10))
        return ConfigHandler.set_typed("ai_max_concurrent_analysis", safe_val)

    @staticmethod
    def get_ai_news_max_concurrent():
        val = ConfigHandler.get_typed("ai_news_max_concurrent", int, 1)
        return max(1, min(val, 5))

    @staticmethod
    def set_ai_news_max_concurrent(val):
        safe_val = max(1, min(int(val), 5))
        return ConfigHandler.set_typed("ai_news_max_concurrent", safe_val)

    @staticmethod
    def get_sync_concurrency_light():
        return ConfigHandler.get_typed("sync_concurrency_light", int, 20)

    @staticmethod
    def set_sync_concurrency_light(val):
        return ConfigHandler.set_typed("sync_concurrency_light", int(val))

    # === API Robustness Parameters ===

    @staticmethod
    def get_sync_retry_count():
        return ConfigHandler.get_typed("request_max_retries", int, 3)

    @staticmethod
    def get_request_max_retries():
        return ConfigHandler.get_typed("request_max_retries", int, 3)

    @staticmethod
    def get_tushare_timeout():
        return ConfigHandler.get_typed("tushare_timeout", int, 30)

    @staticmethod
    def set_tushare_timeout(seconds):
        return ConfigHandler.set_typed("tushare_timeout", int(seconds))

    @staticmethod
    def get_tushare_api_limit():
        return ConfigHandler.get_typed("tushare_api_rate_limit", int, 200)

    @staticmethod
    def set_tushare_api_limit(limit):
        return ConfigHandler.set_typed("tushare_api_rate_limit", int(limit))

    @staticmethod
    def get_tushare_point_tier():
        return ConfigHandler.get_typed("tushare_point_tier", str, "custom")

    @staticmethod
    def set_tushare_point_tier(tier):
        valid_tiers = {"free", "standard", "pro", "flagship", "custom"}
        if tier not in valid_tiers:
            return False
        return ConfigHandler.set_typed("tushare_point_tier", str(tier))

    # === Localization ===
    @staticmethod
    def get_locale():
        return ConfigHandler.get_typed("locale", str, "zh")

    @staticmethod
    def set_locale(locale):
        return ConfigHandler.set_typed("locale", locale)

    # === Theme ===
    @staticmethod
    def get_theme_name():
        return ConfigHandler.get_typed("theme_name", str, "dark")

    @staticmethod
    def set_theme_name(theme_name):
        return ConfigHandler.set_typed("theme_name", theme_name)

    # === Scheduler ===
    # ai_prediction_time removed as it was unused

    # === Thread Pool Configuration ===
    @staticmethod
    def get_max_io_workers():
        """Get max IO threads from config, capped by DB connection pool capacity."""
        config = ConfigHandler.load_config()
        val = config.get("max_io_workers", 16)
        try:
            io_workers = int(val)
        except (ValueError, TypeError):
            return 0

        if io_workers <= 0:
            io_workers = os.cpu_count() or 4

        db_pool_size = ConfigHandler.get_typed("db_connection_pool_size", int, 10)
        db_max_overflow = ConfigHandler.get_typed("db_max_overflow", int, 5)
        db_capacity = db_pool_size + db_max_overflow

        if io_workers > db_capacity:
            logger.warning(
                f"[Config] IO workers ({io_workers}) exceeds DB connection capacity ({db_capacity}). Capping to {db_capacity}.",
            )
            io_workers = db_capacity

        return io_workers

    @staticmethod
    def set_max_io_workers(count):
        return ConfigHandler.set_typed("max_io_workers", int(count))

    @staticmethod
    def get_max_cpu_workers():
        """Get max CPU threads from config."""
        return ConfigHandler.get_typed("max_cpu_workers", int, 4)

    @staticmethod
    def set_max_cpu_workers(count):
        return ConfigHandler.set_typed("max_cpu_workers", int(count))

    # === Task Manager Configuration ===
    @staticmethod
    def get_max_concurrent_tasks():
        """Get max concurrent tasks for TaskManager.
        Defaults to cpu_workers count to avoid overwhelming the CPU pool.
        Falls back to 5 if neither is configured."""
        val = ConfigHandler.get_typed("max_concurrent_tasks", int, 0)
        if val > 0:
            return val
        cpu = ConfigHandler.get_max_cpu_workers()
        return cpu if cpu > 0 else 5

    @staticmethod
    def set_max_concurrent_tasks(count):
        return ConfigHandler.set_typed("max_concurrent_tasks", int(count))

    # === Sync Rate Limiting ===
    @staticmethod
    def get_sync_request_delay(is_heavy=False):
        if is_heavy:
            return ConfigHandler.get_typed("sync_request_delay_heavy", float, 0.0)
        return ConfigHandler.get_typed("sync_request_delay_light", float, 0.0)

    @staticmethod
    def set_sync_request_delay(delay, is_heavy=False):
        key = "sync_request_delay_heavy" if is_heavy else "sync_request_delay_light"
        return ConfigHandler.set_typed(key, float(delay))

    # === Polling Intervals ===
    @staticmethod
    def get_news_poll_interval():
        return ConfigHandler.get_typed("news_poll_interval", int, 60)

    @staticmethod
    def set_news_poll_interval(seconds):
        return ConfigHandler.set_typed("news_poll_interval", int(max(10, seconds)))

    @staticmethod
    def get_market_data_poll_interval():
        return ConfigHandler.get_typed("market_data_poll_interval", int, 30)

    @staticmethod
    def set_market_data_poll_interval(seconds):
        return ConfigHandler.set_typed("market_data_poll_interval", int(max(10, seconds)))

    @staticmethod
    def get_sync_integrity_config():
        """获取数据完整性检查配置"""
        config = ConfigHandler.load_config()
        defaults = get_default_config()["sync_integrity"]
        sync_integrity = config.get("sync_integrity", defaults)
        return {
            "quotes_tolerance_ratio": sync_integrity.get("quotes_tolerance_ratio", defaults["quotes_tolerance_ratio"]),
            "indicators_tolerance_ratio": sync_integrity.get(
                "indicators_tolerance_ratio", defaults["indicators_tolerance_ratio"]
            ),
            "moneyflow_tolerance_ratio": sync_integrity.get(
                "moneyflow_tolerance_ratio", defaults["moneyflow_tolerance_ratio"]
            ),
            "financial_min_periods": sync_integrity.get("financial_min_periods", defaults["financial_min_periods"]),
            "quality_threshold": sync_integrity.get("quality_threshold", defaults["quality_threshold"]),
            "quality_weights": sync_integrity.get("quality_weights", defaults["quality_weights"]),
        }
