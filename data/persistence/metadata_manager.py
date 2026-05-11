import typing

from data.data_dictionary import COMMON_COLUMNS, TABLE_DEFINITIONS


class MetaDataManager:
    _alias_cache: dict[tuple, str] = {}

    @classmethod
    def invalidate_cache(cls):
        cls._alias_cache.clear()

    @classmethod
    def get_table_alias(cls, table_name: str) -> str:
        cache_key = ("table", table_name)
        cached = cls._alias_cache.get(cache_key)
        if cached is not None:
            return cached

        from core.i18n import I18n

        table_def = TABLE_DEFINITIONS.get(table_name)
        if table_def and "alias" in table_def:
            alias_key = table_def["alias"]
            result = f"{table_name} ({I18n.get(alias_key)})"
        else:
            result = table_name

        cls._alias_cache[cache_key] = result
        return result

    @classmethod
    def get_column_alias(cls, table_name: str | None, col_name: str) -> str:
        cache_key = ("col", table_name, col_name)
        cached = cls._alias_cache.get(cache_key)
        if cached is not None:
            return cached

        from core.i18n import I18n

        alias_key = None

        if table_name:
            table_def = TABLE_DEFINITIONS.get(table_name)
            if table_def and "columns" in table_def:
                alias_key = table_def["columns"].get(col_name)

        if not alias_key:
            alias_key = COMMON_COLUMNS.get(col_name)

        if alias_key:
            result = f"{col_name} ({I18n.get(alias_key)})"
        elif col_name.startswith("rsi_"):
            period = col_name[4:]
            result = f"RSI({period})"
        else:
            result = col_name

        cls._alias_cache[cache_key] = result
        return result

    @classmethod
    def get_raw_alias(cls, term: typing.Any, context_table: typing.Any = None):
        cache_key = ("raw", context_table, term)
        cached = cls._alias_cache.get(cache_key)
        if cached is not None:
            return cached

        from core.i18n import I18n

        alias_key = None
        if context_table:
            table_def = TABLE_DEFINITIONS.get(context_table)
            if table_def and "columns" in table_def:
                alias_key = table_def["columns"].get(term)

        if not alias_key:
            alias_key = COMMON_COLUMNS.get(term)

        if alias_key:
            result = I18n.get(alias_key)
        else:
            result = term

        cls._alias_cache[cache_key] = result
        return result
