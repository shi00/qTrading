"""scripts/verify_embedded_pg_connect.py 双运行模式回归（技术债 P3-PR513-Script-SysPath-Inject）。

覆盖验收标准：
① 移除无条件 `sys.path.insert` 后 `python -m scripts.verify_embedded_pg_connect --help` 可运行；
② 现有使用方式 `python scripts/verify_embedded_pg_connect.py --help` 不回退。

另断言路径注入守卫本身：模块模式（`__package__ == "scripts"`）不注入仓库根；
直跑等价模式（`__package__` 为空）才注入仓库根；源码中不存在无条件注入。
两路均清除 `PYTHONPATH`，确保 import 可达性只来自各自运行模式的 `sys.path` 发现机制。
`--help` 由 argparse 在触达 sidecar 前退出，无副作用。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = ROOT / "scripts" / "verify_embedded_pg_connect.py"
ROOT_STR = str(ROOT)


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _run_help(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args, "--help"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_clean_env(),
        timeout=120,
    )


def _run_inline(code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=_clean_env(),
        timeout=120,
    )


def test_module_mode_help_from_repo_root() -> None:
    """模块模式（仓库根 cwd）不依赖 sys.path 注入即可运行。"""
    proc = _run_help(["-m", "scripts.verify_embedded_pg_connect"], cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert "usage: verify_embedded_pg_connect.py" in proc.stdout


def test_script_mode_help_from_outside_repo_root(tmp_path: Path) -> None:
    """直跑模式在仓库外 cwd 仍需注入仓库根，保证旧用法不回退。"""
    proc = _run_help([str(SCRIPT)], cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "usage: verify_embedded_pg_connect.py" in proc.stdout


def test_module_mode_does_not_inject_repo_root() -> None:
    """模块模式 `__package__ == "scripts"`，守卫必须跳过注入。"""
    code = f"import json, sys\nimport scripts.verify_embedded_pg_connect\nprint(json.dumps({ROOT_STR!r} in sys.path))\n"
    proc = _run_inline(code, ROOT)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) is False


def test_script_mode_injects_repo_root_outside_repo(tmp_path: Path) -> None:
    """直跑等价模式（`__package__` 为空）仍须注入仓库根，仓库外 cwd 下 import 才可达。"""
    code = f"import json, runpy, sys\nrunpy.run_path({str(SCRIPT)!r})\nprint(json.dumps({ROOT_STR!r} in sys.path))\n"
    proc = _run_inline(code, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) is True


def test_sys_path_insert_is_guarded_by_package_check() -> None:
    """源码中的 `sys.path.insert` 必须包在 `__package__` 判空守卫内（禁止无条件注入）。"""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def _is_sys_path_insert(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "insert"):
            return False
        owner = func.value
        if not (isinstance(owner, ast.Attribute) and owner.attr == "path"):
            return False
        return isinstance(owner.value, ast.Name) and owner.value.id == "sys"

    inserts = [node for node in ast.walk(tree) if _is_sys_path_insert(node)]
    assert inserts, "直跑兼容依赖路径注入，但源码中未找到 sys.path.insert"

    guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(isinstance(sub, ast.Name) and sub.id == "__package__" for sub in ast.walk(node.test))
    ]
    assert guards, "缺少 `__package__` 判空守卫"

    for insert in inserts:
        assert any(insert in ast.walk(guard) for guard in guards), (
            "存在未被 `__package__` 守卫包裹的 sys.path.insert（无条件注入）"
        )
