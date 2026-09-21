import threading
import typing

from data.data_dictionary import COMMON_COLUMNS, TABLE_DEFINITIONS, column_i18n_key, columns_of
from utils.singleton_registry import register_singleton


@register_singleton
class MetaDataManager:
    _instance = None
    _alias_cache: dict[tuple, str] = {}
    _lock = threading.Lock()

    @classmethod
    def _reset_singleton(cls):
        with cls._lock:
            cls._instance = None
            cls._alias_cache.clear()

    @classmethod
    def invalidate_cache(cls):
        with cls._lock:
            cls._alias_cache.clear()

    @classmethod
    def preload_aliases(cls):
        """B-P1-9: Preload all table and column aliases at startup to avoid
        blocking the event loop during UI rendering.

        OSS-01：列集合经 ``columns_of`` 从 ORM 派生，不再遍历硬编码 columns 段；
        COMMON_COLUMNS 公共列仍须预载（table=None 的全局查询缓存）。
        """
        for table_name in TABLE_DEFINITIONS:
            cls.get_table_alias(table_name)
            for col_name in columns_of(table_name):
                cls.get_column_alias(table_name, col_name)
        for col_name in COMMON_COLUMNS:
            cls.get_column_alias(None, col_name)

    @classmethod
    def get_table_alias(cls, table_name: str) -> str:
        from core.i18n import I18n

        locale = I18n.current_locale()
        cache_key = ("table", table_name, locale)
        with cls._lock:
            cached = cls._alias_cache.get(cache_key)
            if cached is not None:
                return cached

        table_def = TABLE_DEFINITIONS.get(table_name)
        if table_def and "alias" in table_def:
            alias_key = table_def["alias"]
            result = f"{table_name} ({I18n.get(alias_key)})"
        else:
            result = table_name

        with cls._lock:
            cls._alias_cache[cache_key] = result
        return result

    @classmethod
    def get_column_alias(cls, table_name: str | None, col_name: str) -> str:
        from core.i18n import I18n

        locale = I18n.current_locale()
        cache_key = ("col", table_name, col_name, locale)
        with cls._lock:
            cached = cls._alias_cache.get(cache_key)
            if cached is not None:
                return cached

        alias_key = None

        if table_name:
            alias_key = column_i18n_key(table_name, col_name)

        if not alias_key:
            alias_key = column_i18n_key(None, col_name)

        if alias_key:
            result = f"{col_name} ({I18n.get(alias_key)})"
        elif col_name.startswith("rsi_"):
            period = col_name[4:]
            result = f"RSI({period})"
        else:
            result = col_name

        with cls._lock:
            cls._alias_cache[cache_key] = result
        return result

    @classmethod
    def get_raw_alias(cls, term: typing.Any, context_table: typing.Any = None):
        from core.i18n import I18n

        locale = I18n.current_locale()
        is_hashable = isinstance(term, (str, int, float, tuple)) or term is None
        term_key = (
            term
            if is_hashable
            else (
                tuple(term)
                if isinstance(term, list)
                else (tuple(sorted(term.items())) if isinstance(term, dict) else str(term))
            )
        )
        cache_key = ("raw", context_table, term_key, locale)

        with cls._lock:
            cached = cls._alias_cache.get(cache_key)
            if cached is not None:
                return cached

        alias_key = None
        if is_hashable and isinstance(term, str) and context_table:
            alias_key = column_i18n_key(context_table, term)

        if is_hashable and isinstance(term, str) and not alias_key:
            alias_key = column_i18n_key(None, term)

        if alias_key:
            result = I18n.get(alias_key)
        else:
            result = term

        with cls._lock:
            cls._alias_cache[cache_key] = result
        return result
