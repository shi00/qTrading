"""PRF-02：main.py 首帧最小 import 回归测试。

验证在干净子进程中 ``import main`` 不提前加载重依赖（``app.application`` 及其拉起的
1300+ 模块），以守住「首帧空白期收敛」的性能不变量，防止日后回归把重 import 移回顶层。
通过独立子进程执行，避免当前测试进程已加载模块造成干扰；``cwd`` 显式指向仓库根，
保证子进程从 PR 源根按 ``main`` 模块解析，而非继承父进程 cwd。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# tests/unit/ 上溯 2 级即仓库根，main.py 位于仓库根
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _subprocess_env() -> dict[str, str]:
    """构造子进程环境，强制 UTF-8 IO 编码（Windows CI 默认 code page cp1252 无法编码中文）。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


_SUBPROCESS_CODE = (
    "import sys\n"
    "import main\n"
    "loaded = lambda name: name in sys.modules\n"
    "assert 'app.application' not in sys.modules, 'import main 不应加载 app.application'\n"
    "assert 'data.persistence.models' not in sys.modules, 'import main 不应加载数据层模型'\n"
    "assert 'pandas' not in sys.modules, 'import main 不应加载 pandas（横切重依赖）'\n"
    "print('OK: main import 保持首帧最小导入')\n"
)


def test_import_main_does_not_load_heavy_imports() -> None:
    """导入 main 不触发 app.application / 数据层 / pandas 加载（PRF-02）。

    顶层仅保留 multiprocessing / os / flet；app.application、日志、异常钩子、
    E2E 判定均在 ``main()`` 函数体内延迟导入。
    """
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_CODE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr or result.stdout}"
    assert "OK: main import 保持首帧最小导入" in result.stdout


def test_main_function_contains_deferred_imports() -> None:
    """``main()`` 源码包含四类延迟导入，防误移除到顶层以保首帧最小集。"""
    import main

    import inspect

    source = inspect.getsource(main.main)
    for stmt in (
        "from app.application import run",
        "from utils.app_env import is_e2e_mode",
        "from utils.exception_hooks import install_global_exception_hooks",
        "from utils.logger import setup_logging",
    ):
        assert stmt in source, f"main() 源码应含延迟导入: {stmt}"
