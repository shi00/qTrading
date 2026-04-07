import json
import logging
from pathlib import Path

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

LOCALE_MAP = {
    "zh": "zh_CN",
    "zh_CN": "zh_CN",
    "en": "en_US",
    "en_US": "en_US",
}

SUPPORTED_LOCALES = ["zh_CN", "en_US"]
DEFAULT_LOCALE = "zh_CN"


class I18n:
    """
    Internationalization support.
    Manages locale state and provides translated strings.
    Dynamically loads translations from JSON files in locales/ directory.
    """

    _locale: str = DEFAULT_LOCALE
    _listeners: list | None = None
    _initialized: bool = False
    _strings_cache: dict = {}
    _missing_keys: set = set()
    _locales_dir: Path | None = None

    @classmethod
    def _get_locales_dir(cls) -> Path:
        if cls._locales_dir is None:
            cls._locales_dir = Path(__file__).parent.parent / "locales"
        return cls._locales_dir

    @classmethod
    def _load_locale_file(cls, locale: str) -> dict:
        locale_folder = LOCALE_MAP.get(locale, locale)
        file_path = cls._get_locales_dir() / locale_folder / "strings.json"

        if not file_path.exists():
            logger.warning(f"[I18n] Locale file not found: {file_path}")
            return {}

        try:
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"[I18n] Failed to parse locale file {file_path}: {e}")
            return {}
        except Exception as e:
            logger.error(f"[I18n] Failed to load locale file {file_path}: {e}")
            return {}

    @classmethod
    def _get_strings(cls, locale: str) -> dict:
        if locale not in cls._strings_cache:
            cls._strings_cache[locale] = cls._load_locale_file(locale)
        return cls._strings_cache[locale]

    @classmethod
    def initialize(cls):
        """Initialize locale from config. Safe to call multiple times."""
        if cls._initialized:
            return

        config_locale = ConfigHandler.get_locale()
        normalized_locale = LOCALE_MAP.get(config_locale, config_locale)

        if normalized_locale in SUPPORTED_LOCALES:
            cls._locale = normalized_locale
        else:
            logger.warning(f"[I18n] Unsupported locale '{config_locale}', falling back to {DEFAULT_LOCALE}")
            cls._locale = DEFAULT_LOCALE

        cls._initialized = True
        logger.info(f"[I18n] Initialized with locale: {cls._locale}")

    @classmethod
    def get(cls, key: str, default: str | None = None, **kwargs) -> str:
        """
        Get translated string by key with optional formatting.

        Args:
            key: Translation key
            default: Optional fallback string if key is not found
            **kwargs: Optional format arguments (e.g., error="...", count=5)

        Returns:
            Translated and formatted string, or key itself if not found.

        Example:
            I18n.get("screener_done", count=10)  # Returns "筛选完成，共 10 只股票"
        """
        if not cls._initialized:
            cls.initialize()

        locale_map = cls._get_strings(cls._locale)

        if key not in locale_map:
            if key not in cls._missing_keys:
                if default is not None:
                    logger.debug(
                        f"[I18n] Using default fallback '{default}' for missing key: '{key}' (Locale: {cls._locale})",
                    )
                else:
                    logger.warning(
                        f"[I18n] Missing translation for key: '{key}' (Locale: {cls._locale})",
                    )
                cls._missing_keys.add(key)
            return default if default is not None else key

        template = locale_map[key]

        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError as e:
                logger.warning(f"[I18n] Missing format arg for '{key}': {e}")
                return template
        return template

    @classmethod
    def set_locale(cls, locale: str):
        """Change locale and notify listeners"""
        normalized_locale = LOCALE_MAP.get(locale, locale)

        if normalized_locale in SUPPORTED_LOCALES:
            cls._locale = normalized_locale
            ConfigHandler.set_locale(locale)
            logger.info(f"[I18n] Locale changed to: {cls._locale}")

            if cls._listeners:
                for listener in cls._listeners:
                    try:
                        listener()
                    except Exception as e:
                        logger.error(f"[I18n] Listener error: {e}")
        else:
            logger.warning(f"[I18n] Attempted to set unsupported locale: {locale}")

    @classmethod
    def subscribe(cls, callback):
        """Subscribe to locale changes.

        Returns:
            The callback itself (can be used as subscription_id for unsubscribe)
        """
        if cls._listeners is None:
            cls._listeners = []
        if callback not in cls._listeners:
            cls._listeners.append(callback)
        return callback

    @classmethod
    def unsubscribe(cls, callback_or_id):
        """
        Unsubscribe from locale changes.
        Call this in view's dispose/cleanup to prevent memory leaks.

        Args:
            callback_or_id: The callback function or subscription_id returned by subscribe()
        """
        if cls._listeners and callback_or_id in cls._listeners:
            cls._listeners.remove(callback_or_id)

    @classmethod
    def current_locale(cls) -> str:
        return cls._locale

    @classmethod
    def get_supported_locales(cls) -> list:
        """Return list of supported locale codes."""
        return SUPPORTED_LOCALES.copy()

    @classmethod
    def reload_locale(cls):
        """Force reload current locale from file (useful for development)."""
        if cls._locale in cls._strings_cache:
            del cls._strings_cache[cls._locale]
        cls._get_strings(cls._locale)
        logger.info(f"[I18n] Reloaded locale: {cls._locale}")


def classify_error(e: Exception, context: str = "general") -> dict:
    """
    Classify exceptions into user-friendly i18n messages.

    Args:
        e: The exception to classify
        context: Error context - "token", "db", "llm", "chart", "general"

    Returns:
        {"code": str, "message": str} where message is translated i18n text
    """
    error_str = str(e).lower()
    error_type = type(e).__name__

    if context == "token":
        if "token" in error_str and ("invalid" in error_str or "not set" in error_str):
            return {"code": "invalid", "message": I18n.get("wizard_err_token_invalid")}
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message": I18n.get("wizard_err_token_timeout")}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message": I18n.get("wizard_err_token_network")}
        if "抱歉" in error_str or "每分钟" in error_str or "限制" in error_str:
            return {"code": "server", "message": I18n.get("wizard_err_token_server")}
        return {"code": "unknown", "message": I18n.get("wizard_err_token_unknown")}

    if context == "llm":
        if "401" in error_str or "unauthorized" in error_str or "invalid api key" in error_str:
            return {"code": "auth_failed", "message": I18n.get("llm_err_auth_failed")}
        if "403" in error_str or "forbidden" in error_str:
            return {"code": "forbidden", "message": I18n.get("llm_err_forbidden")}
        if "404" in error_str or "not found" in error_str:
            return {"code": "not_found", "message": I18n.get("llm_err_not_found")}
        if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
            return {"code": "rate_limit", "message": I18n.get("llm_err_rate_limit")}
        if "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str:
            return {"code": "server_error", "message": I18n.get("llm_err_server")}
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message": I18n.get("llm_err_timeout")}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message": I18n.get("llm_err_network")}
        if "dns" in error_str or "getaddrinfo" in error_str:
            return {"code": "dns", "message": I18n.get("llm_err_dns")}
        if "ssl" in error_str or "certificate" in error_str:
            return {"code": "ssl", "message": I18n.get("llm_err_ssl")}
        if "model" in error_str and ("not found" in error_str or "unsupported" in error_str):
            return {
                "code": "model_not_found",
                "message": I18n.get("llm_err_model_not_found"),
            }
        return {"code": "unknown", "message": I18n.get("llm_err_unknown")}

    if context == "db":
        if error_type == "ValueError":
            return {
                "code": "format",
                "message": I18n.get("db_err_format").format(error=error_str),
            }
        if "password" in error_str or "authentication" in error_str:
            return {"code": "auth", "message": I18n.get("db_err_auth")}
        if "timeout" in error_str:
            return {"code": "timeout", "message": I18n.get("db_err_timeout")}
        if "refused" in error_str or "connect" in error_str:
            return {"code": "refused", "message": I18n.get("db_err_refused")}
        return {"code": "unknown", "message": I18n.get("db_err_unknown")}

    if context == "chart":
        if "timeout" in error_str or "timed out" in error_str:
            return {"code": "timeout", "message": I18n.get("detail_err_chart_timeout")}
        if "connection" in error_str or "network" in error_str or "connect" in error_str:
            return {"code": "network", "message": I18n.get("detail_err_chart_network")}
        if "data" in error_str or "empty" in error_str or "null" in error_str:
            return {"code": "data", "message": I18n.get("detail_err_chart_data")}
        return {"code": "unknown", "message": I18n.get("detail_err_chart_unknown")}

    # JSON parsing errors
    if error_type == "JSONDecodeError":
        return {"code": "json_parse", "message": I18n.get("common_err_json_parse")}

    # File system errors
    if error_type in ("FileNotFoundError", "FileExistsError"):
        return {
            "code": "file_not_found",
            "message": I18n.get("common_err_file_not_found"),
        }
    if error_type == "PermissionError":
        return {"code": "permission", "message": I18n.get("common_err_permission")}
    if error_type == "OSError" and ("disk" in error_str or "space" in error_str):
        return {"code": "disk_space", "message": I18n.get("common_err_disk_space")}

    # General errors
    if "timeout" in error_str or "timed out" in error_str:
        return {"code": "timeout", "message": I18n.get("common_err_timeout")}
    if "connection" in error_str or "network" in error_str or "connect" in error_str:
        return {"code": "network", "message": I18n.get("common_err_network")}
    if "500" in error_str or "502" in error_str or "503" in error_str:
        return {"code": "server", "message": I18n.get("common_err_server")}

    return {"code": "unknown", "message": I18n.get("common_err_unknown")}
