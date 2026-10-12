"""strategies/ 新闻情绪字段展示边界守护测试（ADR-0008，Plans N3-7）。

docs/patterns/ai-service.md §5「仅展示边界」：打分输出（label/confidence/score/probs）
仅用于新闻流着色与展示面板，**选股/策略模块不得读取**（ADR-0008「输出仅用于展示，
并通过测试验证无执行路径」）。本测试以 AST 静态扫描 strategies/ 全部 .py 源码，
断言不存在对新闻情绪字段 ``sentiment`` / ``sentiment_score`` 的任何读取/写入执行
路径，且不得导入打分服务 ``services.news_sentiment_scorer``。

范式与 tests/unit/ui/test_view_business_import_boundary.py 同源（AST NodeVisitor +
负向测试证明守护可捕获绕过形式），检测形式：

1. 下标读取：      ``row["sentiment"]`` / ``df["sentiment_score"]``
2. 属性读取：      ``row.sentiment``
3. 裸变量引用：    ``sentiment = ...``（含 import 绑定名、函数体内引用）
4. dict.get：      ``item.get("sentiment")``
5. Polars 列操作： ``pl.col("sentiment")`` / ``df.select("sentiment_score")``
6. 关键字传参：    ``save_market_news(sentiment=...)``
7. dict 字面量键： ``df.rename({"sentiment": ...})``
8. 直接 import：   ``from services.news_sentiment_scorer import NewsSentimentScorer``
9. importlib：     ``importlib.import_module("services.news_sentiment_scorer")``

豁免：``TYPE_CHECKING`` 块内 import（仅类型注解，无运行时依赖）。

不误报的既有现场（鲁棒性由负向测试锁定）：
- ``strategies/oversold_strategy.py`` 的 ``SENTIMENT_INDICES`` / ``SENTIMENT_INDEX_NAMES``
  大写情绪指数常量（精确匹配小写字段名，不命中）；
- ``strategies/market.py`` 英文日志文案 "market sentiment insufficient"（长文本不精确
  等于字段名，且注释/文案不构成 AST 读取语义）。

范围边界：仅扫 strategies/（含 backtest/ 子目录）。data/ 的 DAO/models 是
``market_news.sentiment`` 列的合法数据通道、ui/ 是合法展示消费方（N3-5 已接线），
均不在本边界内。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
STRATEGIES_DIR = PROJECT_ROOT / "strategies"

# 守护的新闻情绪字段名（market_news 列 + 打分输出落库字段；精确匹配，见 Plans N3-7 DoD；
# N5-2 扩充 sentiment_conf / sentiment_model 三打分列随聚合落地后同步纳入守护）
GUARDED_FIELDS: frozenset[str] = frozenset({"sentiment", "sentiment_score", "sentiment_conf", "sentiment_model"})

# 打分服务模块（策略层不得导入：输出仅展示边界，ADR-0008 / ai-service.md §5）
SCORER_MODULE = "services.news_sentiment_scorer"

# 数据访问语义的方法名白名单：调用名（Attribute.attr 或 Name.id）命中且第一个位置参数
# 为守护字段名的字符串常量时违规。仅收 Polars/dict 的数据访问方法，避免误伤
# logger.info("...") 等任意位置参数恰好为字段名的非数据访问场景。
FIELD_ACCESS_METHODS: frozenset[str] = frozenset(
    {
        "col",
        "first",
        "last",
        "exclude",
        "get",
        "pluck",
        "select",
        "drop",
        "take",
        "gather",
        "item",
        "group_by",
        "sort",
        "join",
    }
)


def _is_guarded_str(node: ast.expr) -> bool:
    """节点是否为值精确等于守护字段名的字符串常量。"""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in GUARDED_FIELDS


def _is_type_checking_test(test: ast.expr) -> bool:
    """检测 if 的 test 是否为 TYPE_CHECKING 引用（直接 Name 或 typing.TYPE_CHECKING 属性）。"""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _SentimentBoundaryVisitor(ast.NodeVisitor):
    """AST 扫描器：检测 strategies/ 源码中新闻情绪字段的读取/写入执行路径。

    覆盖模块 docstring 列出的 9 类形式；跳过 TYPE_CHECKING 块内 import。
    """

    def __init__(self) -> None:
        self._in_type_checking = False
        self.violations: list[str] = []

    def _record(self, lineno: int, evidence: str) -> None:
        self.violations.append(f"line {lineno}: {evidence}")

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            old = self._in_type_checking
            self._in_type_checking = True
            for stmt in node.body:
                self.visit(stmt)
            # else 分支是运行时路径，须在恢复标记后再遍历（不得豁免）
            self._in_type_checking = old
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # 形式 1：row["sentiment"] / df["sentiment_score"]
        if _is_guarded_str(node.slice):
            assert isinstance(node.slice, ast.Constant)
            self._record(node.lineno, f'subscript read of field "{node.slice.value}"')
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # 形式 2：row.sentiment / row.sentiment_score
        if node.attr in GUARDED_FIELDS:
            self._record(node.lineno, f'attribute read of field "{node.attr}"')
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # 形式 3：裸变量引用（赋值目标 / 函数体内引用 / import 绑定名）
        if node.id in GUARDED_FIELDS:
            self._record(node.lineno, f'bare identifier "{node.id}"')
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        call_name: str | None = None
        if isinstance(func, ast.Attribute):
            call_name = func.attr
        elif isinstance(func, ast.Name):
            call_name = func.id
        # 形式 4/5：item.get("sentiment") / pl.col("sentiment") / df.select("sentiment_score")
        if call_name in FIELD_ACCESS_METHODS and node.args and _is_guarded_str(node.args[0]):
            assert isinstance(node.args[0], ast.Constant)
            self._record(node.lineno, f'{call_name}("{node.args[0].value}") field access')
        # 形式 9：importlib.import_module(...) 间接导入打分服务
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value == SCORER_MODULE
        ):
            self._record(node.lineno, f'importlib.import_module("{SCORER_MODULE}")')
        elif isinstance(func, ast.Name) and func.id == "import_module" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and arg0.value == SCORER_MODULE:
                self._record(node.lineno, f'import_module("{SCORER_MODULE}")')
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        # 形式 6：save_market_news(sentiment=...)（**kwargs 展开的 arg 为 None，跳过）
        if node.arg in GUARDED_FIELDS:
            self._record(node.lineno, f'keyword argument "{node.arg}"')
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        # 形式 7：df.rename({"sentiment": ...}) 等以守护字段名为键的 dict 字面量
        for key in node.keys:
            if key is not None and _is_guarded_str(key):
                assert isinstance(key, ast.Constant)
                self._record(node.lineno, f'dict literal key "{key.value}"')
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # 形式 8：from services.news_sentiment_scorer import X / from services import news_sentiment_scorer
        # 及相对 import 形态（from . / from .. import news_sentiment_scorer，module 为空）
        if self._in_type_checking:
            return
        module = node.module or ""
        if module == SCORER_MODULE:
            self._record(node.lineno, f"from {SCORER_MODULE} import ...")
        for alias in node.names:
            if f"{module}.{alias.name}" == SCORER_MODULE or (
                module in ("services", "") and alias.name == "news_sentiment_scorer"
            ):
                self._record(node.lineno, f"from {module} import {alias.name}")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._in_type_checking:
            return
        for alias in node.names:
            if alias.name == SCORER_MODULE or alias.name.startswith(SCORER_MODULE + "."):
                self._record(node.lineno, f"import {alias.name}")
        self.generic_visit(node)


def _strategy_python_files() -> list[Path]:
    """收集 strategies/ 全部 .py 文件（递归，含 backtest/ 子目录与 __init__.py）。"""
    return sorted(STRATEGIES_DIR.rglob("*.py"))


def _scan_source(source: str) -> list[str]:
    """辅助：扫描源码字符串，返回违规证据列表。"""
    tree = ast.parse(source)
    visitor = _SentimentBoundaryVisitor()
    visitor.visit(tree)
    return visitor.violations


# ============================================================================
# 主测试
# ============================================================================


def test_no_strategy_reads_sentiment_fields() -> None:
    """strategies/ 全部源码不得存在新闻情绪字段的读取/写入路径，不得导入打分服务（ADR-0008）。"""
    violations: list[str] = []
    for py_file in _strategy_python_files():
        rel_path = py_file.relative_to(PROJECT_ROOT).as_posix()
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            violations.append(f"{rel_path}: SyntaxError while parsing: {exc}")
            continue
        visitor = _SentimentBoundaryVisitor()
        visitor.visit(tree)
        for evidence in visitor.violations:
            violations.append(f"{rel_path}: {evidence}")

    assert not violations, (
        "strategies/ 出现新闻情绪字段（sentiment / sentiment_score）读取/写入路径或打分服务导入，"
        "违反展示边界（ADR-0008：AI 输出仅用于展示，选股/策略模块不得读取；"
        "见 docs/patterns/ai-service.md §5）。\n"
        "打分输出仅允许经 news 流着色与展示面板消费；策略输入数据须与情绪字段解耦。\n" + "\n".join(violations)
    )


# ============================================================================
# 负向测试：证明 AST 守护可捕获全部绕过形式
# ============================================================================


def test_scanner_catches_subscript_read() -> None:
    """形式 1：下标读取必须被捕获。"""
    violations = _scan_source('value = row["sentiment"]\n')
    assert any("subscript read" in v for v in violations), f"未能捕获下标读取: {violations}"


def test_scanner_catches_attribute_read() -> None:
    """形式 2：属性读取必须被捕获。"""
    violations = _scan_source("value = row.sentiment_score\n")
    assert any("attribute read" in v for v in violations), f"未能捕获属性读取: {violations}"


def test_scanner_catches_bare_identifier() -> None:
    """形式 3：裸变量引用必须被捕获。"""
    violations = _scan_source("sentiment = compute()\n")
    assert any("bare identifier" in v for v in violations), f"未能捕获裸变量引用: {violations}"


def test_scanner_catches_dict_get() -> None:
    """形式 4：dict.get 字段访问必须被捕获。"""
    violations = _scan_source('value = item.get("sentiment")\n')
    assert any('get("sentiment")' in v for v in violations), f"未能捕获 dict.get: {violations}"


def test_scanner_catches_polars_column_access() -> None:
    """形式 5：Polars 列操作必须被捕获（pl.col 与 df.select 两种调用形态）。"""
    violations = _scan_source('import polars as pl\ncol = pl.col("sentiment_score")\npicked = df.select("sentiment")\n')
    assert sum("field access" in v for v in violations) == 2, f"未能捕获 Polars 列操作: {violations}"


def test_scanner_catches_keyword_argument() -> None:
    """形式 6：关键字传参必须被捕获。"""
    violations = _scan_source("save_market_news(rows, sentiment=item)\n")
    assert any("keyword argument" in v for v in violations), f"未能捕获关键字传参: {violations}"


def test_scanner_catches_dict_literal_key() -> None:
    """形式 7：dict 字面量键必须被捕获（rename 场景）。"""
    violations = _scan_source('renamed = df.rename({"sentiment": "news_sentiment"})\n')
    assert any("dict literal key" in v for v in violations), f"未能捕获 dict 字面量键: {violations}"


def test_scanner_catches_scorer_import() -> None:
    """形式 8：打分服务导入（两种 from 形态 + import 形态）必须被捕获。"""
    violations = _scan_source(
        "from services.news_sentiment_scorer import NewsSentimentScorer\n"
        "from services import news_sentiment_scorer\n"
        "import services.news_sentiment_scorer as scorer\n"
    )
    assert len(violations) == 3, f"应捕获 3 处打分服务导入, 实际: {violations}"


def test_scanner_catches_importlib_indirect_import() -> None:
    """形式 9：importlib 间接导入打分服务必须被捕获。"""
    violations = _scan_source('import importlib\nscorer = importlib.import_module("services.news_sentiment_scorer")\n')
    assert any("importlib" in v for v in violations), f"未能捕获 importlib 间接导入: {violations}"


def test_scanner_catches_relative_import_of_scorer() -> None:
    """缺陷修复 C1 回归：相对 import 形态（module 为空）必须被捕获。"""
    violations = _scan_source("from . import news_sentiment_scorer\n")
    assert any("news_sentiment_scorer" in v for v in violations), f"未能捕获相对导入打分服务: {violations}"


def test_scanner_catches_scorer_import_in_type_checking_else() -> None:
    """缺陷修复 C2 回归：TYPE_CHECKING 的 else 分支是运行时路径，导入打分服务必须被捕获。"""
    violations = _scan_source(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    pass\n"
        "else:\n"
        "    from services.news_sentiment_scorer import NewsSentimentScorer\n"
    )
    assert any("news_sentiment_scorer" in v for v in violations), (
        f"TYPE_CHECKING else 分支内的打分服务导入被误豁免: {violations}"
    )


# ============================================================================
# 鲁棒性测试：既有现场不误报
# ============================================================================


def test_scanner_ignores_uppercase_sentiment_constants() -> None:
    """SENTIMENT_INDICES 情绪指数常量（大写）不误报（精确匹配小写字段名）。"""
    violations = _scan_source(
        "from data.constants import SENTIMENT_INDICES, SENTIMENT_INDEX_NAMES\nindices = SENTIMENT_INDICES\n"
    )
    assert not violations, f"大写常量被误报: {violations}"


def test_scanner_ignores_prose_mentioning_sentiment() -> None:
    """日志/文案中的 "market sentiment" 等长文本不误报（不精确等于字段名）。"""
    violations = _scan_source(
        'logger.info("[%s] Gating: market sentiment insufficient.", code)\n'
        'doc = "news sentiment score for display only"\n'
    )
    assert not violations, f"日志/文案被误报: {violations}"


def test_scanner_respects_type_checking_block() -> None:
    """TYPE_CHECKING 块内的 import 不算违规（仅类型注解用途）。"""
    violations = _scan_source(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from services.news_sentiment_scorer import NewsSentimentScorer\n"
        "\n"
        "def handle(s: 'NewsSentimentScorer') -> None:\n"
        "    pass\n"
    )
    assert not violations, f"TYPE_CHECKING 块内 import 被误报: {violations}"


def test_scanner_ignores_comments() -> None:
    """注释中的伪引用不误报（AST 不解析注释）。"""
    violations = _scan_source('# row["sentiment"] 是展示字段，策略不得读取\nx = 1\n')
    assert not violations, f"注释被误报: {violations}"


def test_scanner_ignores_similar_but_different_fields() -> None:
    """相似但不同的字段名不误报（精确匹配，非前缀/子串匹配）。"""
    violations = _scan_source(
        'value = row["sentiment_class"]\nother = row.sentiment_index\npicked = df.select("sentiment_label")\n'
    )
    assert not violations, f"相似字段名被误报: {violations}"
