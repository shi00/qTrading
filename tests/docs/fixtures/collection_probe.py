"""纯收集子进程观测探针（R11）：报告实际加载的 conftest 与顶层模块。

仅供 ``tests/docs/test_no_double_collection.py`` 以 ``-p tests.docs.fixtures.collection_probe``
显式加载：默认 docs 运行不会导入本模块，也不改变 collection 结果。子进程把
``R11_PROBE <json>`` 单行写入 stdout，父进程据此断言「未隐式加载兄弟目录 conftest、
未执行任何业务导入」。
"""

from __future__ import annotations

import json
import sys

import pytest


def pytest_collection_finish(session: pytest.Session) -> None:
    del session
    conftests = {
        name: str(getattr(module, "__file__", ""))
        for name, module in list(sys.modules.items())
        if name == "conftest" or name.endswith(".conftest")
    }
    modules = sorted({name.split(".")[0] for name in list(sys.modules)})
    print("R11_PROBE " + json.dumps({"conftests": conftests, "modules": modules}), flush=True)
