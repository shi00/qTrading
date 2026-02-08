import json
import logging
import os

from readerwriterlock import rwlock

import config
from utils.security_utils import SecurityManager, DecryptionError

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(config.APP_ROOT, "user_settings.json")

DEFAULT_AI_PROMPT = """# A股智能分析系统提示词 (System Prompt)
# ... (Prompt content unchanged, just kept as constant in file if I don't touch it, but wait, replace needs full block if I touch surrounding)
# Wait, I am replacing the whole file content from line 5 down since I'm adding methods and changing imports? 
# No, let's target specific blocks to be safe and efficient.
"""

DEFAULT_NEWS_PROMPT = """你是拥有20年经验的金融量化分析师。任务是对新闻进行**金融交易导向分类**。

# Taxonomy (分类体系)
1. **金融核心** (直接交易标的): A股市场/港美股/期货市场/贵金属/外汇市场/宏观政策
2. **宏观经济** (基本面): 宏观数据/财政政策/国际宏观
3. **国际地缘** (避险): 冲突与博弈/能源格局
4. **行业产业** (板块): 科技/消费/能源/金融
5. **其他人文** (无影响): 纯民生/娱乐

# Rules
- 黄金/白银 -> [金融核心-贵金属] (优先)
- 铜/原油 -> [金融核心-期货市场] (优先)
- 央行/证监会 -> [金融核心-宏观政策]

# Output Format (JSON Only)
Strictly output a JSON object.
Structure: {"category_L1": "String", "category_L2": "String", "sentiment": "Positive/Neutral/Negative", "emoji": "Symbol"}

# Examples (Few-Shot)
User: 紫金矿业在南美发现巨型高品位金矿，预计储量增加50吨。
Assistant: {"category_L1": "金融核心", "category_L2": "贵金属", "sentiment": "Positive", "emoji": "🥇"}

User: 美联储宣布加息25个基点，并在会议纪要中暗示通胀顽固。
Assistant: {"category_L1": "金融核心", "category_L2": "宏观政策", "sentiment": "Negative", "emoji": "🦅"}

User: 乘联会发布数据，上月新能源车零售渗透率突破50%。
Assistant: {"category_L1": "行业产业", "category_L2": "科技制造", "sentiment": "Positive", "emoji": "🚗"}"""


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
        "auto_update_time": "16:30",
        "log_max_mb": 5,
        "log_backup_count": 5,
        "db_queue_size": 1024,
        "sync_concurrency": 3,
        "max_batch_rows": 20000,
        "sync_retry_count": 3,
        "no_proxy_domains": [],
        "ai_api_key": "",
        "ai_base_url": "https://api.deepseek.com",
        "ai_model_name": "deepseek-chat",
        "local_model_path": "",
        "local_model_timeout": 90,
        "ai_system_prompt": DEFAULT_AI_PROMPT,
        "ai_news_prompt": DEFAULT_NEWS_PROMPT,
        "ai_max_candidates": 30,
        "strategy_min_turnover": 2.0,
        "ai_concurrency": 0,
        "request_max_retries": 3,
        "tushare_timeout": 30,
        "tushare_api_rate_limit": 200,
        "locale": "zh",
        "max_io_workers": 0,
        "max_cpu_workers": 0,
        "sync_request_delay_heavy": 0.0,
        "sync_request_delay_light": 0.0,
        "news_poll_interval": 60,
        "market_data_poll_interval": 30,
        # Local AI Advanced Settings
        "local_n_threads": 4,
        "local_n_batch": 512,
        "local_n_ctx": 4096,
        "local_flash_attn": True,
        "local_n_gpu_layers": 0
    }

    @staticmethod
    def ensure_defaults():
        """Ensure default settings exist AND remove unused keys from user_settings.json"""
        try:
            current_config = ConfigHandler.load_config()
            dirty = False

            # 1. Add missing defaults
            for key, default_val in ConfigHandler.DEFAULT_CONFIG.items():
                if key not in current_config:
                    current_config[key] = default_val
                    dirty = True
                    logger.info(f"Initialized default config: {key}")

            # 2. Prune deprecated/unused keys
            valid_keys = set(ConfigHandler.DEFAULT_CONFIG.keys())
            existing_keys = list(current_config.keys())

            for key in existing_keys:
                if key not in valid_keys:
                    logger.info(f"Removing deprecated/unused config: {key}")
                    current_config.pop(key)
                    dirty = True

            # Save if any changes
            if dirty:
                ConfigHandler.save_config(current_config, replace=True)
                logger.info("Configuration (defaults & cleanup) synchronized to user_settings.json")

        except Exception as e:
            logger.error(f"Failed to ensure default config: {e}")

    @staticmethod
    def _save_json_atomically(data, path):
        """Helper: Atomic write for JSON config"""
        tmp_file = path + ".tmp"
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, path)
            return True
        except Exception as e:
            logger.error(f"Atomic save failed for {path}: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except:
                    pass
            return False

    @staticmethod
    def _decrypt_or_migrate(value, key_name, validator=None):
        """
        Helper: Decrypt value, or migrate if it's plaintext.
        Returns: Decrypted value (str) or "" if invalid.
        """
        if not value:
            return ""

        try:
            return SecurityManager.decrypt_data(value)
        except DecryptionError:
            # Failed to decrypt. Check if it's a legacy plaintext value.
            is_valid_plaintext = validator(value) if validator else True

            if is_valid_plaintext:
                logger.info(f"Migrating plaintext config '{key_name}' to encrypted storage...")
                try:
                    encrypted = SecurityManager.encrypt_data(value)
                    # Safe to call save_config here as load_config lock is already released by caller
                    ConfigHandler.save_config({key_name: encrypted})
                    return value
                except Exception as e:
                    logger.warning(f"Failed to migrate '{key_name}': {e}")
                    return value
            else:
                logger.warning(f"Config '{key_name}' contains invalid data (Undecryptable). Ignoring.")
                return ""

    @staticmethod
    def load_config():
        """Load config with Read Lock"""
        with ConfigHandler._lock.gen_rlock():
            if ConfigHandler._config_cache is not None:
                return ConfigHandler._config_cache.copy()

            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                        ConfigHandler._config_cache = json.load(f)
                        return ConfigHandler._config_cache.copy()
                except Exception:
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
                if replace:
                    current_config = config_data.copy()
                else:
                    current_config = {}
                    if ConfigHandler._config_cache is not None:
                        current_config = ConfigHandler._config_cache.copy()
                    elif os.path.exists(CONFIG_FILE):
                        try:
                            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                                current_config = json.load(f)
                        except:
                            pass
                    current_config.update(config_data)

                success = ConfigHandler._save_json_atomically(current_config, CONFIG_FILE)

                if success:
                    ConfigHandler._config_cache = current_config
                    return True
                return False
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            return False

    @staticmethod
    def _is_valid_tushare_format(token):
        """Check if string looks like valid Tushare token (Hex, len 32-56)"""
        if not token or not isinstance(token, str):
            return False
        # Tushare tokens are hex strings. Encrypted are base64.
        if len(token) > 60: return False  # Encrypted usually longer
        return True

    @staticmethod
    def get_token():
        config = ConfigHandler.load_config()
        token = config.get("ts_token", "")
        return ConfigHandler._decrypt_or_migrate(token, "ts_token", ConfigHandler._is_valid_tushare_format)

    @staticmethod
    def save_token(token):
        encrypted = SecurityManager.encrypt_data(token)
        return ConfigHandler.save_config({"ts_token": encrypted})

    @staticmethod
    def is_onboarding_complete():
        config = ConfigHandler.load_config()
        return config.get("onboarding_complete", False)

    @staticmethod
    def set_onboarding_complete(complete=True):
        return ConfigHandler.save_config({"onboarding_complete": complete})

    @staticmethod
    def is_auto_update_enabled():
        config = ConfigHandler.load_config()
        return config.get("auto_update_enabled", False)

    @staticmethod
    def get_log_level():
        config = ConfigHandler.load_config()
        return config.get("log_level", "INFO").upper()

    @staticmethod
    def set_log_level(level):
        return ConfigHandler.save_config({"log_level": level.upper()})

    @staticmethod
    def get_auto_update_time():
        config = ConfigHandler.load_config()
        return config.get("auto_update_time", "16:30")

    @staticmethod
    def get_log_max_mb():
        config = ConfigHandler.load_config()
        return config.get("log_max_mb", 5)

    @staticmethod
    def get_log_backup_count():
        config = ConfigHandler.load_config()
        return config.get("log_backup_count", 5)

    @staticmethod
    def get_db_queue_size():
        config = ConfigHandler.load_config()
        return config.get("db_queue_size", 1024)

    @staticmethod
    def set_db_queue_size(size):
        return ConfigHandler.save_config({"db_queue_size": int(size)})

    @staticmethod
    def get_sync_concurrency():
        config = ConfigHandler.load_config()
        # Default changed from 6 to 3 for stability
        return config.get("sync_concurrency", 3)

    @staticmethod
    def set_sync_concurrency(concurrency):
        return ConfigHandler.save_config({"sync_concurrency": int(concurrency)})

    @staticmethod
    def get_max_batch_rows():
        config = ConfigHandler.load_config()
        return config.get("max_batch_rows", 20000)

    @staticmethod
    def set_max_batch_rows(rows):
        return ConfigHandler.save_config({"max_batch_rows": int(rows)})

    @staticmethod
    def get_sync_retry_count():
        config = ConfigHandler.load_config()
        return config.get("sync_retry_count", 3)

    @staticmethod
    def set_sync_retry_count(count):
        return ConfigHandler.save_config({"sync_retry_count": int(count)})

    @staticmethod
    def get_no_proxy_domains():
        """
        Get domains that should BYPASS proxy (NO_PROXY).
        """
        config = ConfigHandler.load_config()
        val = config.get("no_proxy_domains", [])
        if isinstance(val, list):
            return val
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
    def get_ai_config():
        """Get all AI related clean config"""
        config = ConfigHandler.load_config()
        encrypted_key = config.get("ai_api_key", "")

        # Use helper
        api_key = ConfigHandler._decrypt_or_migrate(encrypted_key, "ai_api_key")

        return {
            "ai_api_key": api_key,
            "ai_base_url": config.get("ai_base_url", ""),
            "ai_model_name": config.get("ai_model_name", "")
        }

    @staticmethod
    def save_ai_config(api_key, base_url, model_name):
        """Save AI settings (API Key Encrypted)"""
        encrypted_key = ""
        if api_key:
            encrypted_key = SecurityManager.encrypt_data(api_key)

        return ConfigHandler.save_config({
            "ai_api_key": encrypted_key,
            "ai_base_url": base_url,
            "ai_model_name": model_name
        })

    @staticmethod
    def get_local_ai_timeout() -> int:
        """
        Get local AI inference timeout in seconds.
        Value must come from user_settings.json.
        Returns:
            int: Timeout seconds, or None if not configured (wait indefinitely)
        """
        try:
            val = ConfigHandler.get_setting('local_model_timeout')
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            # If config is corrupted/invalid, treat as not set (no default provided)
            return None

    @staticmethod
    def set_local_ai_timeout(seconds: int):
        """Set local AI inference timeout (1-3600s)"""
        # Enforce bounds to be consistent with UI
        val = max(1, min(seconds, 3600))
        ConfigHandler.save_config({'local_model_timeout': val})

    @staticmethod
    def get_local_ai_config() -> dict:
        """Get local AI configuration"""
        default_threads = max(1, os.cpu_count() - 1) if os.cpu_count() else 4
        return {
            "local_model_path": ConfigHandler.get_setting("local_model_path", ""),
            "local_timeout": ConfigHandler.get_setting("local_model_timeout", 90),
            "n_threads": ConfigHandler.get_setting("local_n_threads", default_threads),
            "n_batch": ConfigHandler.get_setting("local_n_batch", 512),
            "n_ctx": ConfigHandler.get_setting("local_n_ctx", 4096),
            "flash_attn": ConfigHandler.get_setting("local_flash_attn", True),
            "n_gpu_layers": ConfigHandler.get_setting("local_n_gpu_layers", 0),
        }

    @staticmethod
    def save_local_ai_config(model_path: str, timeout: int = 30, **kwargs):
        """Save local AI configuration"""
        # Build config dict
        cfg = {
            "local_model_path": model_path,
            "local_model_timeout": timeout
        }

        # Save optional params
        if 'n_threads' in kwargs: cfg["local_n_threads"] = kwargs['n_threads']
        if 'n_batch' in kwargs: cfg["local_n_batch"] = kwargs['n_batch']
        if 'n_ctx' in kwargs: cfg["local_n_ctx"] = kwargs['n_ctx']
        if 'flash_attn' in kwargs: cfg["local_flash_attn"] = kwargs['flash_attn']
        if 'n_gpu_layers' in kwargs: cfg["local_n_gpu_layers"] = kwargs['n_gpu_layers']

        ConfigHandler.save_config(cfg)

    @staticmethod
    def get_ai_system_prompt():
        config = ConfigHandler.load_config()
        return config.get("ai_system_prompt", DEFAULT_AI_PROMPT)

    @staticmethod
    def save_ai_system_prompt(prompt):
        return ConfigHandler.save_config({"ai_system_prompt": prompt})

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
        return config.get("ai_max_candidates", 30)

    @staticmethod
    def set_ai_max_candidates(val):
        return ConfigHandler.save_config({"ai_max_candidates": int(val)})

    @staticmethod
    def get_strategy_min_turnover():
        config = ConfigHandler.load_config()
        l_min_turnover = config.get("strategy_min_turnover", 2.0)
        return float(l_min_turnover)

    @staticmethod
    def set_strategy_min_turnover(val):
        return ConfigHandler.save_config({"strategy_min_turnover": float(val)})

    @staticmethod
    def get_ai_concurrency():
        config = ConfigHandler.load_config()
        return config.get("ai_concurrency", 0)

    @staticmethod
    def set_ai_concurrency(val):
        return ConfigHandler.save_config({"ai_concurrency": int(val)})

    # === API Robustness Parameters ===

    @staticmethod
    def get_request_max_retries():
        config = ConfigHandler.load_config()
        # Default to 3 if missing, matching DEFAULT_CONFIG
        return config.get("request_max_retries", 3)

    @staticmethod
    def get_tushare_timeout():
        config = ConfigHandler.load_config()
        # Default to 30s for Tushare specifically
        return config.get("tushare_timeout", 30)

    @staticmethod
    def set_tushare_timeout(seconds):
        return ConfigHandler.save_config({"tushare_timeout": int(seconds) if seconds is not None else None})

    @staticmethod
    def get_tushare_api_limit():
        config = ConfigHandler.load_config()
        # Default to 200 req/min (safe default)
        return config.get("tushare_api_rate_limit", 200)

    @staticmethod
    def set_tushare_api_limit(limit):
        return ConfigHandler.save_config({"tushare_api_rate_limit": int(limit)})

    # === Localization ===
    @staticmethod
    def get_locale():
        config = ConfigHandler.load_config()
        return config.get("locale", "zh")

    @staticmethod
    def set_locale(locale):
        return ConfigHandler.save_config({"locale": locale})

    # === Scheduler ===
    # ai_prediction_time removed as it was unused

    # === Thread Pool Configuration ===
    @staticmethod
    def get_max_io_workers():
        """Get max IO threads from config."""
        config = ConfigHandler.load_config()
        val = config.get("max_io_workers", 0)
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def set_max_io_workers(count):
        return ConfigHandler.save_config({"max_io_workers": int(count)})

    @staticmethod
    def get_max_cpu_workers():
        """Get max CPU threads from config."""
        config = ConfigHandler.load_config()
        val = config.get("max_cpu_workers", 0)
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def set_max_cpu_workers(count):
        return ConfigHandler.save_config({"max_cpu_workers": int(count)})

    # === Sync Rate Limiting ===
    @staticmethod
    def get_sync_request_delay(is_heavy=False):
        config = ConfigHandler.load_config()
        if is_heavy:
            return config.get("sync_request_delay_heavy", 0.0)
        return config.get("sync_request_delay_light", 0.0)

    @staticmethod
    def set_sync_request_delay(delay, is_heavy=False):
        key = "sync_request_delay_heavy" if is_heavy else "sync_request_delay_light"
        return ConfigHandler.save_config({key: float(delay)})

    # === Polling Intervals ===
    @staticmethod
    def get_news_poll_interval():
        config = ConfigHandler.load_config()
        return config.get("news_poll_interval", 60)

    @staticmethod
    def set_news_poll_interval(seconds):
        return ConfigHandler.save_config({"news_poll_interval": int(max(10, seconds))})

    @staticmethod
    def get_market_data_poll_interval():
        config = ConfigHandler.load_config()
        return config.get("market_data_poll_interval", 30)

    @staticmethod
    def set_market_data_poll_interval(seconds):
        return ConfigHandler.save_config({"market_data_poll_interval": int(max(10, seconds))})
