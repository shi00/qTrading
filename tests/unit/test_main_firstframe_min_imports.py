"""PRF-02：main.py 首帧最小 import 回归测试。

验证在干净子进程中 ``import main`` 不提前加载重依赖（``app.application`` 及其拉起的
1300+ 模块），以守住「首帧空白期收敛」的性能不变量，防止日后回归把重 import 移回顶层。
通过独立子进程执行，避免当前测试进程已加载模块造成干扰；``cwd`` 显式指向仓库根，
保证子进程从 PR 源根按 ``main`` 模块解析，而非继承父进程 cwd。
"""

from __future__ import annotations

import ast
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
    "assert 'keyring' not in sys.modules, 'import main 不应加载 keyring'\n"
    "assert 'asyncpg' not in sys.modules, 'import main 不应加载 asyncpg'\n"
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


# PRF-07：main 顶层仅保留 multiprocessing/os/flet（实测约 207 模块）。上限取宽松值，
# 用于防 app.application（1300+ 模块）级重导入回流入顶层；此断言防量级回归而非精确计数。
_MAIN_MODULE_COUNT_LIMIT = 400

# PRF-07：utils.logger 属轻量启动链，不应提前拉下载重库。以运行时 sys.modules 断言守护
# PRF-01（pandas 惰性）、PRF-07（keyring 惰性 re-export）；asyncpg/httpx 的惰性（PRF-14）
# 位于 error_classifier，由下方独立门禁专门守护（utils.logger 导入链不经过该模块）。
# 用运行时断言而非 AST 扫描（字符串式 import 规避 AST 可见性，见 PRF-04）。
_UTILS_LOGGER_HEAVY_MODULES = ("pandas", "keyring")


def test_import_main_module_count_bounded() -> None:
    """import main 后 sys.modules 总量不超宽松上限（PRF-07）。"""
    code = "import sys\nimport main\nprint(len(sys.modules))\n"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr or result.stdout}"
    count = int(result.stdout.strip())
    assert count <= _MAIN_MODULE_COUNT_LIMIT, (
        f"import main 加载模块数 {count} 超过上限 {_MAIN_MODULE_COUNT_LIMIT}，"
        "疑似重导入回流（如 app.application 移回顶层）"
    )


def test_import_utils_logger_does_not_load_heavy_imports() -> None:
    """import utils.logger 不加载 pandas/keyring/asyncpg（PRF-07 运行时门禁）。"""
    code = (
        "import sys\n"
        "import utils.logger\n"
        f"heavy={list(_UTILS_LOGGER_HEAVY_MODULES)!r}\n"
        "loaded = sorted(m for m in heavy if m in sys.modules)\n"
        "print('loaded=' + repr(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr or result.stdout}"
    assert loaded_modules(result.stdout) == [], (
        f"import utils.logger 不应加载重依赖，实际加载: {loaded_modules(result.stdout)}"
    )


# PRF-14：error_classifier 的 asyncpg/httpx 惰性加载需独立门禁——utils.logger / main 导入链
# 均不经过 error_classifier，必须在干净子进程直接导入该模块并断言两库未加载，才能守住 PRF-14。
# 若不设此门禁，asyncpg/httpx 被移回 error_classifier 模块级也不会有任何 sys.modules 守护告警
# （检视发现的 PRF-07 覆盖缺口）。
_ERROR_CLASSIFIER_HEAVY_MODULES = ("asyncpg", "httpx")


def test_import_error_classifier_does_not_load_asyncpg_httpx() -> None:
    """import utils.error_classifier 不加载 asyncpg/httpx（PRF-14 门禁，补 PRF-07 覆盖缺口）。"""
    code = (
        "import sys\n"
        "import utils.error_classifier\n"
        f"heavy={list(_ERROR_CLASSIFIER_HEAVY_MODULES)!r}\n"
        "loaded = sorted(m for m in heavy if m in sys.modules)\n"
        "print('loaded=' + repr(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr or result.stdout}"
    assert loaded_modules(result.stdout) == [], (
        f"import utils.error_classifier 不应加载 asyncpg/httpx，实际加载: {loaded_modules(result.stdout)}"
    )


def loaded_modules(stdout: str) -> list[str]:
    """解析子进程打印的已加载重依赖列表。"""
    for line in stdout.splitlines():
        if line.startswith("loaded="):
            text = line[len("loaded=") :].strip()
            return ast.literal_eval(text)
    return ["<无法解析子进程输出>"]


def test_config_frozen_uses_meipass_for_resource_root_and_tiktoken_cache(tmp_path: Path) -> None:
    """B02: PyInstaller 6+ onedir 模式下 sys.frozen=True 且 sys._MEIPASS 指向 _internal，
    config.RESOURCE_ROOT 应优先解析为 sys._MEIPASS，并正确注入 TIKTOKEN_CACHE_DIR。
    """
    tiktoken_dir = tmp_path / "data" / "tiktoken_cache"
    tiktoken_dir.mkdir(parents=True, exist_ok=True)
    code = (
        "import os, sys\n"
        "os.environ.pop('TIKTOKEN_CACHE_DIR', None)\n"
        "os.environ.pop('CUSTOM_TIKTOKEN_CACHE_DIR', None)\n"
        "sys.frozen = True\n"
        f"sys._MEIPASS = {str(tmp_path)!r}\n"
        "import config\n"
        "print('RESOURCE_ROOT=' + config.RESOURCE_ROOT)\n"
        "print('TIKTOKEN=' + os.environ.get('TIKTOKEN_CACHE_DIR', ''))\n"
        "print('CUSTOM_TIKTOKEN=' + os.environ.get('CUSTOM_TIKTOKEN_CACHE_DIR', ''))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr or result.stdout}"
    lines = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert lines.get("RESOURCE_ROOT") == str(tmp_path), (
        f"frozen 模式下 RESOURCE_ROOT 应等于 sys._MEIPASS ({tmp_path})，实际: {lines.get('RESOURCE_ROOT')}"
    )
    assert lines.get("TIKTOKEN") == str(tiktoken_dir), (
        f"TIKTOKEN_CACHE_DIR 应指向 _MEIPASS/data/tiktoken_cache，实际: {lines.get('TIKTOKEN')}"
    )
    assert lines.get("CUSTOM_TIKTOKEN") == str(tiktoken_dir), (
        f"CUSTOM_TIKTOKEN_CACHE_DIR 应指向 _MEIPASS/data/tiktoken_cache，实际: {lines.get('CUSTOM_TIKTOKEN')}"
    )


def test_main_smoke_test_flag_exits_zero() -> None:
    """B04: python main.py --smoke-test 应完成核心包与静态资源自检、输出特征字符串并以退出码 0 退出。"""
    result = subprocess.run(
        [sys.executable, "main.py", "--smoke-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        cwd=str(_REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, f"--smoke-test 退出码应为 0，实际: {result.returncode}\nstderr: {result.stderr}"
    assert "AStockScreener smoke check OK" in result.stdout, (
        f"--smoke-test 应输出 'AStockScreener smoke check OK'，实际 stdout: {result.stdout}"
    )
