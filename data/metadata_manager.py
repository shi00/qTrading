from typing import Optional
from data.data_dictionary import COMMON_COLUMNS, TABLE_DEFINITIONS
from utils.config_handler import ConfigHandler

class MetaDataManager:
    """
    Manages access to business metadata and aliases.
    Decouples data definitions from UI translations.
    """
    
    @staticmethod
    def _is_chinese() -> bool:
        """Check if current locale is Chinese."""
        return ConfigHandler.get_locale() == "zh"

    @classmethod
    def get_table_alias(cls, table_name: str) -> str:
        """
        Get alias for a table.
        Returns: "Table (Alias)" if alias exists and locale is zh, else "Table"
        """
        if not cls._is_chinese():
            return table_name
            
        table_def = TABLE_DEFINITIONS.get(table_name)
        if table_def and "alias" in table_def:
            return f"{table_name} ({table_def['alias']})"
        
        return table_name

    @classmethod
    def get_column_alias(cls, table_name: Optional[str], col_name: str) -> str:
        """
        Get alias for a column with context awareness.
        Priority:
        1. Table-specific alias
        2. Common alias
        3. Original name
        
        Returns: "Col (Alias)" if alias exists and locale is zh, else "Col"
        """
        if not cls._is_chinese():
            return col_name
            
        alias = None
        
        # 1. Look up table specific override
        if table_name:
            table_def = TABLE_DEFINITIONS.get(table_name)
            if table_def and "columns" in table_def:
                alias = table_def["columns"].get(col_name)
            
        # 2. Look up common definition
        if not alias:
            alias = COMMON_COLUMNS.get(col_name)
            
        if alias:
            return f"{col_name} ({alias})"
            
        return col_name

    @classmethod
    def get_raw_alias(cls, term, context_table=None):
        """
        Get just the Chinese alias string without formatting.
        Useful for tooltips or other UI elements.
        """
        if context_table:
            table_def = TABLE_DEFINITIONS.get(context_table)
            if table_def and "columns" in table_def:
                alias = table_def["columns"].get(term)
                if alias: return alias
                
        return COMMON_COLUMNS.get(term, term)
