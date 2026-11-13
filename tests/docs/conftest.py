"""纯文档测试夹具（D3，检视 5.3 / AC14）。

本文件与 tests/docs 下全部测试只依赖 docs 组（markdown-it-py / github-slugger /
pyyaml / pytest）与标准库，禁止任何业务模块导入（隔离负例由 test_isolation.py
静态 + 运行时双重守护）。sys.path 注入仅指向 scripts/ 以导入解析模块 docs_markdown。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pytest

import docs_markdown as dm

pytestmark = pytest.mark.docs_pure


@pytest.fixture()
def write_doc(tmp_path: Path) -> Callable[[str, str], Path]:
    """在临时目录写一份 UTF-8 文档，返回其路径（不触碰仓库 docs/ 正文）。"""

    def _write(name: str, text: str) -> Path:
        target = tmp_path / name
        target.write_text(text, encoding="utf-8")
        return target

    return _write


@pytest.fixture()
def view_factory():
    """直接从文本构造解析视图（不经过文件读取边界）。"""

    return dm.parse_text
