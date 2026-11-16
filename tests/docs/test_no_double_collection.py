r"""D24 双收集守卫：按实际 collection 结果拒绝新旧入口混选。

检视 5.3 合同：
- 「同一 job 只能选择迁移后的纯测试或旧兼容转发之一，不能同时收集两份后将
  重复执行计为新增覆盖」；
- 「旧节点名的参数化与标记保持」。

D9 迁移后，`tests/docs/` 持有实现，`tests/unit/` 旧文件仅保留模块级 re-export。
pytest 不按实现对象 identity 自动去重：显式同时收集新旧文件会为同一测试函数生成
两个节点名。本守卫改为**实跑 collection** 验证：

1. 新旧入口显式混选必须失败（由 `tests/docs/conftest.py` 的收集钩子拒绝）；
2. 旧转发单独收集仍能完整收集（节点数与新实现一致，转发未丢失，参数化 ID 不变）；
3. 默认业务收集的 `testpaths` 不含纯 docs（`tests/docs`），独立 docs 命令保留；
4. 纯收集子进程不隐式加载兄弟目录（`tests/unit`）conftest，也不执行任何业务导入
   —— `--confcutdir` 只截断被收集路径的祖先，兄弟目录 conftest 仍会被发现（R11）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from test_isolation import BUSINESS_TOP_LEVELS

ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "tests" / "docs"
DOCS_INI = DOCS_DIR / "pytest.ini"

NEW_CANONICAL = "tests/docs/test_canonical_examples.py"
OLD_CANONICAL = "tests/unit/test_docs_canonical_examples.py"
NEW_CONSISTENCY = "tests/docs/test_consistency.py"
OLD_CONSISTENCY = "tests/unit/test_docs_consistency.py"

_COLLECTED_RE = re.compile(r"^(\d+) tests? collected", re.MULTILINE)
_PROBE_PREFIX = "R11_PROBE "
_PROBE_PLUGIN = "tests.docs.fixtures.collection_probe"

pytestmark = pytest.mark.docs_pure


def _collect(*paths: str, probe: bool = False) -> subprocess.CompletedProcess[str]:
    """按独立 docs 配置实跑 collection（禁插件自动加载 + 禁全部隐式 conftest）。

    ``--confcutdir`` 只截断被收集路径的*祖先*：收集 ``tests/unit/`` 下的旧入口时，
    兄弟目录的 ``tests/unit/conftest.py`` 仍会被隐式加载并初始化业务夹具（R11）。
    因此追加 ``--noconftest`` 关闭全部隐式 conftest 发现，再用 ``-p tests.docs.conftest``
    显式且仅显式加载 docs 自身 conftest（提供 sys.path 注入与双收集拒绝钩子），
    业务套件的单例隔离职责（`tests/conftest.py` / `tests/unit/conftest.py`，R7）保持不变。
    """
    # 强制 UTF-8：Windows 子进程默认编码会把「双收集」写成 \\uXXXX 转义，导致诊断断言误判
    env = dict(
        os.environ,
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    plugin_args = ["-p", "tests.docs.conftest"]
    if probe:
        plugin_args += ["-p", _PROBE_PLUGIN]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(DOCS_INI),
            f"--confcutdir={DOCS_DIR}",
            "--noconftest",
            *plugin_args,
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            *paths,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        # 子进程按 PYTHONUTF8 输出 UTF-8；父端也必须显式按 UTF-8 解码，
        # 否则 Windows 会用 cp1252 解码中文诊断并让 stderr 变为 None
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def _collected_count(run: subprocess.CompletedProcess[str]) -> int:
    match = _COLLECTED_RE.search(run.stdout)
    assert match is not None, f"无法解析收集数量:\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}"
    return int(match.group(1))


def _probe_payload(run: subprocess.CompletedProcess[str]) -> dict[str, object]:
    for line in run.stdout.splitlines():
        if line.startswith(_PROBE_PREFIX):
            return json.loads(line[len(_PROBE_PREFIX) :])
    raise AssertionError(f"子进程未输出观测探针数据:\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}")


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
        # 参数化节点 ID 必须跨入口保留（不能被转发改写成不同 ID）
        parametrized_id = "test_bridge_example_uses_real_screener_api[mvvm.md]"
        assert parametrized_id in new_run.stdout, new_run.stdout
        assert parametrized_id in old_run.stdout, old_run.stdout

    def test_pure_collection_loads_no_sibling_conftest(self):
        """旧入口纯收集不得隐式加载兄弟目录 conftest，也不得执行任何业务导入。

        `--confcutdir` 只截断祖先目录，收集 `tests/unit/` 旧入口时仍会隐式加载
        `tests/unit/conftest.py`（R11）。本用例按子进程实际 ``sys.modules`` 断言：
        仅显式加载的 docs conftest 存在，业务顶层模块为零。
        """
        run = _collect(OLD_CANONICAL, probe=True)
        assert run.returncode == 0, run.stdout + run.stderr
        payload = _probe_payload(run)
        conftests = payload["conftests"]
        assert isinstance(conftests, dict)
        assert "tests.docs.conftest" in conftests, f"显式 docs conftest 未加载: {conftests}"
        assert "tests.unit.conftest" not in conftests, f"纯收集隐式加载了 unit conftest: {conftests}"
        assert "tests.conftest" not in conftests, f"纯收集隐式加载了业务根 conftest: {conftests}"
        outside = sorted(name for name, path in conftests.items() if not Path(path).is_relative_to(DOCS_DIR))
        assert outside == [], f"纯收集加载了 tests/docs 之外的 conftest: {outside}"
        modules = payload["modules"]
        assert isinstance(modules, list)
        # `tests` 为空包标记（tests/__init__.py 无内容），由 `-p tests.docs.conftest`
        # 按模块名导入必然引入，非业务代码；其余业务顶层模块必须为零。
        business = sorted(set(modules) & (BUSINESS_TOP_LEVELS - {"tests"}))
        assert business == [], f"纯收集执行了业务导入: {business}"

    def test_default_business_testpaths_exclude_pure_docs(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        testpaths = config["tool"]["pytest"]["ini_options"]["testpaths"]
        assert "tests" not in testpaths, "默认 testpaths 不得收回整个 tests（会重新收集 tests/docs）"
        assert "tests/docs" not in testpaths, "默认业务收集必须排除纯 docs"
        assert {"tests/unit", "tests/integration", "tests/e2e"} <= set(testpaths)
