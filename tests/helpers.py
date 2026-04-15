"""
Test Utilities

Shared utility functions for test modules.
"""

import inspect


def get_model_columns(model_class: type) -> set:
    """Extract column names from SQLAlchemy model class."""
    columns = set()
    for name, attr in inspect.getmembers(model_class):
        if hasattr(attr, "property") and hasattr(attr.property, "columns"):
            columns.add(name)
    return columns


def get_model_db_columns(model_class: type) -> set:
    """Extract database column names from SQLAlchemy model class.

    Unlike get_model_columns which returns Python attribute names,
    this returns the actual database column names (which may differ
    when using Column(name=...) parameter).
    """
    columns = set()
    for _name, attr in inspect.getmembers(model_class):
        if hasattr(attr, "property") and hasattr(attr.property, "columns"):
            for col in attr.property.columns:
                columns.add(col.name)
    return columns


def extract_cols_from_method(method) -> set | None:
    """Extract cols list from save method source code (static analysis).

    Supports three patterns:
    1. Static list: cols = ["col1", "col2", ...]
    2. Dynamic call: cols = get_model_columns(ModelClass)
    3. Dynamic call with exclude: all_cols = get_model_columns(ModelClass, exclude={...})
    """
    import re

    source = inspect.getsource(method)

    pattern = r"(?:cols|columns|all_cols)\s*=\s*\[([^\]]+)\]"
    match = re.search(pattern, source, re.DOTALL)

    if match:
        cols_str = match.group(1)
        cols = set()

        for item in cols_str.split(","):
            item = item.strip().strip('"').strip("'")
            if item and not item.startswith("#"):
                cols.add(item)

        return cols if cols else None

    pattern = (
        r"(?:cols|columns|all_cols)\s*=\s*get_model_columns\s*\(\s*(\w+)\s*(?:,\s*exclude\s*=\s*\{([^}]*)\})?\s*\)"
    )
    match = re.search(pattern, source)

    if match:
        model_name = match.group(1)
        exclude_str = match.group(2)
        from data.persistence.models import get_model_columns as gmc
        import data.persistence.models as models

        model_class = getattr(models, model_name, None)
        if model_class:
            exclude = set()
            if exclude_str:
                for item in exclude_str.split(","):
                    item = item.strip().strip('"').strip("'")
                    if item:
                        exclude.add(item)
            return set(gmc(model_class, exclude=exclude or None))

    return None


def extract_fields_from_api_method(method) -> set:
    """Extract fields list from API method source code (static analysis)."""
    import re

    source = inspect.getsource(method)

    pattern = r'fields\s*=\s*["\']([^"\']+)["\']'
    match = re.search(pattern, source)

    if not match:
        return set()

    fields_str = match.group(1)
    return set(f.strip() for f in fields_str.split(",") if f.strip())
