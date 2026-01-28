import json
import os
import config
from utils.security_utils import SecurityManager

CONFIG_FILE = os.path.join(config.APP_ROOT, "user_settings.json")

class ConfigHandler:
    @staticmethod
    def load_config():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @staticmethod
    def save_config(config_data):
        try:
            current_config = ConfigHandler.load_config()
            current_config.update(config_data)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(current_config, f, indent=4)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False


    @staticmethod
    def _is_valid_tushare_format(token):
        """
        Check if a string looks like a valid Tushare token.
        Tushare tokens are typically 32-56 character hex strings.
        Encrypted tokens are Base64 and usually much longer.
        """
        if not token or not isinstance(token, str):
            return False
        # Crude but effective check: Tushare tokens are hex, encrypted are base64 (with potential +/ symbols)
        # And length check. 
        if len(token) > 60: # Encrypted tokens are usually longer due to overhead
            return False
        # Optional: check for hex characters only?
        # Let's keep it loose to avoid breaking unusual but valid tokens, 
        # but strict enough to reject obvious ciphertext.
        return True

    @staticmethod
    def get_token():
        config = ConfigHandler.load_config()
        token = config.get("ts_token", "")
        
        if not token:
            return ""
            
        # Try to decrypt
        try:
            return SecurityManager.decrypt_data(token)
        except Exception:
            # Failed to decrypt. 
            # This happens if:
            # 1. It's a legacy plain text token (we should encrypt it).
            # 2. It's an encrypted token but the key is wrong/missing (we should discard it).
            
            if ConfigHandler._is_valid_tushare_format(token):
                # Looks like a valid plain text token, let's migrate it
                try:
                    SecurityManager.get_key() # Ensure key exists
                    encrypted = SecurityManager.encrypt_data(token)
                    ConfigHandler.save_config({"ts_token": encrypted})
                    return token
                except Exception as e:
                    print(f"Error migrating token: {e}")
                    return token
            else:
                # Does NOT look like a valid token. Likely an encrypted blob we can't read.
                # Return empty so UI prompts user to re-enter.
                print("Warning: Stored token could not be decrypted and does not appear to be plaintext. Ignoring.")
                return ""

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
        """Get configured log level (default: INFO)"""
        config = ConfigHandler.load_config()
        return config.get("log_level", "INFO").upper()

    @staticmethod
    def set_log_level(level):
        """Set log level (DEBUG, INFO, WARNING, ERROR)"""
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
        return config.get("sync_concurrency", 5)

    @staticmethod
    def set_sync_concurrency(concurrency):
        return ConfigHandler.save_config({"sync_concurrency": int(concurrency)})
