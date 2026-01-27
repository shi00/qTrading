import json
import os
import config

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
    def get_token():
        config = ConfigHandler.load_config()
        return config.get("ts_token", "")

    @staticmethod
    def save_token(token):
        return ConfigHandler.save_config({"ts_token": token})

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
    def get_auto_update_time():
        config = ConfigHandler.load_config()
        return config.get("auto_update_time", "16:30")
