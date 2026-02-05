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


# I will assume DEFAULT_AI_PROMPT is unchanged and skip re-pasting it in thought trace.
# But for replace_file_content, I need to be precise. 
# The previous `view_file` showed the whole file. 
# I can target `class ConfigHandler` and replace it entirely OR replace methods.
# Replacing the whole class is safer to ensure order and new methods are there.

class ConfigHandler:
    _config_cache = None
    _last_load_time = 0
    _lock = rwlock.RWLockFair()

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

            # If specifically validated as false (e.g. looks like corrupted ciphertext), reject it.
            # If no validator, strictly speaking we can't distinguish "bad ciphertext" from "random plaintext string".
            # heuristic: if len > 100 and validator is None? No.

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
                    # If load fails, return empty. Atomic write prevents partial reads.
                    return {}
            return {}

    @staticmethod
    def save_config(config_data):
        """Save config with Write Lock and Atomic Write"""
        try:
            with ConfigHandler._lock.gen_wlock():

                # Read current state (disk or cache)
                current_config = {}
                if ConfigHandler._config_cache is not None:
                    # CRITICAL: Must copy! Otherwise we modify cache in-place before confirmation.
                    current_config = ConfigHandler._config_cache.copy()
                elif os.path.exists(CONFIG_FILE):
                    try:
                        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                            current_config = json.load(f)
                    except:
                        pass

                # Update
                current_config.update(config_data)

                # Atomic Write
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
        # Note: if _decrypt_or_migrate returns "", api_key is empty.
        # Fallback to simple logic: if it was short, maybe plain text? 
        # But _decrypt_or_migrate handles migration if validator passes.
        # For AI Key, we didn't pass a validator, so it assumes IsValidPlaintext=True.
        # This is fine, as random base64 errors will be caught by DecryptionError.

        return {
            "ai_api_key": api_key,
            "ai_base_url": config.get("ai_base_url", "https://api.deepseek.com"),
            "ai_model_name": config.get("ai_model_name", "deepseek-chat")
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
    def get_ai_system_prompt():
        config = ConfigHandler.load_config()
        return config.get("ai_system_prompt", DEFAULT_AI_PROMPT)

    @staticmethod
    def save_ai_system_prompt(prompt):
        return ConfigHandler.save_config({"ai_system_prompt": prompt})

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
        return config.get("strategy_min_turnover", 2.0)

    @staticmethod
    def set_strategy_min_turnover(val):
        return ConfigHandler.save_config({"strategy_min_turnover": float(val)})

    @staticmethod
    def get_ai_concurrency():
        config = ConfigHandler.load_config()
        return config.get("ai_concurrency", 5)

    @staticmethod
    def set_ai_concurrency(val):
        return ConfigHandler.save_config({"ai_concurrency": int(val)})

    # === API Robustness Parameters ===

    @staticmethod
    def get_request_max_retries():
        config = ConfigHandler.load_config()
        return config.get("request_max_retries", 3)

    @staticmethod
    def get_request_timeout():
        config = ConfigHandler.load_config()
        return config.get("request_timeout", 30)

    @staticmethod
    def get_tushare_timeout():
        config = ConfigHandler.load_config()
        return config.get("tushare_timeout", None)

    @staticmethod
    def set_tushare_timeout(seconds):
        return ConfigHandler.save_config({"tushare_timeout": int(seconds) if seconds is not None else None})

    @staticmethod
    def get_tushare_api_limit():
        config = ConfigHandler.load_config()
        return config.get("tushare_api_rate_limit", None)

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
    @staticmethod
    def get_ai_prediction_time():
        config = ConfigHandler.load_config()
        return config.get("ai_prediction_time", "20:30")

    @staticmethod
    def set_ai_prediction_time(time_str):
        return ConfigHandler.save_config({"ai_prediction_time": time_str})

    # === Thread Pool Configuration ===
    @staticmethod
    def get_max_io_workers():
        """Get max IO threads from config. Defaults to 32."""
        config = ConfigHandler.load_config()
        val = config.get("max_io_workers", 32)
        try:
            return int(val) if val is not None and int(val) > 0 else 32
        except (ValueError, TypeError):
            return 32

    @staticmethod
    def set_max_io_workers(count):
        return ConfigHandler.save_config({"max_io_workers": int(count)})

    @staticmethod
    def get_max_cpu_workers():
        """Get max CPU threads from config. Defaults to CPU count."""
        config = ConfigHandler.load_config()
        val = config.get("max_cpu_workers", None)
        default_cpu = os.cpu_count() or 1
        try:
            return int(val) if val is not None and int(val) > 0 else default_cpu
        except (ValueError, TypeError):
            return default_cpu

    @staticmethod
    def set_max_cpu_workers(count):
        return ConfigHandler.save_config({"max_cpu_workers": int(count)})
