"""Persistence layer - 持久化层"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.persistence.database_manager import DatabaseManager
    from data.persistence.models import Base

# Subpackages that should be importable via attribute access on this module.
# Needed because some tests re-create this module via ``del sys.modules[...]``,
# which leaves the subpackage attribute unset on the new module object.
_SUBPACKAGES = frozenset({"daos"})


def __getattr__(name):
    if name == "DatabaseManager":
        from data.persistence.database_manager import DatabaseManager

        return DatabaseManager
    if name == "Base":
        from data.persistence.models import Base

        return Base
    if name == "models":
        globals()["models"] = None
        try:
            mod = importlib.import_module("data.persistence.models")
            globals()["models"] = mod
            return mod
        except Exception:
            del globals()["models"]
            raise
    if name in _SUBPACKAGES:
        mod = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DatabaseManager", "Base"]
