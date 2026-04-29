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
    def get(cls, key: str, default: str | None = None, locale: str | None = None, **kwargs) -> str:
        """
        Get translated string by key with optional formatting.

        Args:
            key: Translation key
            default: Optional fallback string if key is not found
            locale: Optional locale to use instead of current locale
            **kwargs: Optional format arguments (e.g., error="...", count=5)

        Returns:
            Translated and formatted string, or key itself if not found.

        Example:
            I18n.get("screener_done", count=10)  # Returns "筛选完成，共 10 只股票"
            I18n.get("app_title", locale="en_US")  # Returns "A-Share Intelligent Screener"
        """
        if not cls._initialized:
            cls.initialize()

        target_locale = locale if locale else cls._locale
        normalized_locale = LOCALE_MAP.get(target_locale, target_locale)
        locale_map = cls._get_strings(normalized_locale)

        if key not in locale_map:
            if key not in cls._missing_keys:
                if default is not None:
                    logger.debug(
                        f"[I18n] Using default fallback '{default}' for missing key: '{key}' (Locale: {normalized_locale})",
                    )
                else:
                    logger.warning(
                        f"[I18n] Missing translation for key: '{key}' (Locale: {normalized_locale})",
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


# S5-1 fix: classify_error moved to utils/error_classifier.py
# Re-export for backward compatibility


_STRATEGY_NAME_MAP = {
    "AI_Auto_Nightly": "strategy_ai_nightly_name",
    "AI 深度精选 (Beta)": "strategy_ai_active_name",
    "AI Deep Dive (Beta)": "strategy_ai_active_name",
    "价值投资": "strategy_value_name",
    "Value Investing": "strategy_value_name",
    "高成长策略": "strategy_growth_name",
    "高股息策略": "strategy_dividend_name",
    "技术突破": "strategy_tech_breakout_name",
    "北向持股": "strategy_northbound_holding_name",
    "北向净流入": "strategy_northbound_flow_name",
    "超跌反弹": "strategy_oversold_name",
    "龙虎榜机构": "strategy_institutional_name",
    "筹码集中 (暂不可用)": "strategy_chips_name",
    "大宗交易": "strategy_block_trade_name",
    "现金流优质": "strategy_cashflow_name",
    "大盘低估": "strategy_large_pe_name",
}


def translate_strategy_name(name: str | None) -> str | None:
    """
    Translate strategy name to localized version.

    Args:
        name: Strategy name (either an identifier like 'AI_Auto_Nightly' or already localized)

    Returns:
        Localized strategy name
    """
    if not name:
        return name

    if name in _STRATEGY_NAME_MAP:
        return I18n.get(_STRATEGY_NAME_MAP[name])

    return name
