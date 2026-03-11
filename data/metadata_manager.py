from typing import Optional

from data.data_dictionary import COMMON_COLUMNS, TABLE_DEFINITIONS


class MetaDataManager:
    """
    Manages access to business metadata and aliases.
    Decouples data definitions from UI translations.
    """

    # _is_chinese removed as I18n handles locale internally

    @classmethod
    def get_table_alias(cls, table_name: str) -> str:
        """
        Get alias for a table using I18n.
        """
        from ui.i18n import I18n  # Import here to avoid circular dependency

        table_def = TABLE_DEFINITIONS.get(table_name)
        if table_def and "alias" in table_def:
            alias_key = table_def["alias"]
            return f"{table_name} ({I18n.get(alias_key)})"

        return table_name

    @classmethod
    def get_column_alias(cls, table_name: Optional[str], col_name: str) -> str:
        """
        Get alias for a column with context awareness using I18n.
        """
        from ui.i18n import I18n  # Import here to avoid circular dependency

        alias_key = None

        # 1. Look up table specific override
        if table_name:
            table_def = TABLE_DEFINITIONS.get(table_name)
            if table_def and "columns" in table_def:
                alias_key = table_def["columns"].get(col_name)

        # 2. Look up common definition
        if not alias_key:
            alias_key = COMMON_COLUMNS.get(col_name)

        if alias_key:
            # Translate the key
            return f"{col_name} ({I18n.get(alias_key)})"

        # 3. Dynamic prefix matching for technical indicators (e.g., rsi_14 → "RSI(14)")
        if col_name.startswith("rsi_"):
            period = col_name[4:]  # Extract the number after 'rsi_'
            return f"RSI({period})"

        return col_name

    @classmethod
    def get_raw_alias(cls, term, context_table=None):
        """
        Get just the translated alias string without formatting.
        Useful for tooltips or other UI elements.
        """
        from ui.i18n import I18n  # Import here to avoid circular dependency

        alias_key = None
        if context_table:
            table_def = TABLE_DEFINITIONS.get(context_table)
            if table_def and "columns" in table_def:
                alias_key = table_def["columns"].get(term)

        if not alias_key:
            alias_key = COMMON_COLUMNS.get(term)

        if alias_key:
            return I18n.get(alias_key)

        return term
