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


def extract_cols_from_method(method) -> set | None:
    """Extract cols list from save method source code (static analysis)."""
    import re

    source = inspect.getsource(method)

    pattern = r"(?:cols|columns)\s*=\s*\[([^\]]+)\]"
    match = re.search(pattern, source, re.DOTALL)

    if not match:
        return None

    cols_str = match.group(1)
    cols = set()

    for item in cols_str.split(","):
        item = item.strip().strip('"').strip("'")
        if item and not item.startswith("#"):
            cols.add(item)

    return cols if cols else None


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
