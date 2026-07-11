import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import flet as ft

logger = logging.getLogger(__name__)


def _is_debug_mode() -> bool:
    """Check if DEBUG mode is enabled via environment variable."""
    return os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


LOCALE_MAP = {
    "zh": "zh_CN",
    "zh_CN": "zh_CN",
    "en": "en_US",
    "en_US": "en_US",
}

SUPPORTED_LOCALES = ["zh_CN", "en_US"]
DEFAULT_LOCALE = "zh_CN"

LOCALE_NAMES: dict[str, str] = {
    "zh_CN": "简体中文",
    "en_US": "English",
}


@ft.observable
@dataclass
class I18nState(ft.Observable):
    """i18n Observable 状态源。

    声明式组件通过 ``ft.use_state(I18n.get_observable_state)`` 订阅，
    locale 变更时 ``I18n.set_locale`` 更新 ``state.locale`` 触发 Observable 通知，
    框架自动重渲染订阅该 state 的组件。

    显式继承 ``ft.Observable`` 使 pyright 能识别 ``subscribe`` 等方法；
    ``@ft.observable`` 装饰器检测到 ``Observable in __mro__`` 后直接返回原类（no-op）。

    旧接口 ``I18n.subscribe``/``_listeners`` 保留供命令式 View 使用（阶段 4 删除）。
    """

    locale: str = DEFAULT_LOCALE


class I18n:
    """
    Internationalization support.
    Manages locale state and provides translated strings.
    Dynamically loads translations from JSON files in locales/ directory.

    A-P0-2 fix: Moved from ui/i18n.py to core/i18n.py to eliminate
    reverse dependency (strategies/utils should not import from ui layer).
    ui/i18n.py re-exports this class for backward compatibility.
    """

    _locale: str = DEFAULT_LOCALE
    _listeners: list | None = None
    _initialized: bool = False
    _strings_cache: dict = {}
    _missing_keys: set = set()
    _locales_dir: Path | None = None
    _state: I18nState | None = None

    @classmethod
    def get_observable_state(cls) -> I18nState:
        """获取 i18n Observable 状态源单例。

        声明式组件通过 ``ft.use_state(I18n.get_observable_state)`` 订阅，
        locale 变更时自动触发重渲染。
        """
        if cls._state is None:
            cls._state = I18nState()
        return cls._state

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
            logger.warning("[I18n] Locale file not found: %s", file_path)
            return {}

        try:
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error("[I18n] Failed to parse locale file %s: %s", file_path, e)
            return {}
        except Exception as e:
            logger.error("[I18n] Failed to load locale file %s: %s", file_path, e)
            return {}

    @classmethod
    def _get_strings(cls, locale: str) -> dict:
        if locale not in cls._strings_cache:
            cls._strings_cache[locale] = cls._load_locale_file(locale)
        return cls._strings_cache[locale]

    @classmethod
    def initialize(cls, locale: str | None = None):
        """Initialize locale. Safe to call multiple times."""
        if cls._initialized:
            return

        config_locale = locale or DEFAULT_LOCALE
        normalized_locale = LOCALE_MAP.get(config_locale, config_locale)

        if normalized_locale in SUPPORTED_LOCALES:
            cls._locale = normalized_locale
        else:
            logger.warning("[I18n] Unsupported locale '%s', falling back to %s", config_locale, DEFAULT_LOCALE)
            cls._locale = DEFAULT_LOCALE

        cls._initialized = True
        # 同步 Observable state（声明式组件自动重渲染）。
        # 若 _state 尚未被组件订阅，get_observable_state 会 lazy 创建实例，
        # 后续 use_state(I18n.get_observable_state) 复用同一实例拿到正确 locale。
        cls.get_observable_state().locale = cls._locale
        logger.info("[I18n] Initialized with locale: %s", cls._locale)

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
            if _is_debug_mode():
                raise RuntimeError(
                    "[I18n] Not initialized in DEBUG mode. Call I18n.initialize(locale) explicitly at startup."
                )
            logger.warning(
                "[I18n] Auto-initializing with default locale. Call I18n.initialize(locale) explicitly at startup."
            )
            cls.initialize()

        target_locale = locale if locale else cls._locale
        normalized_locale = LOCALE_MAP.get(target_locale, target_locale)
        locale_map = cls._get_strings(normalized_locale)

        if key not in locale_map:
            if key not in cls._missing_keys:
                if default is not None:
                    logger.debug(
                        "[I18n] Using default fallback '%s' for missing key: '%s' (Locale: %s)",
                        default,
                        key,
                        normalized_locale,
                    )
                else:
                    logger.warning(
                        "[I18n] Missing translation for key: '%s' (Locale: %s)",
                        key,
                        normalized_locale,
                    )
                cls._missing_keys.add(key)
            return default if default is not None else key

        template = locale_map[key]

        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError as e:
                logger.warning("[I18n] Missing format arg for '%s': %s", key, e)
                return template
        return template

    @classmethod
    def set_locale(cls, locale: str):
        """Change locale and notify listeners.

        同步更新 Observable state（声明式组件自动重渲染）；
        旧接口 ``_listeners`` 回调保留供命令式 View 使用（阶段 4 删除）。
        """
        normalized_locale = LOCALE_MAP.get(locale, locale)

        if normalized_locale in SUPPORTED_LOCALES:
            cls._locale = normalized_locale
            cls._initialized = True
            # 同步 Observable state：state.locale 赋值触发 __setattr__ → _notify
            # → 订阅该 state 的声明式组件自动重渲染。
            cls.get_observable_state().locale = cls._locale
            logger.info("[I18n] Locale changed to: %s", cls._locale)

            # 旧接口：命令式 View 通过 subscribe 注册的回调（阶段 4 删除）
            if cls._listeners:
                for listener in cls._listeners:
                    try:
                        listener()
                    except Exception as e:
                        logger.error("[I18n] Listener error: %s", e)
        else:
            logger.warning("[I18n] Attempted to set unsupported locale: %s", locale)

    @classmethod
    def subscribe(cls, callback, *, sync_immediately: bool = True):
        """Subscribe to locale changes.

        Args:
            callback: Zero-arg callable (bound method or closure) invoked on
                locale change. Project convention: all callbacks must take no
                arguments other than the bound ``self``.
            sync_immediately: If True, fire ``callback`` once immediately upon
                subscription so that late-mounted components sync to the current
                global locale instead of rendering with the stale locale captured
                in their ``__init__``. Defaults to True. Pass False explicitly
                for the rare case where the caller must not refresh yet.

        Returns:
            The callback itself (can be used as subscription_id for unsubscribe)
        """
        if cls._listeners is None:
            cls._listeners = []
        if callback not in cls._listeners:
            cls._listeners.append(callback)
            if sync_immediately:
                try:
                    callback()
                except Exception as e:
                    logger.error("[I18n] Immediate sync error: %s", e, exc_info=True)
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
    def get_language_label(cls) -> str:
        """Return bilingual language label like '语言 / Language'.

        Format: '<current_locale_native_name> / <other_locale_native_name>'
        If only one locale is supported, returns its native name.
        """
        if not cls._initialized:
            logger.warning(
                "[I18n] Auto-initializing with default locale. Call I18n.initialize(locale) explicitly at startup."
            )
            cls.initialize()
        current_name = LOCALE_NAMES.get(cls._locale, cls._locale)
        other_names = [LOCALE_NAMES[loc] for loc in SUPPORTED_LOCALES if loc != cls._locale and loc in LOCALE_NAMES]
        if other_names:
            return f"{current_name} / {' / '.join(other_names)}"
        return current_name

    @classmethod
    def get_language_options(cls) -> list[tuple[str, str]]:
        """Return list of (locale_code, native_name) tuples for dropdown options."""
        return [(loc, LOCALE_NAMES.get(loc, loc)) for loc in SUPPORTED_LOCALES]

    @classmethod
    def reload_locale(cls):
        """Force reload current locale from file (useful for development)."""
        if cls._locale in cls._strings_cache:
            del cls._strings_cache[cls._locale]
        cls._get_strings(cls._locale)
        logger.info("[I18n] Reloaded locale: %s", cls._locale)
