"""纯文档测试夹具（D3，检视 5.3 / AC14）。

本文件与 tests/docs 下全部测试只依赖 docs 组（markdown-it-py / github-slugger /
pyyaml / pytest）与标准库，禁止任何业务模块导入（隔离负例由 test_isolation.py
静态 + 运行时双重守护）。sys.path 注入仅指向 scripts/ 以导入解析模块
docs_markdown 与生成模块 docs_generated（D12 生成保护控制测试共用夹具）。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))

import pytest

import docs_generated as dg  # noqa: E402
import docs_markdown as dm

pytestmark = pytest.mark.docs_pure


# ---------------------------------------------------------------------------
# D12 生成保护控制测试共用夹具（L01 唯一作者源 / L07 生成保护边界）
# ---------------------------------------------------------------------------

_LIMITS_SCHEMA = dg.SourceSchema(
    path="sources/limits.yml",
    fmt="yaml",
    item_key="limits",
    required_fields=frozenset({"id", "value"}),
    known_fields=frozenset({"id", "value"}),
    identity_field="id",
)


def _render_limits(validated: dict[str, object], root: Path) -> list[str]:
    """渲染合成限额条目：作者源 → 派生块的唯一映射（控制测试最小作者源）。"""
    del root
    items = validated["sources/limits.yml"]
    if not isinstance(items, list):
        raise dg.SourceFormatError("渲染输入非条目列表")
    lines: list[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise dg.SourceFormatError("渲染条目非映射")
        lines.append(f"- {entry.get('id')}: {entry.get('value')}")
    return lines


_LIMITS_REGISTRY: tuple[dg.BlockSpec, ...] = (
    dg.BlockSpec(
        block_id="limits",
        target="targets/derived.md",
        sources=(_LIMITS_SCHEMA,),
        render=_render_limits,
    ),
)


@pytest.fixture()
def generated_block_repo(tmp_path: Path) -> tuple[Path, tuple[dg.BlockSpec, ...]]:
    """最小生成仓库：作者源 sources/limits.yml + 目标 targets/derived.md 空生成块。"""
    root = tmp_path / "docrepo"
    (root / "sources").mkdir(parents=True)
    (root / "targets").mkdir()
    (root / "sources" / "limits.yml").write_text(
        "limits:\n  - id: max_rows\n    value: 500\n",
        encoding="utf-8",
    )
    (root / "targets" / "derived.md").write_text(
        "# 派生目标\n\n<!-- generated:limits -->\n<!-- /generated:limits -->\n",
        encoding="utf-8",
    )
    return root, _LIMITS_REGISTRY


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
