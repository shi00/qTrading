"""MissingMaskingVisitor（R21 缺失伪装检测器）遍历完整性与误报边界单测。

覆盖 scripts/prototype_business_redlines.py 的 MissingMaskingVisitor 在 fillna/fill
形态上的下钻行为：同一 `fillna(0)` 位于 return、赋值右侧、外层调用参数三种形态时，
均应命中且仅命中一条同 kind（masking_fillna_lowconf）记录；None 哨兵、布尔列填充
与字段无关赋值不得误报。
"""

import ast
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.meta]

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from prototype_business_redlines import MissingMaskingVisitor  # noqa: E402 - sys.path 注入后导入


def _hits(src: str) -> list[dict[str, object]]:
    """解析源码并返回 MissingMaskingVisitor 的全部命中记录。"""
    visitor = MissingMaskingVisitor()
    visitor.cur_file = "fake.py"
    visitor.visit(ast.parse(src))
    return visitor.hits


class TestMissingMaskingTraversal:
    """fillna 位于不同语法位置时必须被下钻检出，且同一节点只记一次。"""

    @pytest.mark.parametrize(
        ("name", "src"),
        [
            ("return", "def f(df):\n    return df['score'].fillna(0)\n"),
            ("assign", "def f(df):\n    result = df['score'].fillna(0)\n    return result\n"),
            ("nested_call", "def f(df):\n    return outer(inner.fillna(0))\n"),
        ],
    )
    def test_fillna_hit_in_each_position(self, name: str, src: str) -> None:
        """return / 赋值右侧 / 嵌套调用三种形态各命中一条 masking_fillna_lowconf。"""
        hits = _hits(src)
        assert len(hits) == 1, f"{name}: {hits}"
        assert hits[0]["kind"] == "masking_fillna_lowconf"

    def test_fillna_on_assigned_score_hit_once(self) -> None:
        """score = series.fillna(0)：命中 fillna 记录且不因目标字段重复记录。"""
        hits = _hits("def f(df):\n    score = df['score'].fillna(0)\n")
        assert len(hits) == 1, hits
        assert hits[0]["kind"] == "masking_fillna_lowconf"

    def test_chained_fillna_two_nodes(self) -> None:
        """链式调用中每个 fillna 节点各记一次，互不吞并。"""
        hits = _hits("def f(df):\n    return df['score'].fillna(0).fill(50)\n")
        assert len(hits) == 2, hits
        assert [h["kind"] for h in hits] == ["masking_fillna_lowconf", "masking_fillna_lowconf"]


class TestMissingMaskingNoFalsePositive:
    """合法写法与无关内容不得误报。"""

    def test_fillna_none_not_flagged(self) -> None:
        """fillna(None) 是哨兵缺失表示，不应命中。"""
        assert _hits("def f(df):\n    return df['score'].fillna(None)\n") == []

    def test_fillna_false_not_flagged(self) -> None:
        """fillna(False) 是布尔列填充（False == 0），不属于 score=0 伪装。"""
        assert _hits("def f(df):\n    return df['is_st'].fillna(False)\n") == []

    def test_non_business_field_assign_not_flagged(self) -> None:
        """非业务语义字段赋 0 不受 R21 约束。"""
        assert _hits("def f(row):\n    row['retry_count'] = 0\n") == []

    def test_comment_and_docstring_not_flagged(self) -> None:
        """注释与文档字符串中的 fillna(0) 文本不是调用节点。"""
        src = (
            "def f(df):\n"
            '    """不要用 df["score"].fillna(0) 伪装缺失。"""\n'
            "    # score = 0 同样属于缺失伪装\n"
            "    return df\n"
        )
        assert _hits(src) == []
