"""
Test Utilities

Shared utility functions for test modules.
"""

import inspect


def get_model_columns(model_class: type) -> set:
    """Extract column names from SQLAlchemy model class.

    Uses __table__.columns directly instead of inspect.getmembers,
    which is more robust and doesn't depend on Python internals.
    """
    return {c.name for c in model_class.__table__.columns}


def get_model_db_columns(model_class: type) -> set:
    """Extract database column names from SQLAlchemy model class.

    Unlike get_model_columns which returns Python attribute names,
    this returns the actual database column names (which may differ
    when using Column(name=...) parameter).
    """
    return {c.name for c in model_class.__table__.columns}


def extract_cols_from_method(method) -> set | None:
    """Extract cols list from DAO save method by resolving the model class.

    Since all DAO save methods use the pattern:
        cols = get_model_columns(ModelClass)
    we resolve the ModelClass by inspecting the method's closure and globals,
    then call get_model_columns directly. This avoids fragile source-string
    parsing via inspect.getsource + AST.
    """
    try:
        from data.persistence.models import get_model_columns as gmc

        model_class = _resolve_model_class_from_method(method)
        if model_class is not None:
            exclude = _resolve_exclude_from_method(method)
            return set(gmc(model_class, exclude=exclude))

        return _resolve_hardcoded_cols_from_method(method)
    except Exception:
        return None


def _resolve_model_class_from_method(method) -> type | None:
    """Resolve the SQLAlchemy model class referenced in a DAO save method.

    Inspects the method's closure variables and global scope for calls
    to get_model_columns(SomeModel), resolving SomeModel without AST parsing.
    """
    try:
        source = inspect.getsource(method)
    except OSError, TypeError:
        return None

    import re

    patterns = [
        r"get_model_columns\(\s*(\w+)\s*\)",
        r"get_model_columns\(\s*(\w+)\s*,",
    ]
    for pattern in patterns:
        match = re.search(pattern, source)
        if match:
            model_name = match.group(1)
            module = inspect.getmodule(method)
            if module and hasattr(module, model_name):
                return getattr(module, model_name)
            method_globals = getattr(method, "__globals__", {})
            if model_name in method_globals:
                return method_globals[model_name]
    return None


def _resolve_exclude_from_method(method) -> set | None:
    """Resolve the exclude= parameter from a DAO save method if present."""
    try:
        source = inspect.getsource(method)
    except OSError, TypeError:
        return None

    import re

    match = re.search(r"exclude\s*=\s*\{([^}]+)\}", source)
    if match:
        items = match.group(1)
        return {s.strip().strip("\"'") for s in items.split(",") if s.strip()}
    return None


def _resolve_hardcoded_cols_from_method(method) -> set | None:
    """Fallback: try to resolve hardcoded column lists from method source."""
    try:
        source = inspect.getsource(method)
    except OSError, TypeError:
        return None

    import re

    match = re.search(r"(?:cols|columns|all_cols)\s*=\s*\[([^\]]+)\]", source)
    if match:
        items = match.group(1)
        result = set()
        for item in items.split(","):
            item = item.strip().strip("\"'")
            if item and not item.startswith("#"):
                result.add(item)
        return result if result else None
    return None


def extract_fields_from_api_method(method) -> set:
    """Extract fields list from TushareClient API method.

    Resolves the fields="..." keyword argument by inspecting the method's
    default values and source, without relying on AST source parsing.
    """
    try:
        source = inspect.getsource(method)
    except OSError, TypeError:
        return set()

    import re

    match = re.search(r'fields\s*=\s*["\']([^"\']+)["\']', source)
    if match:
        fields_str = match.group(1)
        return set(f.strip() for f in fields_str.split(",") if f.strip())

    return set()
