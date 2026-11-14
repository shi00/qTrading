r"""D24 双收集守卫：按实际 collection 结果拒绝新旧入口混选。

检视 5.3 合同：
- 「同一 job 只能选择迁移后的纯测试或旧兼容转发之一，不能同时收集两份后将
  重复执行计为新增覆盖」；
- 「旧节点名的参数化与标记保持」。

D9 迁移后，`tests/docs/` 持有实现，`tests/unit/` 旧文件仅保留模块级 re-export。
pytest 不按实现对象 identity 自动去重：显式同时收集新旧文件会为同一测试函数生成
两个节点名。本守卫改为**实跑 collection** 验证：

1. 新旧入口显式混选必须失败（由 `tests/docs/conftest.py` 的收集钩子拒绝）；
2. 旧转发单独收集仍能完整收集（节点数与新实现一致，转发未丢失）；
3. 默认业务收集的 `testpaths` 不含纯 docs（`tests/docs`），独立 docs 命令保留。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "tests" / "docs"
DOCS_INI = DOCS_DIR / "pytest.ini"

NEW_CANONICAL = "tests/docs/test_canonical_examples.py"
OLD_CANONICAL = "tests/unit/test_docs_canonical_examples.py"
NEW_CONSISTENCY = "tests/docs/test_consistency.py"
OLD_CONSISTENCY = "tests/unit/test_docs_consistency.py"

_COLLECTED_RE = re.compile(r"^(\d+) tests? collected", re.MULTILINE)

pytestmark = pytest.mark.docs_pure


def _collect(*paths: str) -> subprocess.CompletedProcess[str]:
    """按独立 docs 配置实跑 collection（禁插件自动加载 + 截断业务 conftest）。"""
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(DOCS_INI),
            f"--confcutdir={DOCS_DIR}",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            *paths,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _collected_count(run: subprocess.CompletedProcess[str]) -> int:
    match = _COLLECTED_RE.search(run.stdout)
    assert match is not None, f"无法解析收集数量:\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}"
    return int(match.group(1))


class TestDoubleCollectionRejected:
    """显式混选必须失败，单独收集必须完整。"""

    def test_mixed_canonical_entries_are_rejected(self):
        run = _collect(NEW_CANONICAL, OLD_CANONICAL)
        assert run.returncode != 0, f"新旧 canonical 显式混选未被拒绝:\n{run.stdout}"
        assert "双收集" in run.stdout + run.stderr, run.stdout + run.stderr

    def test_mixed_consistency_entries_are_rejected(self):
        run = _collect(NEW_CONSISTENCY, OLD_CONSISTENCY)
        assert run.returncode != 0, f"新旧 consistency 显式混选未被拒绝:\n{run.stdout}"
        assert "双收集" in run.stdout + run.stderr, run.stdout + run.stderr

    def test_legacy_canonical_forwarder_collects_all_tests(self):
        new_run = _collect(NEW_CANONICAL)
        old_run = _collect(OLD_CANONICAL)
        assert new_run.returncode == 0, new_run.stderr
        assert old_run.returncode == 0, old_run.stderr
        new_count = _collected_count(new_run)
        assert new_count > 0
        assert _collected_count(old_run) == new_count, "旧入口转发的用例数与新实现不一致（转发丢失）"

    def test_default_business_testpaths_exclude_pure_docs(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        testpaths = config["tool"]["pytest"]["ini_options"]["testpaths"]
        assert "tests" not in testpaths, "默认 testpaths 不得收回整个 tests（会重新收集 tests/docs）"
        assert "tests/docs" not in testpaths, "默认业务收集必须排除纯 docs"
        assert {"tests/unit", "tests/integration", "tests/e2e"} <= set(testpaths)
