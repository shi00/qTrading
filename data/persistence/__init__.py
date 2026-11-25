"""Persistence layer - 持久化层"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.persistence.data_explorer_query_client import DataExplorerQueryClient
    from data.persistence.models import Base

# Subpackages that should be importable via attribute access on this module.
# Needed because some tests re-create this module via ``del sys.modules[...]``,
# which leaves the subpackage attribute unset on the new module object.
_SUBPACKAGES = frozenset({"daos", "embedded_postgres"})

#: 需在父包属性访问时惰性加载的普通子模块（同上：模块被 ``del sys.modules[...]`` 重建后
#: 子模块属性会丢失，字符串路径 patch 会因 ``getattr`` 失败而报错）。
_SUBMODULES = frozenset({"instance_lock"})


def __getattr__(name):
    if name == "DataExplorerQueryClient":
        from data.persistence.data_explorer_query_client import DataExplorerQueryClient

        return DataExplorerQueryClient
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
    if name in _SUBPACKAGES or name in _SUBMODULES:
        mod = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Base", "DataExplorerQueryClient"]
