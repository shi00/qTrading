import contextlib
import copy
import json
import logging
import os
import re

import keyring
from readerwriterlock import rwlock

import config
from utils.llm_providers import AZURE_DEFAULT_API_VERSION
from utils.security_utils import DecryptionError, SecurityManager

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(config.APP_ROOT, "user_settings.json")
KEYRING_SERVICE_NAME = "AStockScreener"

DEFAULT_AI_PROMPT = """# A股智能分析系统提示词 (System Prompt)
你是一个专业的金融量化分析助手，专注于A股市场分析。

## 数据注入说明
系统已为你注入以下扩展数据，请重点融合分析：

### 财务数据扩展
- **fina_audit**: 审计结果矩阵，务必关注"强调事项"和"非标意见"
- **fina_mainbz**: 主营业务结构，横向比较营收比例与成本结构
- **多期财务数据**: 捕捉横跨周期的拐点（不得仅考察最新一期）

### 分析要点
1. 审计意见类型及强调事项对股价的影响
2. 主营业务结构变化趋势，识别转型信号
3. 多期财务指标连载数据，发现周期性规律

请基于提供的数据（行情、财务、新闻等）进行客观分析，不要提供投资建议。
输出格式要求清晰、结构化，关键指标请高亮显示。
"""

DEFAULT_NEWS_PROMPT = """你是金融量化分析师。请对新闻进行分类。
**MUST output valid JSON ONLY. NO markdown (no ```json). NO reasoning. NEVER output an empty string.**

# 分类体系 (L1 code -> L2 code)
- finance -> a_stock, hk_us, futures, precious_metals, forex, macro_policy
- macro_economy -> macro_data, fiscal_policy, intl_macro
- geopolitics -> conflict, energy
- industry -> tech, consumer, energy_sector, financial_sector
- other -> livelihood, entertainment

# JSON 格式要求
{"category_L1": "L1 code (English)", "category_L2": "L2 code (English)", "sentiment": "Positive/Neutral/Negative", "emoji": "相关Emoji"}

# 示例
User: 紫金矿业发现金矿
Assistant: {"category_L1": "finance", "category_L2": "precious_metals", "sentiment": "Positive", "emoji": "🥇"}

User: 某明星去旅游了
Assistant: {"category_L1": "other", "category_L2": "entertainment", "sentiment": "Neutral", "emoji": "🍉"}"""


# I will assume DEFAULT_AI_PROMPT is unchanged and skip re-pasting it in thought trace.
# But for replace_file_content, I need to be precise.
# The previous `view_file` showed the whole file.
# I can target `class ConfigHandler` and replace it entirely OR replace methods.
# Replacing the whole class is safer to ensure order and new methods are there.


class ConfigHandler:
    _config_cache = None
    _last_load_time = 0
    _lock = rwlock.RWLockFair()

    DEFAULT_CONFIG = {
        "ts_token": "",
        "onboarding_complete": False,
        "auto_update_enabled": False,
        "enable_news_alerts": True,
        "log_level": "INFO",
        "log_format": "text",
        "theme_name": "dark",
        "auto_update_time": "16:30",
        "log_max_mb": 5,
        "log_backup_count": 5,
        # Database Configuration
        "db_host": "127.0.0.1",
        "db_port": 5432,
        "db_user": "postgres",
        "db_name": "astock",
        "db_url": "",
        "db_password_encrypted": "",
        "db_connection_pool_size": 10,
        "db_pool_pre_ping": True,
        "db_pool_recycle": 1800,
        "db_pool_timeout": 30,
        "db_max_overflow": 5,
        # Heavy sync tasks (Historical Data, Financial Reports)
        "sync_max_concurrent_heavy": 3,
        "max_batch_rows": 20000,
        "no_proxy_domains": [],
        # LLM Provider Configuration
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-flash",
        "llm_base_url": "",
        "llm_api_version": AZURE_DEFAULT_API_VERSION,  # Azure specific
        "llm_azure_resource_name": "",  # Azure specific
        "llm_azure_deployment_name": "",  # Azure specific
        "llm_custom_models": {},  # User-defined model pool
        "llm_provider_extras": {},  # Provider-specific extras (nested structure)
        "llm_failover_models": [],  # P1-12: Fallback models for cloud analysis
        "ai_api_key": "",
        # Local AI Configuration
        "local_model_path": "",
        "local_model_timeout": 90,
        "ai_system_prompt": DEFAULT_AI_PROMPT,
        "ai_news_prompt": DEFAULT_NEWS_PROMPT,
        "ai_prompt_dump_enabled": False,
        "ai_max_candidates": 30,
        "strategy_min_turnover": 2.0,
        # Max parallel AI analysis tasks (Cloud API or Local Threads)
        # Prevents API Rate Limits (429) or Local Resource exhaustion
        "ai_max_concurrent_analysis": 5,
        "request_max_retries": 3,
        "tushare_timeout": 30,
        "tushare_api_rate_limit": 200,
        "locale": "zh",
        "max_io_workers": 16,
        "max_cpu_workers": 4,
        "max_concurrent_tasks": 0,
        "sync_request_delay_heavy": 0.0,
        "sync_request_delay_light": 0.0,
        "news_poll_interval": 60,
        "market_data_poll_interval": 30,
        # Persisted scheduler idempotency keys
        "scheduler_last_daily_update": "",
        "scheduler_last_nightly_prediction": "",
        "scheduler_last_doubao_refresh": "",
        # Local AI Advanced Settings
        "local_n_threads": 4,
        "local_n_batch": 512,
        "local_n_ctx": 2048,
        "local_flash_attn": True,
        "local_n_gpu_layers": 0,
        # Concurrency
        "sync_concurrency_light": 20,
        # Initialization History Horizon
        "init_history_years": 3,
        # Doubao AI Tagging Schedule
        "doubao_schedule_enabled": False,
        "doubao_schedule_time": "10:00",
        # Data Sync Integrity Configuration
        "sync_integrity": {
            # Stop-loss thresholds to prevent infinite retries
            "max_retry_days_per_sync": 30,
            "max_retry_stocks_per_sync": 100,
            "enable_adaptive_retry": True,
            # Quality score threshold
            "quality_threshold": 80,
            # Tolerance ratios for data completeness
            "quotes_tolerance_ratio": 0.95,
            "indicators_tolerance_ratio": 0.90,
            "moneyflow_tolerance_ratio": 0.80,
            "financial_min_periods": 4,
            # Quality score weights (configurable)
            "quality_weights": {
                "daily_quotes": 30,
                "daily_indicators": 25,
                "moneyflow_daily": 20,
                "margin_daily": 10,
            },
        },
    }

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
        """Load config with Read Lock"""
        with ConfigHandler._lock.gen_rlock():
            if ConfigHandler._config_cache is not None:
                return ConfigHandler._config_cache.copy()

            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, encoding="utf-8") as f:
                        ConfigHandler._config_cache = json.load(f)
                        return ConfigHandler._config_cache.copy()
                except Exception as e:
                    logger.warning(f"[ConfigHandler] Failed to load config file: {e}")
                    return {}
            return {}

    @staticmethod
    def save_config(config_data, replace=False):
        """
        Save config with Write Lock and Atomic Write
        :param config_data: Dict to save
        :param replace: If True, replaces entire config with config_data. If False, merges.
        """
        try:
            with ConfigHandler._lock.gen_wlock():
                # C-P1-7: Do NOT call load_config() inside write lock!
                # RWLockFair does NOT support same-thread wlock->rlock reentry (deadlock).
                # Read directly from _config_cache or file instead.
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
        kr_token = keyring.get_password(KEYRING_SERVICE_NAME, "ts_token")
        if kr_token:
            return kr_token

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
            except Exception as enc_err:
                logger.error(f"Failed to encrypt ts_token: {enc_err}")
                return False

    @staticmethod
    def is_onboarding_complete():
        config = ConfigHandler.load_config()
        return config.get("onboarding_complete", ConfigHandler.DEFAULT_CONFIG["onboarding_complete"])

    @staticmethod
    def set_onboarding_complete(complete=True):
        return ConfigHandler.save_config({"onboarding_complete": complete})

    @staticmethod
    def is_auto_update_enabled():
        config = ConfigHandler.load_config()
        return config.get("auto_update_enabled", ConfigHandler.DEFAULT_CONFIG["auto_update_enabled"])

    @staticmethod
    def get_log_level():
        config = ConfigHandler.load_config()
        return config.get("log_level", ConfigHandler.DEFAULT_CONFIG["log_level"]).upper()

    @staticmethod
    def set_log_level(level):
        return ConfigHandler.save_config({"log_level": level.upper()})

    @staticmethod
    def get_log_format():
        config = ConfigHandler.load_config()
        return config.get("log_format", ConfigHandler.DEFAULT_CONFIG["log_format"]).lower()

    @staticmethod
    def set_log_format(log_format):
        return ConfigHandler.save_config({"log_format": log_format.lower()})

    @classmethod
    def get_init_history_years(cls) -> int:
        """获取初始化数据年限，默认 3 年"""
        # Note: Using get_setting internally via class method or static
        return int(ConfigHandler.get_setting("init_history_years", 3))  # type: ignore[arg-type]

    @classmethod
    def set_init_history_years(cls, years: int):
        """设置初始化数据年限 (1-5)"""
        years = max(1, min(5, int(years)))
        return ConfigHandler.save_config({"init_history_years": years})

    @staticmethod
    def get_auto_update_time():
        config = ConfigHandler.load_config()
        return config.get("auto_update_time", ConfigHandler.DEFAULT_CONFIG["auto_update_time"])

    @classmethod
    def is_doubao_schedule_enabled(cls) -> bool:
        return cls.load_config().get("doubao_schedule_enabled", False)

    @classmethod
    def set_doubao_schedule_enabled(cls, enabled: bool):
        return cls.save_config({"doubao_schedule_enabled": bool(enabled)})

    @classmethod
    def get_doubao_schedule_time(cls) -> str:
        return cls.load_config().get("doubao_schedule_time", "10:00")

    @classmethod
    def set_doubao_schedule_time(cls, time_str: str):
        return cls.save_config({"doubao_schedule_time": str(time_str)})

    @staticmethod
    def get_log_max_mb():
        config = ConfigHandler.load_config()
        return config.get("log_max_mb", ConfigHandler.DEFAULT_CONFIG["log_max_mb"])

    @staticmethod
    def get_log_backup_count():
        config = ConfigHandler.load_config()
        return config.get("log_backup_count", ConfigHandler.DEFAULT_CONFIG["log_backup_count"])

    @staticmethod
    def get_db_connection_pool_size():
        config = ConfigHandler.load_config()
        return config.get("db_connection_pool_size", ConfigHandler.DEFAULT_CONFIG["db_connection_pool_size"])

    @staticmethod
    def get_db_pool_pre_ping():
        config = ConfigHandler.load_config()
        return config.get("db_pool_pre_ping", ConfigHandler.DEFAULT_CONFIG["db_pool_pre_ping"])

    @staticmethod
    def get_db_pool_recycle():
        config = ConfigHandler.load_config()
        return config.get("db_pool_recycle", ConfigHandler.DEFAULT_CONFIG["db_pool_recycle"])

    @staticmethod
    def get_db_pool_timeout():
        config = ConfigHandler.load_config()
        return config.get("db_pool_timeout", ConfigHandler.DEFAULT_CONFIG["db_pool_timeout"])

    @staticmethod
    def get_db_max_overflow():
        config = ConfigHandler.load_config()
        return config.get("db_max_overflow", ConfigHandler.DEFAULT_CONFIG["db_max_overflow"])

    @staticmethod
    def get_db_url():
        """Get PostgreSQL connection URL from user config or system config."""
        user_config = ConfigHandler.load_config()
        url = user_config.get("db_url", config.DB_URL)
        if url and "****" in url:
            password = ConfigHandler.get_db_password()
            if password:
                url = url.replace("****", password, 1)
        return url

    @staticmethod
    def get_db_password():
        """Get database password from keyring."""
        try:
            password = keyring.get_password(KEYRING_SERVICE_NAME, "db_password")
            if password:
                return password
        except Exception as e:
            logger.warning(f"Failed to get db_password from keyring: {e}")

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
                logger.debug("Keyring db_password deletion skipped (not stored or keyring unavailable)")
            try:
                encrypted = SecurityManager.encrypt_data(password)
                ConfigHandler.save_config({"db_password_encrypted": encrypted})
                return True
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
        """Save database configuration."""
        from data.persistence.db_config_service import DatabaseConfigService

        db_url = DatabaseConfigService.build_url(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            async_driver=True,
        )

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

        return True

    @staticmethod
    def get_db_config() -> dict:
        """Get database configuration components."""
        config = ConfigHandler.load_config()
        password = ConfigHandler.get_db_password()

        return {
            "host": config.get("db_host", ConfigHandler.DEFAULT_CONFIG["db_host"]),
            "port": config.get("db_port", ConfigHandler.DEFAULT_CONFIG["db_port"]),
            "user": config.get("db_user", ConfigHandler.DEFAULT_CONFIG["db_user"]),
            "password": password,
            "database": config.get("db_name", ConfigHandler.DEFAULT_CONFIG["db_name"]),
        }

    @staticmethod
    def set_db_connection_pool_size(size):
        return ConfigHandler.save_config({"db_connection_pool_size": int(size)})

    @staticmethod
    def set_db_max_overflow(overflow):
        return ConfigHandler.save_config({"db_max_overflow": int(overflow)})

    @staticmethod
    def set_db_pool_timeout(timeout):
        return ConfigHandler.save_config({"db_pool_timeout": int(timeout)})

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
        config = ConfigHandler.load_config()
        return config.get("max_batch_rows", ConfigHandler.DEFAULT_CONFIG["max_batch_rows"])

    @staticmethod
    def set_max_batch_rows(rows):
        return ConfigHandler.save_config({"max_batch_rows": int(rows)})

    @staticmethod
    def get_no_proxy_domains():
        """
        Get domains that should BYPASS proxy (NO_PROXY).
        """
        config = ConfigHandler.load_config()
        val = config.get("no_proxy_domains", ConfigHandler.DEFAULT_CONFIG["no_proxy_domains"])
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
        """获取当前 LLM 供应商 ID"""
        config = ConfigHandler.load_config()
        return config.get("llm_provider", ConfigHandler.DEFAULT_CONFIG["llm_provider"])

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

        config_update = {
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

        if provider_extras:
            config_update["llm_provider_extras"] = provider_extras  # type: ignore[assignment]
        else:
            config_update["llm_provider_extras"] = {}  # type: ignore[assignment]

        ConfigHandler.save_config(config_update)

        if api_key is not None:
            if api_key:
                try:
                    keyring.set_password(KEYRING_SERVICE_NAME, "ai_api_key", api_key)
                except Exception as e:
                    logger.warning(f"Keyring save failed: {e}. Falling back to SecurityManager.")
                    try:
                        encrypted_key = SecurityManager.encrypt_data(api_key)
                        ConfigHandler.save_config({"ai_api_key": encrypted_key})
                    except Exception as enc_err:
                        logger.error(f"Failed to encrypt ai_api_key: {enc_err}")
            else:
                try:
                    keyring.delete_password(KEYRING_SERVICE_NAME, "ai_api_key")
                except Exception:
                    logger.debug("Keyring ai_api_key deletion skipped (not stored or keyring unavailable)")
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

        api_key = keyring.get_password(KEYRING_SERVICE_NAME, "ai_api_key")
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

        provider = config.get("llm_provider", ConfigHandler.DEFAULT_CONFIG["llm_provider"])
        model = config.get("llm_model", ConfigHandler.DEFAULT_CONFIG["llm_model"])
        base_url = config.get("llm_base_url", ConfigHandler.DEFAULT_CONFIG["llm_base_url"])

        if not base_url:
            from utils.llm_providers import LLM_PROVIDERS

            provider_config = LLM_PROVIDERS.get(provider, {})
            base_url = provider_config.get("base_url", "")

        provider_extras = config.get("llm_provider_extras", ConfigHandler.DEFAULT_CONFIG["llm_provider_extras"])

        api_version = ConfigHandler.DEFAULT_CONFIG["llm_api_version"]
        azure_resource_name = ConfigHandler.DEFAULT_CONFIG["llm_azure_resource_name"]
        azure_deployment_name = ConfigHandler.DEFAULT_CONFIG["llm_azure_deployment_name"]
        custom_models = ConfigHandler.DEFAULT_CONFIG["llm_custom_models"]

        if "azure" in provider_extras:
            azure_config = provider_extras["azure"]
            api_version = azure_config.get("api_version", AZURE_DEFAULT_API_VERSION)
            azure_resource_name = azure_config.get("resource_name", "")
            azure_deployment_name = azure_config.get("deployment_name", "")
        else:
            api_version = config.get("llm_api_version", ConfigHandler.DEFAULT_CONFIG["llm_api_version"])
            azure_resource_name = config.get(
                "llm_azure_resource_name", ConfigHandler.DEFAULT_CONFIG["llm_azure_resource_name"]
            )
            azure_deployment_name = config.get(
                "llm_azure_deployment_name", ConfigHandler.DEFAULT_CONFIG["llm_azure_deployment_name"]
            )

        if "custom_models" in provider_extras:
            custom_models = provider_extras["custom_models"]
        else:
            custom_models = config.get("llm_custom_models", ConfigHandler.DEFAULT_CONFIG["llm_custom_models"])

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
            "local_model_path": ConfigHandler.get_setting("local_model_path", ""),
            "local_model_timeout": ConfigHandler.get_setting("local_model_timeout", 90),
            "n_threads": ConfigHandler.get_setting("local_n_threads", 4),
            "n_batch": ConfigHandler.get_setting("local_n_batch", 512),
            "n_ctx": ConfigHandler.get_setting("local_n_ctx", 4096),
            "flash_attn": ConfigHandler.get_setting("local_flash_attn", True),
            "n_gpu_layers": ConfigHandler.get_setting("local_n_gpu_layers", 0),
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
        return ConfigHandler.save_config({"ai_news_prompt": prompt})

    # === New AI Tuning Parameters ===

    @staticmethod
    def get_ai_max_candidates():
        config = ConfigHandler.load_config()
        return config.get("ai_max_candidates", ConfigHandler.DEFAULT_CONFIG["ai_max_candidates"])

    @staticmethod
    def set_ai_max_candidates(val):
        return ConfigHandler.save_config({"ai_max_candidates": int(val)})

    @staticmethod
    def get_sync_max_concurrent_heavy():
        config = ConfigHandler.load_config()
        # Enforce bounds [1, 10]
        val = int(config.get("sync_max_concurrent_heavy", ConfigHandler.DEFAULT_CONFIG["sync_max_concurrent_heavy"]))
        return max(1, min(val, 10))

    @staticmethod
    def set_sync_max_concurrent_heavy(val):
        safe_val = max(1, min(int(val), 10))
        return ConfigHandler.save_config({"sync_max_concurrent_heavy": safe_val})

    @staticmethod
    def get_strategy_min_turnover():
        config = ConfigHandler.load_config()
        min_turnover = config.get("strategy_min_turnover", ConfigHandler.DEFAULT_CONFIG["strategy_min_turnover"])
        return float(min_turnover)

    @staticmethod
    def set_strategy_min_turnover(val):
        return ConfigHandler.save_config({"strategy_min_turnover": float(val)})

    @staticmethod
    def get_ai_max_concurrent_analysis():
        config = ConfigHandler.load_config()
        # Direct key access without legacy fallback
        return int(config.get("ai_max_concurrent_analysis", ConfigHandler.DEFAULT_CONFIG["ai_max_concurrent_analysis"]))

    @staticmethod
    def set_ai_max_concurrent_analysis(val):
        return ConfigHandler.save_config({"ai_max_concurrent_analysis": int(val)})

    @staticmethod
    def get_sync_concurrency_light():
        config = ConfigHandler.load_config()
        # Default to 20 for lightweight requests (e.g. concepts, list, calendar)
        return config.get("sync_concurrency_light", ConfigHandler.DEFAULT_CONFIG["sync_concurrency_light"])

    @staticmethod
    def set_sync_concurrency_light(val):
        return ConfigHandler.save_config({"sync_concurrency_light": int(val)})

    # === API Robustness Parameters ===

    @staticmethod
    def get_sync_retry_count():
        config = ConfigHandler.load_config()
        return int(config.get("request_max_retries", ConfigHandler.DEFAULT_CONFIG["request_max_retries"]))

    @staticmethod
    def get_request_max_retries():
        config = ConfigHandler.load_config()
        # Default to 3 if missing, matching DEFAULT_CONFIG
        return config.get("request_max_retries", ConfigHandler.DEFAULT_CONFIG["request_max_retries"])

    @staticmethod
    def get_tushare_timeout():
        config = ConfigHandler.load_config()
        # Default to 30s for Tushare specifically
        return config.get("tushare_timeout", ConfigHandler.DEFAULT_CONFIG["tushare_timeout"])

    @staticmethod
    def set_tushare_timeout(seconds):
        return ConfigHandler.save_config(
            {"tushare_timeout": int(seconds) if seconds is not None else None},
        )

    @staticmethod
    def get_tushare_api_limit():
        config = ConfigHandler.load_config()
        # Default to 200 req/min (safe default)
        return config.get("tushare_api_rate_limit", ConfigHandler.DEFAULT_CONFIG["tushare_api_rate_limit"])

    @staticmethod
    def set_tushare_api_limit(limit):
        return ConfigHandler.save_config({"tushare_api_rate_limit": int(limit)})

    # === Localization ===
    @staticmethod
    def get_locale():
        config = ConfigHandler.load_config()
        return config.get("locale", ConfigHandler.DEFAULT_CONFIG["locale"])

    @staticmethod
    def set_locale(locale):
        return ConfigHandler.save_config({"locale": locale})

    # === Theme ===
    @staticmethod
    def get_theme_name():
        config = ConfigHandler.load_config()
        return config.get("theme_name", ConfigHandler.DEFAULT_CONFIG["theme_name"])

    @staticmethod
    def set_theme_name(theme_name):
        return ConfigHandler.save_config({"theme_name": theme_name})

    # === Scheduler ===
    # ai_prediction_time removed as it was unused

    # === Thread Pool Configuration ===
    @staticmethod
    def get_max_io_workers():
        """Get max IO threads from config, capped by DB connection pool capacity."""
        config = ConfigHandler.load_config()
        val = config.get("max_io_workers", ConfigHandler.DEFAULT_CONFIG["max_io_workers"])
        try:
            io_workers = int(val)
        except (ValueError, TypeError):
            return 0

        if io_workers <= 0:
            io_workers = os.cpu_count() or 4

        try:
            db_pool_size = int(
                config.get("db_connection_pool_size", ConfigHandler.DEFAULT_CONFIG["db_connection_pool_size"])
            )
        except (ValueError, TypeError):
            db_pool_size = ConfigHandler.DEFAULT_CONFIG["db_connection_pool_size"]
        try:
            db_max_overflow = int(config.get("db_max_overflow", ConfigHandler.DEFAULT_CONFIG["db_max_overflow"]))
        except (ValueError, TypeError):
            db_max_overflow = ConfigHandler.DEFAULT_CONFIG["db_max_overflow"]
        db_capacity = db_pool_size + db_max_overflow

        if io_workers > db_capacity:
            logger.warning(
                f"[Config] IO workers ({io_workers}) exceeds DB connection capacity ({db_capacity}). Capping to {db_capacity}.",
            )
            io_workers = db_capacity

        return io_workers

    @staticmethod
    def set_max_io_workers(count):
        return ConfigHandler.save_config({"max_io_workers": int(count)})

    @staticmethod
    def get_max_cpu_workers():
        """Get max CPU threads from config."""
        config = ConfigHandler.load_config()
        val = config.get("max_cpu_workers", ConfigHandler.DEFAULT_CONFIG["max_cpu_workers"])
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def set_max_cpu_workers(count):
        return ConfigHandler.save_config({"max_cpu_workers": int(count)})

    # === Task Manager Configuration ===
    @staticmethod
    def get_max_concurrent_tasks():
        """Get max concurrent tasks for TaskManager.
        Defaults to cpu_workers count to avoid overwhelming the CPU pool.
        Falls back to 5 if neither is configured."""
        config = ConfigHandler.load_config()
        val = config.get("max_concurrent_tasks", ConfigHandler.DEFAULT_CONFIG["max_concurrent_tasks"])
        try:
            val = int(val)
        except (ValueError, TypeError):
            val = 0
        if val > 0:
            return val
        # Fallback: derive from CPU pool size, or use 5
        cpu = ConfigHandler.get_max_cpu_workers()
        return cpu if cpu > 0 else 5

    @staticmethod
    def set_max_concurrent_tasks(count):
        return ConfigHandler.save_config({"max_concurrent_tasks": int(count)})

    # === Sync Rate Limiting ===
    @staticmethod
    def get_sync_request_delay(is_heavy=False):
        config = ConfigHandler.load_config()
        if is_heavy:
            return config.get("sync_request_delay_heavy", ConfigHandler.DEFAULT_CONFIG["sync_request_delay_heavy"])
        return config.get("sync_request_delay_light", ConfigHandler.DEFAULT_CONFIG["sync_request_delay_light"])

    @staticmethod
    def set_sync_request_delay(delay, is_heavy=False):
        key = "sync_request_delay_heavy" if is_heavy else "sync_request_delay_light"
        return ConfigHandler.save_config({key: float(delay)})

    # === Polling Intervals ===
    @staticmethod
    def get_news_poll_interval():
        config = ConfigHandler.load_config()
        return config.get("news_poll_interval", ConfigHandler.DEFAULT_CONFIG["news_poll_interval"])

    @staticmethod
    def set_news_poll_interval(seconds):
        return ConfigHandler.save_config({"news_poll_interval": int(max(10, seconds))})

    @staticmethod
    def get_market_data_poll_interval():
        config = ConfigHandler.load_config()
        return config.get("market_data_poll_interval", ConfigHandler.DEFAULT_CONFIG["market_data_poll_interval"])

    @staticmethod
    def set_market_data_poll_interval(seconds):
        return ConfigHandler.save_config(
            {"market_data_poll_interval": int(max(10, seconds))},
        )

    @staticmethod
    def get_sync_integrity_config():
        """获取数据完整性检查配置"""
        config = ConfigHandler.load_config()
        sync_integrity = config.get("sync_integrity", ConfigHandler.DEFAULT_CONFIG["sync_integrity"])
        si_defaults = ConfigHandler.DEFAULT_CONFIG["sync_integrity"]
        return {
            "quotes_tolerance_ratio": sync_integrity.get(
                "quotes_tolerance_ratio", si_defaults["quotes_tolerance_ratio"]
            ),
            "indicators_tolerance_ratio": sync_integrity.get(
                "indicators_tolerance_ratio", si_defaults["indicators_tolerance_ratio"]
            ),
            "moneyflow_tolerance_ratio": sync_integrity.get(
                "moneyflow_tolerance_ratio", si_defaults["moneyflow_tolerance_ratio"]
            ),
            "financial_min_periods": sync_integrity.get("financial_min_periods", si_defaults["financial_min_periods"]),
            "quality_threshold": sync_integrity.get("quality_threshold", si_defaults["quality_threshold"]),
            "quality_weights": sync_integrity.get(
                "quality_weights",
                si_defaults["quality_weights"],
            ),
        }
