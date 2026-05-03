"""Persistence layer - 持久化层"""


def __getattr__(name):
    if name == "DatabaseManager":
        from data.persistence.database_manager import DatabaseManager

        return DatabaseManager
    if name == "Base":
        from data.persistence.models import Base

        return Base
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DatabaseManager", "Base"]
